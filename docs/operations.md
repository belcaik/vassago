# Operations

Day-to-day tasks for running the `calendar` compose stack. For architecture
and sync semantics, see the other files under `docs/`.

## Build the caldir image

`compose.yaml` references `caldir` as `image: localhost/calendar-caldir:vtodo-support`
with no `build:` key, so `docker compose up` / `podman compose up` will not
build it — the image must be built manually first, from `caldir/`, with
exactly that tag:

```sh
docker build -t localhost/calendar-caldir:vtodo-support caldir/
```

```sh
podman build -t localhost/calendar-caldir:vtodo-support caldir/
```

The build stage clones `https://github.com/belcaik/caldir.git` at
`--branch vtodo-support` with `--depth 1` (`caldir/Dockerfile:10-14`). That
branch is not pinned to a commit, so rebuilding at a different time can pull
different upstream code even with an unchanged `Dockerfile`.

## First run

On container start, `entrypoint.sh` writes the cron schedule from
`CRON_SCHEDULE` into `/etc/cron.d/caldir` (running `run-sync.sh` as root,
output redirected to the container's stdout/stderr via `/proc/1/fd/1` and
`/proc/1/fd/2`), then checks `RUN_ON_START`. In `compose.yaml` this is set to
`"true"`, so on every container start `run-sync.sh` runs once immediately
(errors from this initial run are swallowed with `|| true` — the container
still proceeds to `exec cron -f`).

Because cron starts every job with an empty environment, `entrypoint.sh` also
writes the container environment to the root-only `/etc/caldir.env`, and the
cron line sources it before `run-sync.sh`. That is how scheduled runs receive
`RADICALE_*` and `TZ`. Expect the first run to take longer than
later ones, since `provision-radicale.sh` and `provision-google.py` create
collections, a task list, and a calendar that do not yet exist.

## Force a sync

To trigger a sync outside the cron schedule, run `run-sync.sh` inside the
running container:

```sh
docker compose exec caldir /usr/local/bin/run-sync.sh
```

```sh
podman compose exec caldir /usr/local/bin/run-sync.sh
```

This runs as root inside the container, the same as the cron-triggered
invocation. Outside of manual runs, cron re-triggers `run-sync.sh`
automatically every `CRON_SCHEDULE` (`*/15 * * * *` in `compose.yaml` — every
15 minutes).

## Read the logs

```sh
docker compose logs -f caldir
```

Every line is prefixed by the stage that produced it:

| Prefix | Emitted by |
| --- | --- |
| `[caldir]` | `entrypoint.sh` and `run-sync.sh` (schedule setup, stage transitions) |
| `[provision]` | `provision-radicale.sh` (Radicale collection creation/migration) |
| `[google-provision]` | `provision-google.py` (token refresh, task list / calendar setup) |
| `[u_crawler-merge]` | `merge-ucrawler.py` |
| `[bridge]` | `bridge-vtodo.py` |
| `[windows-bridge]` | `bridge-windows.py` |

A healthy run shows this order: `[caldir]` provisioning messages, then
`[provision]`/`[google-provision]`, then `[u_crawler-merge]`, then `[caldir]
phase 1: sync providers`, then `[bridge]` and `[windows-bridge]`, then
`[caldir] phase 2: publish bridge results`, ending in `[caldir] ... done`
(`caldir/run-sync.sh`). The pipeline runs under `set -eu` and stops at the
first failing step — a run that stops before `phase 2` did not publish
whatever the bridges changed. See `docs/troubleshooting.md#pipeline-never-reaches-phase-2`.

## Google OAuth prerequisites

`provision-google.py` reads two files under
`/data/.config/caldir/providers/google/` inside the container (host path
`./caldir/config/providers/google/`, gitignored):

- `app_config.toml` — keys `client_id`, `client_secret` (OAuth client
  credentials).
- `session/<GOOGLE_ACCOUNT>.toml` — keys `access_token`, `refresh_token`,
  `expires_at`, `auth_mode`, `granted_scopes`. `auth_mode` must be `"local"`;
  any other value is a fatal error (`caldir/provision-google.py:247-250`).

`granted_scopes` must be a superset of:

- `https://www.googleapis.com/auth/tasks`
- `https://www.googleapis.com/auth/calendar.events`
- `https://www.googleapis.com/auth/calendar.calendarlist.readonly`
- `https://www.googleapis.com/auth/calendar.calendars`

`provision-google.py` refreshes the access token itself when it is within 5
minutes of `expires_at`, rewriting only `access_token` and `expires_at` in
the session file — it never performs the initial OAuth authorization. That
first `session/<account>.toml` must be produced by the `caldir`/
`caldir-provider-google` binary's own OAuth flow, which is outside this
repository; consult the `belcaik/caldir` project for how to run it.

## Radicale users

`radicale/config/config` sets `[auth] type = htpasswd` and
`htpasswd_encryption = autodetect`, reading
`radicale/config/users` (gitignored, not created by any script in this
repo). Create or update it with the standard `htpasswd` tool, e.g.:

```sh
htpasswd -c radicale/config/users <user>
```

(omit `-c` when adding to an existing file). `[rights] type = owner_only`
means each user can only see and modify collections under their own
username. Whichever user you add must match `RADICALE_USER` in `.env` —
`caldir` authenticates to Radicale with `RADICALE_USER`/`RADICALE_PASSWORD`,
and `provision-radicale.sh` creates collections under
`<RADICALE_URL>/<RADICALE_USER>/...`.

## Term rotation

The live academic terms are the `TERM_PREFIXES` list declared, identically, in
five scripts: `provision-radicale.sh`, `merge-ucrawler.py`, `dedupe-vtodo.py`,
`bridge-vtodo.py`, `bridge-windows.py`. `scripts/check.sh` enforces that all
five list the same set. Rotation takes two separate changes
([ADR-0008](adr/0008-overlapping-academic-terms.md)):

1. **Add the new term** as soon as u_crawler writes its course directories
   under `./caldir/calendars`. On the next `run-sync.sh`,
   `provision-radicale.sh` and `merge-ucrawler.py` create the Radicale
   collections and canonical directories automatically.
2. **Drop the old term** only once its items are no longer wanted in Google.
   Removing a prefix makes every old-term UID look "deleted canonically": the
   bridges delete untouched mirrors from Google and raise a conflict (blocking
   phase 2 for everything) for any item you edited or completed there. Before
   dropping it, delete the old term's files from `unab/` and `unab-windows/`
   and their entries from both bridge state files.

Both changes need the caldir image rebuilt, because the scripts are copied into
it (see [Build the caldir image](#build-the-caldir-image)).
