# Plan: refactor de sincronización (incidente 2026-10)

- Fecha: 2026-10-28
- Rama Vassago: `refactor/sync-discovery` (desde `main` @ `2f3f0f4`)
- Rama Caldir: `fix/sync-bases-and-scope-refresh` (desde `vtodo-support` @ `02ee000`)
- Registro del incidente: [docs/incidents/2026-10-oauth-cron-sync.md](../incidents/2026-10-oauth-cron-sync.md)
- Decisiones: [ADR-0009](../adr/0009-structural-course-discovery.md),
  [ADR-0008](../adr/0008-caldir-pinned-to-a-commit.md)

## 1. Hallazgos y evidencia

### Estado de los repositorios (Fase 1)

| Elemento | Estado verificado |
| --- | --- |
| Vassago `main` | limpio, `2f3f0f4`, igual a `origin/main` |
| Rama remota `origin/fix/cron-env-and-term-202625` (`7071071`) | **no mergeada**. Contiene el hotfix de cron (`export -p` a `/etc/caldir.env`, línea cron con `. /etc/caldir.env && run-sync.sh`) y una lista `TERM_PREFIXES = ("202615_", "202625_")` en los cinco scripts, más ADR-0008. |
| Hotfix `/run/caldir-cron.env` sugerido durante el incidente | **no existe en Git** en ninguna rama; la rama remota usa otra ruta (`/etc/caldir.env`). |
| Fork Caldir `vtodo-support` | `02ee000` = `origin/vtodo-support`; `constants.rs` ya declara los cuatro scopes. |
| Checkout local de Caldir | sin cambios rastreados; solo documentación no rastreada (`docs/vtodo/**`), que no se tocó. |
| Baphomet | **sin acceso desde esta sesión**. No se sabe qué scripts corren allí, qué imagen está desplegada ni si se aplicó algún hotfix dentro del contenedor. Ver §8. |

### Causas confirmadas en el código

| # | Síntoma | Causa raíz (evidencia) |
| --- | --- | --- |
| A | Cursos `202625_*` ignorados | Glob literal `202615_*` en `merge-ucrawler.py`, `bridge-vtodo.py`, `bridge-windows.py`, `provision-radicale.sh`, `dedupe-vtodo.py`; `scripts/check.sh` paso 4 exigía exactamente un prefijo. |
| B1 | Binario sin `calendar.calendars` | `caldir/Dockerfile` hacía `git clone --branch vtodo-support` dentro de un `RUN` cacheable: el texto del `RUN` no cambia cuando la rama avanza, así que Podman reutiliza la capa con el código viejo. El commit que añade el scope (`02ee000`, 2026-08-26) existía; la imagen no lo contenía. Sin disco, el rebuild fallaba y seguía corriendo la imagen anterior. |
| B2 | Pérdida de `granted_scopes` | `caldir-provider-google/src/session/store.rs::refresh_local` reconstruía `SessionData` desde la respuesta; con `scope` omitido (permitido por RFC 6749 §5.1) `granted_scopes` quedaba vacío. `provision-google.py` en cambio conserva los scopes (solo reescribe token y expiración). |
| C | Cron sin `RADICALE_USER`, logs en UTC | `entrypoint.sh` escribía `/etc/cron.d/caldir` sin entorno; cron arranca los jobs con entorno vacío. `RUN_ON_START` sí heredaba el entorno de Compose. |
| D | 57 restaurados / 57 borrados por ejecución | Las *sync bases* de Caldir eran append-only (`SyncBases::record` solo inserta; nada borra). Tras un borrado remoto, el bridge restaura el archivo con el mismo UID; en fase 2 `CalendarDiff::compute_with` ve "local presente, remoto ausente, base presente" ⇒ borrado remoto ⇒ borra el archivo local. Se repite cada ejecución. `run-sync.sh` no comprobaba el estado final y decía `done`. |
| D-pag | Las tareas "desaparecen" de Google sin que nadie las borre | `caldir-provider-google/src/google_tasks/client.rs::list_tasks` hace un solo `GET tasks.list` sin `maxResults` ni `nextPageToken`: la API devuelve 20 por página, el resto parece borrado en remoto. Con ~77 tareas faltan exactamente 57. Es la causa más probable de que "desaparecieran" en primer lugar. Además, **corregir solo las bases sin paginar crearía 57 duplicados por ejecución**, por eso ambas correcciones van juntas. |
| D' | `merge-ucrawler` "updated" perpetuo | Comparaba `read_text()` (convierte CRLF→LF) contra una salida CRLF: nunca igual. Ya estaba documentado como deuda en `docs/context-handoff.md`. |
| D'' | Riesgo de borrado por export vacío | `merge-ucrawler` borraba del canónico todo `assignment-*.ics` ausente del export, incluso con el directorio de u_crawler vacío o a medio escribir. |

### Sospechas pendientes (requieren producción)

| # | Sospecha | Cómo confirmarla (solo lectura) |
| --- | --- | --- |
| E1 | Calendario de ventanas oculto o no seleccionado en la lista del usuario | `diagnose-google.py` → `calendarList/get` (`hidden`, `selected`); `provision-google.py` ahora lo advierte. |
| E2 | "Pushed: 57 updated" sobre eventos borrados (`status=cancelled`) que la actualización no reactiva | `diagnose-google.py` → `events.list?showDeleted=true`, conteo por `status` y destino de cada `X-GOOGLE-EVENT-ID` local. |
| E3 | Mismo bucle que D en ventanas (restore + borrado en fase 2) | `verify-sync.py` al final de la ejecución; `diagnose-google.py`. |
| E4 | `google_calendar_id` apunta a otro calendario homónimo | `diagnose-google.py` imprime `summary`/`accessRole` del ID configurado. |
| D0 | Cuántas tareas tiene realmente la lista (confirma la causa D-pag) | `diagnose-google.py` (`tasks.list` paginado, con `showDeleted/showHidden/showCompleted`). |

## 2. Arquitectura propuesta

1. **Descubrimiento estructural** (`caldir/courses.py`, ADR-0009). Un curso es
   cualquier directorio de primer nivel con `deadlines/` o `windows/` que
   contenga al menos un `.ics`. Los canónicos se encuentran por el sufijo
   `__<kind>`, así que los períodos antiguos siguen visibles aunque u_crawler
   deje de exportarlos. Nombre validado (`[A-Za-z0-9][A-Za-z0-9._-]*`). Sin
   manifiesto: u_crawler es externo (ADR-0005) y su layout ya es el contrato.
2. **Ausencia ≠ borrado** en `merge-ucrawler.py`: export vacío o ausente no
   borra nada; más de la mitad de un curso en una pasada se retiene salvo
   `MERGE_ALLOW_MASS_DELETE=1`; UID duplicado entre archivos aborta antes de
   escribir.
3. **Caldir olvida la base tras un borrado aplicado** (fork). Una
   recreación posterior es un *create*. Las reglas de autoridad del bridge no
   cambian.
4. **Verificación de convergencia** (`verify-sync.py`), nuevo último paso de
   `run-sync.sh`: cada UID canónico dentro de la ventana de ±365 días debe
   estar en su espejo con `X-GOOGLE-TASK-ID` / `X-GOOGLE-EVENT-ID`; si no,
   rc 3 y sin `done`.
5. **Cron** con `cron-run.sh`: snapshot `0600` de una lista blanca de
   variables, `flock` contra ejecuciones solapadas; `RUN_ON_START` usa el
   mismo camino.
6. **Build reproducible** (ADR-0008): `ARG CALDIR_COMMIT=<sha40>`, fetch de
   ese commit exacto, verificación `HEAD == CALDIR_COMMIT`, comprobación de
   los cuatro scopes en el binario, `BUILD_INFO` y etiquetas OCI.
7. **Diagnóstico** `diagnose-google.py`: solo `GET`.

## 3. Archivos afectados

Vassago: `caldir/{courses.py, verify-sync.py, diagnose-google.py, cron-run.sh}`
(nuevos); `caldir/{merge-ucrawler.py, bridge-vtodo.py, bridge-windows.py,
dedupe-vtodo.py, provision-radicale.sh, provision-google.py, run-sync.sh,
entrypoint.sh, Dockerfile}`; `scripts/check.sh`; `tests/*`; documentación.

Caldir: `caldir-core/src/calendar/state/{sync_bases.rs, event_bases.rs}`,
`caldir-core/src/{calendar.rs, calendar/state.rs, connection.rs}`,
`caldir-provider-google/src/{session/store.rs, commands/connect.rs}`,
`AGENTS.md`, `specs/caldir.md`.

## 4. Estrategia de migración

Ver [docs/migrations.md](../migrations.md). Resumen: publicar la rama de
Caldir, construir con el SHA fijado, desplegar, ejecutar una vez
`diagnose-google.py`, mirar la primera ejecución completa. No hay que borrar
estado: las bases antiguas se van limpiando solas a medida que se aplican
borrados; los archivos LF del canónico se normalizan a CRLF una única vez.

## 5. Riesgos

| Riesgo | Mitigación |
| --- | --- |
| Primera ejecución con el Caldir corregido recrea en Google las tareas restauradas (IDs nuevos) | Es el resultado esperado de la política existente (restaurar asignaciones borradas en Google). Ejecutar `diagnose-google.py` antes y después. |
| Si Google sigue listando un elemento recién borrado, olvidar la base podría re-crearlo localmente | Eventos cancelados ya se ignoran; riesgo residual documentado en `specs/caldir.md`. |
| `verify-sync.py` con falso positivo deja la ejecución en rc 3 cada 15 min | No bloquea datos (es el último paso); la salida lista los UID. Se puede diagnosticar con `diagnose-google.py`. |
| La guarda de borrado masivo retiene una baja legítima en bloque | Aviso explícito en stderr; `MERGE_ALLOW_MASS_DELETE=1` para una ejecución. |
| El SHA fijado no existe en GitHub hasta hacer push del fork | El build falla en `git fetch` (falla cerrado). Push previo obligatorio. |
| Un nombre de curso con caracteres no seguros se ignora | Validación explícita; documentado. |

## 6. Orden de implementación

1. Descubrimiento + guardas de merge + corrección CRLF (Vassago).
2. `verify-sync.py` + paso final en `run-sync.sh`.
3. Scopes en refresh y aviso de visibilidad en `provision-google.py`;
   `diagnose-google.py`.
4. Caldir: olvido de bases, scopes en refresh, test de URL OAuth.
5. Cron (`cron-run.sh`) y Dockerfile fijado.
6. Documentación, ADR, incidente, migración.

## 7. Pruebas de aceptación

`sh scripts/check.sh` verde, que incluye:

| Requisito | Prueba |
| --- | --- |
| 1-3 descubrimiento, nuevos cursos, cursos antiguos | `tests/test_courses.py`, `tests/test_pipeline_cycle.py` |
| 4 segundo ciclo sin cambios | `test_two_terms_publish_and_second_cycle_is_a_no_op`, `test_unchanged_deadline_is_not_rewritten` |
| 5 creadas/editadas/completadas/borradas | `tests/test_bridge_vtodo.py`, `test_completed_in_google_does_not_come_back_pending`, `test_google_deletion_is_restored_and_converges` |
| 6 recuperación tras interrupción | `test_interrupted_run_recovers_without_duplicates` |
| 7 UID e ID remoto | `test_canvas_edit_keeps_uid_and_remote_id`, `test_old_term_survives_when_its_export_disappears` |
| 8 ventanas | `test_windows_are_published_with_event_ids` |
| 9 errores de publicación y rc | `test_append_only_bases_loop_is_reported_not_done`, `test_unpublished_window_fails_verification` |
| 10 entorno de cron y `TZ` | `tests/test_cron_env.py` |
| 11 cuatro scopes en la URL OAuth | Caldir `commands::connect::tests::the_consent_url_requests_the_full_scope_set`; build: `grep -aqF` en el Dockerfile |
| 12 renovación sin pérdida de permisos | Caldir `session::store::tests::*refresh_scope*`; `tests/test_provision_google.py` |

Caldir: `cargo test -p caldir-core -p caldir-provider-google`, `cargo clippy … -D warnings`.

## 8. Lo que requiere Baphomet (no verificado aquí)

- Qué imagen corre (`podman inspect calendar-caldir:vtodo-support`) y si el
  contenedor tiene archivos modificados a mano (`podman diff caldir`).
- Si existe `/run/caldir-cron.env` o `/etc/caldir.env` dentro del contenedor
  (hotfix manual).
- Salida de `diagnose-google.py` (sospechas E1–E4, D0).

Los comandos están en [docs/operations.md](../operations.md) y
[docs/migrations.md](../migrations.md).

## 9. Rollback

- Vassago: volver a desplegar la imagen anterior (se conserva la etiqueta
  previa; ver migración) o `git checkout main` y reconstruir.
- Caldir: el Dockerfile fija un SHA; volver a `02ee000…` reconstruye el
  binario anterior. Las bases que la versión nueva haya olvidado solo
  afectan a elementos ya borrados en ambos lados.
- Ningún paso borra estado, volúmenes ni datos remotos.
