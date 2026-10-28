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

The build stage fetches exactly `ARG CALDIR_COMMIT` (a full SHA) from
`ARG CALDIR_REPO` (`https://github.com/belcaik/caldir.git`), fails unless
`HEAD` equals it, and fails unless `caldir-provider-google` carries all four
OAuth scopes. To pick up a fork change, bump the `CALDIR_COMMIT` default in
`caldir/Dockerfile` (or pass `--build-arg CALDIR_COMMIT=<sha>`); an unchanged
value reuses the cached compile layer. Pass
`--build-arg VASSAGO_REVISION="$(git rev-parse HEAD)"` to record this repo's
revision too. The image records what went in at
`/usr/local/share/caldir/BUILD_INFO` and in the `io.vassago.caldir.commit`
label. See [ADR-0010](adr/0010-caldir-pinned-to-a-commit.md).

## Disk space on the build host

Check free space and Podman's disk usage before rebuilding:

```sh
df -h /
podman system df
```

Safe cleanup is limited to dangling images and explicitly selected old caldir
images:

```sh
podman image prune
podman images
podman image rm <old-caldir-image-id>
```

`podman image prune` without `-a` removes dangling images only. Remove an old
tagged caldir image only by its explicit id after inspecting `podman images`.
Never use `podman system prune -a` or any prune with `--volumes`: those can
remove unrelated images, containers, or volumes on a shared host.

An unchanged pinned `CALDIR_COMMIT` reuses the compiled Rust layer, so changes
limited to the pipeline scripts do not recompile Rust. If the build host still
lacks space, build on the development machine and transfer the image instead:

```sh
podman build -t localhost/calendar-caldir:vtodo-support caldir/
podman save localhost/calendar-caldir:vtodo-support | ssh baphomet podman load
```

Use matching CPU architectures on the two machines.

## First run

On container start, `entrypoint.sh` snapshots the pipeline's environment into
`/run/caldir/cron.env` and writes the cron schedule from `CRON_SCHEDULE` into
`/etc/cron.d/caldir` (running `cron-run.sh` as root, which restores that
environment, takes a lock and execs `run-sync.sh`; output redirected to the
container's stdout/stderr via `/proc/1/fd/1` and `/proc/1/fd/2`), then checks
`RUN_ON_START`. In `compose.yaml` this is set to `"true"`, so on every
container start `cron-run.sh` runs once immediately
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

This runs as root inside the container with the environment `compose exec`
provides. It does not take the run lock; to avoid racing a running cron job,
run `/usr/local/bin/cron-run.sh`, which skips (exit 0)
while another run holds the lock. Outside of manual runs, cron re-triggers `cron-run.sh`
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
| `[verify]` | `verify-sync.py` (post-publication convergence check) |
| `[diagnose]` | `diagnose-google.py` (manual read-only diagnosis) |

A healthy run shows this order: `[caldir]` provisioning messages, then
`[provision]`/`[google-provision]`, then `[u_crawler-merge]`, then `[caldir]
phase 1: sync providers`, then `[bridge]` and `[windows-bridge]`, then
`[caldir] phase 2: publish bridge results`, then `[verify]`, ending in
`[caldir] ... done` (`caldir/run-sync.sh`). The pipeline runs under `set -eu`
and stops at the first failing step — a run that stops before `phase 2` did not
publish whatever the bridges changed, and a verification failure exits 3 without
printing `done`. See `docs/troubleshooting.md#pipeline-never-reaches-phase-2`.

## Google OAuth prerequisites

`provision-google.py` reads two files under
`/data/.config/caldir/providers/google/` inside the container (host path
`./caldir/config/providers/google/`, gitignored):

- `app_config.toml` — keys `client_id`, `client_secret` (OAuth client
  credentials).
- `session/<GOOGLE_ACCOUNT>.toml` — keys `access_token`, `refresh_token`,
  `expires_at`, `auth_mode`, `granted_scopes`. `auth_mode` must be `"local"`;
  any other value is a fatal error.

`REQUIRED_SCOPES` declares the scopes that `granted_scopes` must contain:

- `https://www.googleapis.com/auth/tasks`
- `https://www.googleapis.com/auth/calendar.events`
- `https://www.googleapis.com/auth/calendar.calendarlist.readonly`
- `https://www.googleapis.com/auth/calendar.calendars`

`provision-google.py` refreshes the access token itself when it is within 5
minutes of `expires_at`. If the refresh response reports `scope`, every required
scope must be present or provisioning fails; if the response omits `scope`, the
recorded grants remain authoritative. The script rewrites only `access_token`
and `expires_at`, never `granted_scopes`, and never performs the initial OAuth
authorization. That first `session/<account>.toml` must be produced by the
`caldir`/`caldir-provider-google` binary's own OAuth flow, which is outside this
repository; consult the `belcaik/caldir` project for how to run it.

Provisioning also warns when the windows calendar is hidden or not selected in
the user's calendar list. It does not change those visibility settings.

## Diagnose Google mirrors

Run the read-only diagnostic when Google and the local mirrors disagree, or when
windows do not appear in Google Calendar:

```sh
podman exec caldir /usr/local/bin/diagnose-google.py
```

It uses GET requests only to report the windows calendar's `hidden`, `selected`
and `accessRole` fields, event and task counts by status, and whether each local
Google provider id is active, cancelled, deleted, or unknown remotely. It never
creates, updates, or deletes Google data. Its only possible write is the normal
OAuth access-token refresh shared with provisioning. It prints counts and ids,
never tokens.

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

A new term needs no code change or image rebuild. `caldir/courses.py` discovers
any top-level course directory with a non-empty `deadlines/` or `windows/`
export; a name such as `202625_<course>` is just an example, not a configured
prefix. On the next run, provisioning and merge create that course's canonical
directories and Radicale collections automatically.

Old canonical collections remain visible to the bridges when their source export
becomes empty or disappears. This prevents an old term from looking like a mass
deletion. Removing an old term from Google is therefore a deliberate manual
cleanup step, not part of term rotation; do not delete canonical assignments as
a routine rotation shortcut.

This replaces the two-step `TERM_PREFIXES` rotation of
[ADR-0008](adr/0008-overlapping-academic-terms.md), superseded by
[ADR-0009](adr/0009-structural-course-discovery.md).
