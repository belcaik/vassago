# Migrations

How to move a running Vassago host from one repository state to the next
without manual edits inside containers. Each section is self-contained.

## 2026-10: structural discovery, verified sync, pinned caldir

From: any image built from `main` up to `2f3f0f4`, possibly with hand-made
hotfixes inside the running container.
To: `refactor/sync-discovery` (ADR-0009, ADR-0010), caldir fork commit
`5bf58e03901df33547ecf5a9db2caea1eee347da`.

Nothing here deletes state, volumes or remote data. Every step before
"Switch" is read-only for Google.

### 0. Preconditions (on the development machine)

1. The pinned caldir SHA is published: belcaik/caldir#2 (sync bases,
   scopes, pagination) is merged into `vtodo-support`, and belcaik/caldir#3
   (`3ca66aa`, Google task identity) is merged or at least pushed. Never
   build an image with #2 and without #3: it creates a new Google task for
   every pushed task on every run.
2. `refactor/sync-discovery` is merged into Vassago `main` and pushed.

### 1. Record what the host runs today

```sh
cd <vassago checkout on the host>
git status --short && git log --oneline -1
podman inspect localhost/calendar-caldir:vtodo-support --format '{{.Id}} {{.Created}}'
podman diff caldir > ~/caldir-container-diff-$(date +%F).txt
podman exec caldir cat /etc/cron.d/caldir
```

A non-empty `git status`, or `podman diff` entries under `/usr/local/bin` or
`/etc`, are hotfixes that exist only on the host. Copy them out and compare
them with the repository before continuing; the new image replaces them.

### 2. Back up runtime state (outside the checkout)

```sh
tar -C <vassago checkout> -czf ~/vassago-state-$(date +%F).tar.gz \
    caldir/state caldir/calendars radicale/data
podman tag localhost/calendar-caldir:vtodo-support \
    localhost/calendar-caldir:pre-2026-10
```

The archive holds personal calendar data; keep it out of the repository and
readable only by you (`chmod 600`). `caldir/config` (OAuth tokens) is not
needed for a rollback and is left out on purpose.

### 3. Get the new image

Either build on the host (needs several GB free on `/`; see
[operations.md](operations.md#disk-space-on-the-build-host)):

```sh
git fetch origin && git checkout <merged main sha>
podman build \
    --build-arg VASSAGO_REVISION="$(git rev-parse HEAD)" \
    -t localhost/calendar-caldir:vtodo-support caldir/
```

or build on the development machine (same CPU architecture, check
`uname -m` on both) and copy it, so Rust never compiles on the host:

```sh
podman build --build-arg VASSAGO_REVISION="$(git rev-parse HEAD)" \
    -t localhost/calendar-caldir:vtodo-support caldir/
podman save localhost/calendar-caldir:vtodo-support | ssh baphomet podman load
```

Then check what went in:

```sh
podman run --rm --entrypoint cat localhost/calendar-caldir:vtodo-support \
    /usr/local/share/caldir/BUILD_INFO
```

`caldir_commit` must be `5bf58e03901df33547ecf5a9db2caea1eee347da`. The build
already failed if any of the four OAuth scopes were missing.

### 4. Baseline diagnosis with the new image, before switching (GET only)

```sh
podman-compose run --rm --entrypoint /usr/local/bin/diagnose-google.py caldir
```

Keep the output: windows calendar visibility, event and task counts by
status, and what happened to each local provider id. This may refresh the
access token, which every pipeline run does anyway.

### 4b. Restore orphaned Google Tasks mirror files (once)

Mirror files for tasks pulled under a derived UID may have been removed while
caldir kept their sync base. The new caldir lists every page of the task list,
sees those tasks, and reads them as deleted locally: phase 1 would **delete
them from Google**. Recreate the files from caldir's own bases first. The
script only adds files. Run it without `--apply` first and check the list:

```sh
podman unshare python3 scripts/restore-orphan-task-files.py caldir/calendars/unab
podman unshare python3 scripts/restore-orphan-task-files.py caldir/calendars/unab --apply
```

Then confirm phase 1 pushes no deletes (GET only):

```sh
podman-compose run --rm --entrypoint caldir caldir status --calendar unab
```

### 5. Switch

`podman-compose` 1.0.x `up -d` restarts an existing `caldir` container on the
image it was created from, even after the tag moved. Stop and remove it first,
and check the image id before the first run gets far
(`RUN_ON_START=true` starts it at once):

```sh
podman-compose stop caldir && podman rm caldir
podman-compose up -d caldir
podman ps --filter name=^caldir$ --format '{{.ImageID}}'   # the new image
podman logs -f caldir
```

Expected on the first run:

- `[u_crawler-merge]` reports only new assignments as created; canonical files
  are matched by UID, so the names caldir gives them do not matter.
- `202625_*` (and any newer) courses get Radicale collections and canonical
  directories; their assignments are seeded into Google.
- Tasks the old loop kept restoring are adopted, not recreated:
  `[bridge] adopt Google task <- …` once per assignment whose Google task
  already exists, then `canonical <- Google` for the ones completed in Google.
  Only assignments with no Google task at all are created.
- The run ends with `[verify] … 0 missing … 0 without …` and `[caldir] … done`.

### 6. Verify

```sh
podman exec caldir cat /etc/cron.d/caldir          # cron-run.sh, TZ line
podman exec caldir stat -c '%a %U' /run/caldir/cron.env   # 600 root
podman exec caldir /usr/local/bin/diagnose-google.py
```

Wait for one scheduled run (15 min) and check its log lines carry local time
and end in `done`. A run that ends with `verify-sync` exit 3 lists the UIDs
that did not converge; see [troubleshooting.md](troubleshooting.md).

### 7. Repair duplicate canonical files (2026-10-29, once)

The first deploy ran `5a0d148`, whose merge matched canonical files by name.
caldir renamed every file it wrote back, the next merge re-created
`assignment-<id>.ics` beside it, and the bridge stopped on
`duplicate canonical UID`. With `caldir` stopped and the fixed image tagged,
keep the copy each UID's last bridge state proves, quarantine the rest (dry
run first; exit 1 lists any UID it could not prove and leaves it alone):

```sh
podman unshare python3 scripts/quarantine-duplicate-canonical.py \
    caldir/calendars caldir/state caldir/state/quarantine-merge-20261029
podman unshare python3 scripts/quarantine-duplicate-canonical.py \
    caldir/calendars caldir/state caldir/state/quarantine-merge-20261029 --apply
```

Then switch as in step 5. The first run pushes the restored completions to
Radicale and nothing to Google Tasks.

### Rollback

```sh
podman tag localhost/calendar-caldir:pre-2026-10 localhost/calendar-caldir:vtodo-support
podman-compose stop caldir && podman rm caldir
podman-compose up -d caldir
```

State written by the new version stays compatible with the old one: bridge
state files keep their versions, and caldir sync bases that were forgotten
belong to items already deleted on both sides. If calendar data itself has to
be restored, stop `caldir` and unpack the archive from step 2.
