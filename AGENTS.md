# AGENTS.md

Vassago is a self-hosted calendar sync stack run with Compose. An external
`u_crawler` image extracts academic deadlines and delivery windows from the
university LMS and writes `.ics` files into a shared volume; the `caldir`
container merges them into canonical per-course collections, publishes them to
Radicale over CalDAV, and mirrors them to Google Tasks and Google Calendar
through two conflict-aware bridges. Everything is plain files plus stdlib
Python 3 and POSIX `sh`.

## Pipeline

`caldir/run-sync.sh` runs on `CRON_SCHEDULE` inside the `caldir` container.
Each step aborts the run on failure (`set -eu`):

1. `provision-radicale.sh` — create the Radicale collections.
2. `provision-google.py` — refresh the OAuth token, ensure task list and calendar.
3. `merge-ucrawler.py` — merge u_crawler output into the canonical collections.
4. `caldir sync` — phase 1, sync providers.
5. `bridge-vtodo.py` — deadlines ↔ Google Tasks; a non-zero rc (2 on conflicts) ends the run with that rc.
6. `bridge-windows.py` — windows → Google Calendar; same rc handling.
7. `caldir sync` — phase 2, publish bridge results.

A successful run ends with a `[caldir] … done` line.

## Directory layout

Host paths under `caldir/calendars/`, mounted at `/data/calendars` in the
`caldir` container and `/home/appuser/Caldir` in `u-crawler`.

| Path | Written by | Contents |
| --- | --- | --- |
| `202615_<course>/deadlines/` | u_crawler | VTODO source of truth for assignments |
| `202615_<course>/windows/` | u_crawler | VEVENT delivery windows |
| `202615_<course>__deadlines/` | merge-ucrawler + provision-radicale | canonical VTODO collection, CalDAV-backed |
| `202615_<course>__windows/` | merge-ucrawler + provision-radicale | canonical VEVENT collection, CalDAV-backed |
| `unab/` | provision-google + bridge-vtodo | Google Tasks mirror |
| `unab-windows/` | provision-google + bridge-windows | Google Calendar mirror |

## Where to read more

- [docs/architecture.md](docs/architecture.md) — components, data flow, container paths.
- [docs/sync-semantics.md](docs/sync-semantics.md) — per-script merge rules, authority, exit codes, state files.
- [docs/operations.md](docs/operations.md) — build, first run, forcing a sync, logs, term rotation.
- [docs/security.md](docs/security.md) — secret inventory, access model, token handling.
- [docs/troubleshooting.md](docs/troubleshooting.md) — conflicts, provisioning failures, duplicate UIDs.
- [docs/decisions.md](docs/decisions.md) — index of the ADRs under [docs/adr/](docs/adr/).
- [docs/context-handoff.md](docs/context-handoff.md) — what to carry into a new session.

## Hard rules

- ALWAYS run `sh scripts/check.sh` before claiming a change works. See
  [.claude/skills/validate-vassago/SKILL.md](.claude/skills/validate-vassago/SKILL.md).
- ALWAYS keep the `202615_` term prefix byte-identical across the five scripts
  that hardcode it: `provision-radicale.sh`, `merge-ucrawler.py`,
  `bridge-vtodo.py`, `bridge-windows.py`, `dedupe-vtodo.py`. `scripts/check.sh`
  enforces this.
- ALWAYS keep the step order in `caldir/run-sync.sh` as listed above; the two
  `caldir sync` phases bracket the bridges by design.
- ALWAYS stay on stdlib Python 3 and POSIX `sh`. Adding a dependency means
  changing `caldir/Dockerfile`, which is out of bounds without an ADR.
- ALWAYS treat `caldir/dedupe-vtodo.py` as a manual repair tool: it is not
  COPY'd into the image and not part of the pipeline.
- NEVER commit, print, or read back the gitignored secret and runtime paths:
  `.env`, `radicale/config/users`, `radicale/data/`, `caldir/config/`,
  `caldir/state/`, `caldir/calendars/`, `u-crawler/config/`, and any
  `quarantine-*/` directory.
- NEVER vendor, patch, or reimplement u_crawler. It is an external image
  (`ghcr.io/belcaik/unab-sync-content`), consumed only through the shared volume.
- NEVER change `compose.yaml` schedules, ports, network, volume flags, or
  service names without a new ADR. The `:Z`/`:z`/`:U` flags and the
  `localhost/` image prefix are load-bearing for the runtime host.
- NEVER change the bridge authority rules (which side wins, when a conflict is
  raised) without a new ADR. Deletion of a canonical assignment is never a
  bridge outcome.

## Skills

Three project skills live under `.claude/skills/`:
[validate-vassago](.claude/skills/validate-vassago/SKILL.md),
[rebuild-caldir-image](.claude/skills/rebuild-caldir-image/SKILL.md),
[resolve-bridge-conflict](.claude/skills/resolve-bridge-conflict/SKILL.md).
