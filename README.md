# Vassago

Self-hosted calendar integration stack. `u_crawler` extracts academic
deadlines and delivery windows from a university LMS and writes ICS files
into a shared volume; `caldir` merges them into canonical per-course
calendars, publishes them to a self-hosted Radicale (CalDAV) server, and
mirrors them to Google Tasks / Google Calendar with conflict-aware bridges.
`u_crawler` is developed as a separate project and consumed here only as a
prebuilt image.

## Components

| Service | Image | Role | Schedule |
| --- | --- | --- | --- |
| `radicale` | `ghcr.io/kozea/radicale:stable` | CalDAV server; canonical storage for synced collections; published on the host address set in `compose.yaml` | always on |
| `caldir` | `localhost/calendar-caldir:vtodo-support` (built locally, see Quick start) | Merges u_crawler output, provisions Radicale collections and Google resources, syncs, and reconciles conflicts via two bridges | `RUN_ON_START=true` at container start, then every `CRON_SCHEDULE` (`*/15 * * * *`) |
| `u-crawler` | `ghcr.io/belcaik/unab-sync-content:0.1.0-beta.1` | Crawls the LMS and writes ICS deadlines/windows into the shared `caldir/calendars` volume | `RUN_ON_START=true` at container start, then every `CRON_SCHEDULE` (`0 5 * * *`) |

## Data flow

```
u_crawler ──ICS──▶ caldir/calendars/202615_<course>/{deadlines,windows}/*.ics
                        │
                        ▼
              merge-ucrawler.py (preserves user STATUS/COMPLETED)
                        │
                        ▼
         202615_<course>__{deadlines,windows}/  (canonical, caldir-managed)
                 │                      │
                 ▼                      ▼
         caldir sync (phase 1)    caldir sync (phase 1)
                 │                      │
                 ▼                      ▼
             Radicale (CalDAV)     bridge-vtodo.py / bridge-windows.py
                                         │
                                         ▼
                              Google Tasks ("UNAB") /
                              Google Calendar ("UNAB - Ventanas de entrega")
                                         │
                                         ▼
                              caldir sync (phase 2 — publishes bridge results)
```

Full mechanics — merge rules, bridge state, conflict conditions, exit codes —
are in `docs/sync-semantics.md`.

## Quick start

1. Copy the environment template: `cp .env.example .env`, then set
   `RADICALE_USER`/`RADICALE_PASSWORD` to real values.
2. Create `radicale/config/users` with `htpasswd` for that same user (the
   file is gitignored — see `docs/operations.md#radicale-users`):
   `htpasswd -c radicale/config/users <user>`.
3. Build the `caldir` image from `caldir/`, using the exact tag
   `compose.yaml` expects — `compose.yaml` has no `build:` key for this
   service, so this step cannot be skipped:
   `docker build -t localhost/calendar-caldir:vtodo-support caldir/`
   (or `podman build ...`).
4. Place Google OAuth app credentials and a local-mode OAuth session under
   `caldir/config/providers/google/` (`app_config.toml`, then
   `session/<account>.toml`), with all four required scopes: `tasks`,
   `calendar.events`, `calendar.calendarlist.readonly`,
   `calendar.calendars`. See
   `docs/operations.md#google-oauth-prerequisites`.
5. Start the stack: `docker compose up -d` (or `podman compose up -d`).
6. Validate: `sh scripts/check.sh`.

## Repository layout

Tracked:

- `compose.yaml`, `.env.example`, `LICENSE`
- `radicale/config/` — Radicale server config (`config`); `users` is
  gitignored runtime state
- `caldir/` — Dockerfile and all pipeline scripts (`entrypoint.sh`,
  `run-sync.sh`, `provision-radicale.sh`, `provision-google.py`,
  `merge-ucrawler.py`, `bridge-vtodo.py`, `bridge-windows.py`,
  `dedupe-vtodo.py`); `caldir/config/`, `caldir/state/`, `caldir/calendars/`
  are gitignored runtime state
- `u-crawler/` — compose-referenced volume mount points only;
  `u-crawler/config/` is gitignored runtime state
- `docs/`, `docs/adr/` — documentation and architecture decision records
- `scripts/`, `tests/`, `.github/workflows/`, `.claude/skills/` — validation
  tooling and agent-facing skills

Gitignored runtime state (never commit): `.env`, `radicale/data/`,
`radicale/config/users`, `caldir/config/`, `caldir/state/`,
`caldir/calendars/`, `u-crawler/config/`, `**/quarantine-*/`. Full rationale
in `docs/security.md#what-is-gitignored`.

## Documentation

- `docs/architecture.md` — components, data flow, directory layout,
  container paths, conventions
- `docs/sync-semantics.md` — merge and bridge logic, exit codes, state files
- `docs/operations.md` — build, run, force-sync, logs, OAuth setup,
  Radicale users, term rotation
- `docs/security.md` — secret inventory, access model, network exposure,
  token handling, gitignored paths
- `docs/troubleshooting.md` — bridge conflicts, provisioning failures,
  duplicate UIDs, stalled pipeline
- `docs/decisions.md` — index of architecture decision records
  (`docs/adr/`)
- `docs/context-handoff.md` — handoff notes for picking up this repo cold

## For coding agents

See `AGENTS.md`.

## License

GPL-3.0-or-later (see `LICENSE`). Licenses of the separately developed
`caldir` and `u_crawler` projects are their own.
