# Architecture

Vassago is a self-hosted calendar integration stack. `u_crawler` extracts
academic deadlines and delivery windows from a university LMS (UNAB / Canvas)
and writes ICS files into a shared volume; the `caldir` container merges them
into per-course canonical calendars, publishes them to Radicale over CalDAV,
and mirrors them to Google Tasks and Google Calendar through conflict-aware
bridges.

The integration bus is the filesystem: every component reads and writes plain
`.ics` files under one shared directory tree. Nothing talks to anything else
directly except through Radicale's HTTP API and Google's REST APIs.

## Components

Three services, defined in `compose.yaml` (project name `calendar`, network
`calendar`).

| Service | Image | Role | Schedule | Volumes (host -> container) |
| --- | --- | --- | --- | --- |
| `radicale` | `ghcr.io/kozea/radicale:stable` | CalDAV hub; the endpoint CalDAV clients (Thunderbird, phones) subscribe to. Port 5232 is bound to the host address configured in `compose.yaml`; inside the container Radicale listens on `0.0.0.0:5232` (`radicale/config/config:2`). | always on (`restart: unless-stopped`) | `./radicale/config` -> `/etc/radicale` (`ro,Z`), `./radicale/data` -> `/var/lib/radicale` (`Z,U`) |
| `caldir` | `localhost/calendar-caldir:vtodo-support` | Runs the whole sync pipeline: provisioning, merge, `caldir sync`, the two bridges, and final convergence verification. Built from `caldir/Dockerfile`. | cron `*/15 * * * *`, plus one run at container start (`RUN_ON_START=true`) | `./caldir/config` -> `/data/.config/caldir` (`Z`), `./caldir/state` -> `/data/.local/share/caldir` (`Z`), `./caldir/calendars` -> `/data/calendars` (`z`) |
| `u-crawler` | `ghcr.io/belcaik/unab-sync-content:0.1.0-beta.2` | External LMS scraper. Writes source ICS files into the shared calendars volume. | cron `0 5 * * *`, plus one run at container start | `./u-crawler/config` -> `/home/appuser/.config/u_crawler` (`Z`), `./caldir/calendars` -> `/home/appuser/Caldir` (`z`) |

`caldir` declares `depends_on: radicale`. `compose.yaml` has no `build:` key for
`caldir`, so the image must be built by hand with exactly the tag
`localhost/calendar-caldir:vtodo-support` (`compose.yaml:21`).

`./caldir/calendars` is mounted into both `caldir` and `u-crawler`
(`compose.yaml:38`, `compose.yaml:66`) — that shared mount is the bus.

`.env` supplies `RADICALE_USER` and `RADICALE_PASSWORD`; the `caldir` service
passes them through as environment (`compose.yaml:32-33`).

The `:Z` / `:U` / `:z` volume flags and the `localhost/` image prefix are
Podman/SELinux idioms. That the production runtime is Podman rather than Docker
is an inference from those flags, not a documented fact.

### caldir the binary

`caldir` is an external Rust program, not part of this repository. The
Dockerfile builds it from `https://github.com/belcaik/caldir.git` at the exact
commit in `ARG CALDIR_COMMIT`, verified after fetch, so image builds are
reproducible ([ADR-0010](adr/0010-caldir-pinned-to-a-commit.md)). The build
produces `caldir` plus provider binaries `caldir-provider-google`, `-outlook`,
`-icloud`, `-caldav`, `-webcal`, fails if the Google provider lacks one of its
four OAuth scopes, and records `/usr/local/share/caldir/BUILD_INFO`.

The scripts in this repository interact with `caldir` in exactly two ways: they
run `caldir sync` from `/data/calendars`, and they write `.caldir/config.toml`
files that tell it which remote each directory maps to. Its internals are out of
scope here.

## Data flow

```
                      LMS (UNAB / Canvas)
                              |
                              v
                        [ u-crawler ]                  cron 0 5 * * *
                              |
                writes <course>/deadlines/*.ics   (VTODO)
                       <course>/windows/*.ics     (VEVENT)
                              |
            ./caldir/calendars  (volume shared by both containers)
                              |
                              v
                     [ merge-ucrawler.py ]              cron */15 * * * *
                              |
       canonical: <course>__deadlines/  <course>__windows/
                              |
             +----------------+-----------------------+
             |                                        |
   caldir sync (phase 1)                     bridge-vtodo.py
   provider = "caldav"                       bridge-windows.py
             |                                        |
             v                                        v
       [ radicale ]                       unab/          unab-windows/
       CalDAV clients                     (Google Tasks) (Google Calendar)
                                                    |
                                          caldir sync (phase 2)
                                          provider = "google"
                                                    |
                                                    v
                                          Google Tasks / Google Calendar
                                                    |
                                          verify-sync.py (convergence)
```

### The pipeline

`caldir/run-sync.sh` runs under `set -eu`; each step aborts the run on failure.

1. `provision-radicale.sh` — creates the Radicale collections and their
   `.caldir/config.toml` (`caldir/run-sync.sh:14`).
2. `provision-google.py` — refreshes the OAuth token, ensures the Google task
   list and calendar exist, writes their `.caldir/config.toml`
   (`caldir/run-sync.sh:17`).
3. `merge-ucrawler.py` — folds u_crawler source files into the canonical
   directories (`caldir/run-sync.sh:20`).
4. **Phase 1: sync providers** — `cd /data/calendars && caldir sync`
   (`caldir/run-sync.sh:22-24`). This is the exchange with the remotes:
   canonical directories are reconciled with Radicale, and the `unab/` and
   `unab-windows/` directories are refreshed from Google. After this step the
   local Google mirror directories reflect whatever the user did in Google since
   the last run — which is precisely what the bridges need in order to detect
   Google-side edits.
5. `bridge-vtodo.py` — reconciles canonical deadlines against the Google Tasks
   mirror. A non-zero return code (2 on conflicts) ends the run with that code
   (`caldir/run-sync.sh:26-35`).
6. `bridge-windows.py` — same for delivery windows (`caldir/run-sync.sh:37-46`).
7. **Phase 2: publish bridge results** — `cd /data/calendars && caldir sync`
   again (`caldir/run-sync.sh:48-50`). The bridges only edited local files; this
   second sync is what pushes those edits out to Radicale and Google.
8. **Verify convergence** — `verify-sync.py` checks every managed canonical UID
   within caldir's default ±365-day window. Each must have a Google mirror file
   carrying `X-GOOGLE-TASK-ID` or `X-GOOGLE-EVENT-ID`; otherwise verification
   exits 3 (`caldir/run-sync.sh:52-55`).

The two phases exist because the bridges sit between them: phase 1 fetches, the
bridges decide, phase 2 publishes. A conflict in either bridge stops the run
before phase 2, so nothing is published that run. Verification runs last, and
`[caldir] … done` is printed only after it passes. See
[Exit codes](sync-semantics.md#exit-codes).

cron starts jobs with an empty environment, so `entrypoint.sh` first snapshots
an allowlist of container variables (`TZ`, `HOME`, `PATH`, locale, `RADICALE_*`,
`GOOGLE_*`, `CALENDAR_ROOT`, `CALDIR_*`, `BRIDGE_*`, `WINDOWS_BRIDGE_*`,
`CRON_SCHEDULE`, `RUN_ON_START`) into `/run/caldir/cron.env` (dir `0700`, file
`0600`, written atomically with `export -p`). It then writes
`/etc/cron.d/caldir` with `${CRON_SCHEDULE}` invoking
`/usr/local/bin/cron-run.sh` as root, with output redirected to `/proc/1/fd/1`
and `/proc/1/fd/2` so it lands in the container log; runs `cron-run.sh || true`
once when `RUN_ON_START` is `true`; then `exec cron -f`. The `|| true` on the
startup run means a failing first run does not prevent cron from starting.

`cron-run.sh` refuses a snapshot that is missing, a symlink, not owned by the
invoking user (root) or not mode `0600`; otherwise it sources it, takes
`flock -n /run/caldir/sync.lock` (a run that finds the lock held logs
`previous sync still running; skipping this run` and exits 0) and execs
`run-sync.sh`. The startup run and the cron runs take the same path, so they
never overlap.

## Directory layout

Everything lives under `/data/calendars` in the `caldir` container
(`./caldir/calendars` on the host, `/home/appuser/Caldir` in `u-crawler`).

| Path pattern | Kind | Created by | `.caldir/config.toml` |
| --- | --- | --- | --- |
| `<course>/deadlines/*.ics` | source (VTODO, one per assignment) | `u-crawler` | none — not a caldir collection |
| `<course>/windows/*.ics` | source (VEVENT, delivery windows) | `u-crawler` | none — not a caldir collection |
| `<course>__deadlines/` | canonical | directory by `merge-ucrawler.py` and `provision-radicale.sh`; config by `provision-radicale.sh` | caldav, see below |
| `<course>__windows/` | canonical | directory by `merge-ucrawler.py` and `provision-radicale.sh`; config by `provision-radicale.sh` | caldav, see below |
| `unab/` | mirror (Google Tasks) | `provision-google.py` | google tasklist, see below |
| `unab-windows/` | mirror (Google Calendar) | `provision-google.py` | google calendar, see below |

`caldir/courses.py` discovers source courses by this structure, not by an
academic term prefix. Only a non-empty export causes its canonical directory
and Radicale collection to be created. Existing canonical collections are still
scanned when their source is empty or gone, which keeps old terms visible to the
bridges until an operator deliberately removes them.

The `unab/` and `unab-windows/` slugs are the defaults of
`GOOGLE_UNAB_TASKLIST_SLUG` and `GOOGLE_UNAB_WINDOWS_SLUG`.

Canonical collections, written by `caldir/provision-radicale.sh:60-69` only when
the file does not already exist:

```toml
name = "$flat_name"
read_only = false

[remote]
provider = "caldav"
caldav_account = "${RADICALE_USER}@radicale"
caldav_calendar_url = "${collection_url}"
caldav_supports_tasks = true
```

where `flat_name` is `<course>__<kind>` and `collection_url` is
`${RADICALE_URL}/${RADICALE_USER}/${flat_name}/`. If the file exists but has no
`caldav_supports_tasks` key, that one line is appended
(`caldir/provision-radicale.sh:70-76`) — a migration for configs written before
VTODO support.

`unab/.caldir/config.toml`, written by `caldir/provision-google.py:598-606`
(rewritten whenever the existing content differs from what the script would
produce):

```toml
name = "{TASKLIST_NAME}"
read_only = false

[remote]
provider = "google"
google_account = "{ACCOUNT}"
google_resource_id = "{resource_id}"
google_resource_kind = "tasklist"
```

`unab-windows/.caldir/config.toml`, written by
`caldir/provision-google.py:650-657`:

```toml
name = "UNAB - Ventanas de entrega"
read_only = false

[remote]
provider = "google"
google_account = "{ACCOUNT}"
google_calendar_id = "{calendar_id}"
```

`ACCOUNT` is the account configured via `GOOGLE_ACCOUNT`. Note the asymmetry:
the `unab-windows` `name` is a hardcoded literal, while the `unab` `name`
follows `GOOGLE_UNAB_TASKLIST_NAME`.

## Container paths

Inside the `caldir` container:

| Container path | Host counterpart | Contents |
| --- | --- | --- |
| `/data` | — | `HOME` and `WORKDIR` (`caldir/Dockerfile:121-122`, also set in `compose.yaml:29`) |
| `/data/calendars` | `./caldir/calendars` | the calendar tree above; also mounted at `/home/appuser/Caldir` in `u-crawler` |
| `/data/.config/caldir` | `./caldir/config` | caldir configuration root |
| `/data/.config/caldir/providers/google/app_config.toml` | under `./caldir/config` | Google OAuth `client_id` / `client_secret` (`caldir/provision-google.py:20-28`) |
| `/data/.config/caldir/providers/google/session/<GOOGLE_ACCOUNT>.toml` | under `./caldir/config` | OAuth session: `access_token`, `refresh_token`, `expires_at`, `auth_mode`, `granted_scopes` (`caldir/provision-google.py:35-39`) |
| `/data/.local/share/caldir` | `./caldir/state` | runtime state root |
| `/data/.local/share/caldir/vtodo-bridge-state.json` | under `./caldir/state` | VTODO bridge state (`caldir/bridge-vtodo.py:30-35`) |
| `/data/.local/share/caldir/windows-bridge-state.json` | under `./caldir/state` | windows bridge state (`caldir/bridge-windows.py:29-34`) |
| `/data/.local/share/caldir/quarantine-vtodo/` | under `./caldir/state` | duplicates moved aside by `dedupe-vtodo.py` (`caldir/dedupe-vtodo.py:12`) |
| `/etc/cron.d/caldir` | none — written at container start | the cron entry generated by `entrypoint.sh` |
| `/run/caldir/cron.env` | none — written at container start | `0600` snapshot of the allowlisted environment, sourced by `cron-run.sh`; holds `RADICALE_PASSWORD` |
| `/run/caldir/sync.lock` | none — created by the first run | `flock` lock serialising pipeline runs |
| `/usr/local/bin/` | none — baked into the image | `caldir` and the provider binaries, plus the shell and Python scripts |

Inside the `radicale` container:

| Container path | Host counterpart | Contents |
| --- | --- | --- |
| `/etc/radicale` | `./radicale/config` (read-only) | `config` |
| `/etc/radicale/users` | `./radicale/config/users` | htpasswd file; gitignored, created by hand — no script provisions it |
| `/var/lib/radicale/collections` | under `./radicale/data` | Radicale's collection storage (`radicale/config/config:10`) |

Auth is `htpasswd` with `autodetect` encryption and rights are `owner_only`
(`radicale/config/config:4-13`), so each user reaches only their own
collections.

## Conventions

**File naming.** Source files are named `assignment-<id>.ics`, and a new
canonical file starts under that name. caldir renames a canonical file to its
own slug the first time it writes it, so no script may find a canonical file by
name: `merge-ucrawler.py` matches and deletes by managed UID
(`caldir/merge-ucrawler.py`, `files_by_uid`). `dedupe-vtodo.py` still
reconstructs `assignment-<id>.ics` from the UID (`caldir/dedupe-vtodo.py:63`)
and predates the renaming. Any `.ics` with an unmanaged UID dropped into a
canonical directory is left alone.

**Managed UIDs.** VTODO items are managed when the UID starts with
`u_crawler-todo-` (`caldir/bridge-vtodo.py:37`); the full form is
`u_crawler-todo-<id>@u-crawler.local` (`caldir/dedupe-vtodo.py:14-17`). VEVENT
windows are managed when the UID starts with `u_crawler-window-`
(`caldir/bridge-windows.py:36`). Items carrying any other UID — a personal
Google task, for instance — are skipped by both bridges and never modified.

**Course discovery.** `caldir/courses.py` treats a top-level directory with a
non-empty `deadlines/` or `windows/` export as a source course. No academic
term code is hardcoded; a directory such as `202625_<course>` is only an example.
Canonical `<course>__deadlines/` and `<course>__windows/` collections remain
visible to the bridges even after their source export disappears. This replaced the `TERM_PREFIXES` list of
[ADR-0008](adr/0008-overlapping-academic-terms.md); see
[ADR-0009](adr/0009-structural-course-discovery.md).

**Log prefixes.** Every script tags its output, which makes the pipeline
greppable in the container log:

| Prefix | Source |
| --- | --- |
| `[caldir]` | `entrypoint.sh`, `run-sync.sh` |
| `[provision]` | `provision-radicale.sh` |
| `[google-provision]` | `provision-google.py` |
| `[u_crawler-merge]` | `merge-ucrawler.py` |
| `[bridge]` | `bridge-vtodo.py` |
| `[windows-bridge]` | `bridge-windows.py` |
| `[verify]` | `verify-sync.py` |
| `[diagnose]` | `diagnose-google.py` |
| `[dedupe]` | `dedupe-vtodo.py` |

**Atomic writes.** Every ICS and state write goes to a temp file beside the
target and then `os.replace()`, so an interrupted run never leaves a
half-written calendar file: `caldir/merge-ucrawler.py:121-132` (`.ics.tmp`),
`caldir/bridge-vtodo.py:150-161` and `caldir/bridge-windows.py:129-142`
(`.ics.bridge.tmp`), the state files via `.tmp`
(`caldir/bridge-vtodo.py:453-471`), and the OAuth session file with an added
`chmod 0600` before the replace (`caldir/provision-google.py:230-240`).

**ICS handling.** All four Python scripts work on unfolded logical lines
(`unfold()`), locate the component by scanning for `BEGIN:` / `END:`, and index
properties by name — no ICS library. Output is re-joined with `\r\n` and written
with `newline=""` so Python does not translate the line endings.

**Language.** Documentation and log messages are English; code comments are
Spanish. That mix is deliberate.

**Dependencies.** The Python scripts are stdlib-only (`json`, `os`, `re`,
`hashlib`, `tomllib`, `urllib`, `pathlib`, `shutil`); the shell scripts are
POSIX `sh` under `set -eu`. The runtime image installs only `ca-certificates`,
`cron`, `curl`, `python3`, `tzdata` (`caldir/Dockerfile:72-79`). Nothing in the
runtime needs pip, npm, or cargo.
