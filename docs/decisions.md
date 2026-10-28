# Decisions

Architecture decision records for this stack. All are retroactive: they document
choices already implemented in the tracked code, and every claim in them cites
`path:line` evidence.

## ADRs

- [ADR-0001: Filesystem ICS as the integration bus](adr/0001-filesystem-ics-as-integration-bus.md) — `u_crawler` and `caldir` exchange data only through a bind-mounted directory of `.ics` files; no API, no queue, and `merge-ucrawler.py` is the sole reader.
- [ADR-0002: Radicale as the CalDAV hub](adr/0002-radicale-caldav-hub.md) — one htpasswd/`owner_only` Radicale instance hosts one collection per course × kind, provisioned by `MKCOL` with a `VEVENT`+`VTODO` component set and reached by `caldir`'s CalDAV provider with `caldav_supports_tasks = true`.
- [ADR-0003: Two-phase sync with fail-closed bridges](adr/0003-two-phase-sync-fail-closed-bridges.md) — phase 1 pulls providers, the bridges reconcile locally, phase 2 publishes; any bridge conflict (exit 2) aborts before phase 2, so one conflict blocks publication for everything.
- [ADR-0004: Split authority — deadlines negotiate, windows do not](adr/0004-authority-rules-deadlines-vs-windows.md) — deadlines keep user task state and share fields bidirectionally with Google (a Google hard-delete re-seeds rather than deletes); delivery windows are LMS-authoritative and Google edits are overwritten.
- [ADR-0005: u_crawler stays an external, pinned image](adr/0005-u-crawler-external-image.md) — consumed as `ghcr.io/belcaik/unab-sync-content:0.1.0-beta.2`, developed elsewhere, never vendored; its only contract is the directory/UID/filename convention plus the shared volume.
- [ADR-0006: caldir is built here from the `vtodo-support` branch](adr/0006-caldir-built-from-vtodo-support-branch.md) — built by `caldir/Dockerfile` from an unpinned fork branch, tagged `localhost/calendar-caldir:vtodo-support`, never published, with no `build:` key in Compose. The branch reference is superseded by ADR-0008.
- [ADR-0007: Runtime state and secrets live in gitignored bind mounts](adr/0007-runtime-state-and-secrets-outside-git.md) — every credential and runtime path is a gitignored host directory beside `compose.yaml`, provisioning is idempotent, and `.env` carries only the Radicale credentials.
- [ADR-0008: Several academic terms can be processed at once](adr/0008-overlapping-academic-terms.md) — *superseded by ADR-0009* — the five scripts share a `TERM_PREFIXES` list instead of one literal, so a semester change keeps the old term live until its Google mirrors are retired.
- [ADR-0009: Courses are discovered from u_crawler's layout, and a run must converge](adr/0009-structural-course-discovery.md) — structural discovery replaces term-prefix lockstep, old terms remain visible, guarded merges prevent absence from becoming deletion, and `verify-sync.py` runs last to require Google publication.
- [ADR-0010: caldir is built from a pinned commit](adr/0010-caldir-pinned-to-a-commit.md) — `ARG CALDIR_COMMIT` is a full SHA verified after fetch; the build fails without the four Google OAuth scopes and records `BUILD_INFO` plus OCI labels.

## Small decisions

Visible in the code, consistent across it, and not worth an ADR of their own:

- **Existing code comments are in Spanish**; identifiers, log messages and docs are in English. Either language is acceptable in new comments (`caldir/merge-ucrawler.py:11-12`, `caldir/Dockerfile:81`, `compose.yaml:56,59,65`).
- **Stdlib-only Python and POSIX `sh`.** Every script is `#!/usr/bin/env python3` importing only the standard library (`json`, `os`, `re`, `hashlib`, `tomllib`, `urllib`, `pathlib`, `shutil`) or `#!/bin/sh` with `set -eu`. The runtime image installs no Python package (`caldir/Dockerfile:72-79`).
- **Atomic writes everywhere.** Content goes to a temp sibling and is moved into place with `os.replace`, never written in place (`caldir/merge-ucrawler.py:121-132`, `caldir/bridge-vtodo.py:150-161,453-471`, `caldir/bridge-windows.py:129-142`, `caldir/provision-google.py:230-240`).
- **`dedupe-vtodo.py` is a manual tool.** It is not `COPY`'d into the image and not called by `run-sync.sh`; it is run by hand inside the container with its `courses.py` dependency copied alongside it (its paths are hardcoded to `/data/...`) when duplicates appear.
- **`X-GOOGLE-*` properties are Google-private.** They are excluded from the semantic hashes so Google's own bookkeeping never reads as a user change, and preserved on the Google side when a file is rewritten (`caldir/bridge-vtodo.py:65,164-179,342-347`, `caldir/bridge-windows.py:44,145-160,274-296`).
- **Cron output is redirected to PID 1**, so scheduled runs land in the container log stream rather than in a file nobody reads (`caldir/entrypoint.sh:62`).
- **Cron jobs get the container environment from a 0600 snapshot.** `entrypoint.sh` writes an allowlisted `export -p` snapshot to `/run/caldir/cron.env`; `cron-run.sh` checks its owner and mode, sources it and runs the pipeline under `flock -n`, for both cron and `RUN_ON_START` runs (`caldir/entrypoint.sh:15-46`, `caldir/cron-run.sh`).
