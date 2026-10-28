# Migrations

How to move a running Vassago host from one repository state to the next
without manual edits inside containers. Each section is self-contained.

## 2026-10: structural discovery, verified sync, pinned caldir

From: any image built from `main` up to `2f3f0f4`, possibly with hand-made
hotfixes inside the running container.
To: `refactor/sync-discovery` (ADR-0008, ADR-0009), caldir fork commit
`564203d59aabd7a3b6fe08daf96ffa031e95f322`.

Nothing here deletes state, volumes or remote data. Every step before
"Switch" is read-only for Google.

### 0. Preconditions (on the development machine)

1. The caldir fork branch is published, so the pinned SHA can be fetched:
   `git -C <caldir checkout> push origin fix/sync-bases-and-scope-refresh`
   and merge it into `vtodo-support` (fast-forward).
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

`caldir_commit` must be `564203d59aabd7a3b6fe08daf96ffa031e95f322`. The build
already failed if any of the four OAuth scopes were missing.

### 4. Baseline diagnosis with the new image, before switching (GET only)

```sh
podman-compose run --rm --entrypoint /usr/local/bin/diagnose-google.py caldir
```

Keep the output: windows calendar visibility, event and task counts by
status, and what happened to each local provider id. This may refresh the
access token, which every pipeline run does anyway.

### 5. Switch

```sh
podman-compose up -d caldir
podman logs -f caldir
```

Expected on the first run:

- `[u_crawler-merge]` reports every deadline and window as `updated` once:
  files written as LF by the old merge are normalised to CRLF. The next run
  reports 0.
- `202625_*` (and any newer) courses get Radicale collections and canonical
  directories; their assignments are seeded into Google.
- Tasks the old loop kept restoring are created in Google with new ids.
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

### Rollback

```sh
podman tag localhost/calendar-caldir:pre-2026-10 localhost/calendar-caldir:vtodo-support
podman-compose up -d caldir
```

State written by the new version stays compatible with the old one: bridge
state files keep their versions, and caldir sync bases that were forgotten
belong to items already deleted on both sides. If calendar data itself has to
be restored, stop `caldir` and unpack the archive from step 2.
