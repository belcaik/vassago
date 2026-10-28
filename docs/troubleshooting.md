# Troubleshooting

Symptom (exact log text) -> cause -> resolution. The rules behind these
behaviours live in [sync-semantics.md](sync-semantics.md); this file does not
repeat them.

All output shown here appears in the `caldir` container log: `entrypoint.sh`
points cron's stdout and stderr at `/proc/1/fd/1` and `/proc/1/fd/2`
(`caldir/entrypoint.sh:62`). Grep by [log prefix](architecture.md#conventions).

## Bridge conflicts

A conflict means the bridge found a situation where picking a winner would
destroy someone's intent, so it picked nobody. Every conflict is counted, the
run exits 2, and phase 2 is skipped — see
[Pipeline never reaches phase 2](#pipeline-never-reaches-phase-2).

**How to resolve any conflict.** Decide which side is right, then make the other
side match, or delete that UID's entry from the bridge state file so the next
run re-observes it from scratch (see
[State files](sync-semantics.md#state-files)). Three admissible moves:

1. edit or delete the **Google-side** mirror file under `unab/` or
   `unab-windows/`;
2. edit the **canonical** file under `<course>__deadlines/` or
   `<course>__windows/`;
3. remove the item's entry from the bridge state JSON.

Never resolve a conflict by deleting the canonical `assignment-<id>.ics`. That
file is the Canvas-derived source of truth; deleting it either resurfaces at the
next `merge-ucrawler.py` run or, worse, propagates the deletion outward.

After any manual edit, the next scheduled run (or a manual `run-sync.sh`) picks
it up.

### `managed task exists only in Google and has no canonical course`

```
[bridge] CONFLICT <uid>: managed task exists only in Google and has no canonical course
```

**Branch.** VTODO bridge, row 3: no state entry, the UID is present in `unab/`
but absent from every `<course>__deadlines/` directory
(`caldir/bridge-vtodo.py:569-580`).

**Cause.** A task carrying a `u_crawler-todo-` UID exists in Google with no
canonical counterpart. Typically: the state file was lost or its version was
bumped while a stale task lingered in Google, the canonical course directory was
renamed or removed, or a task was copied between Google lists.

**Resolution.** If the assignment is genuinely gone, delete the mirror file from
`unab/`. If the canonical side went missing by accident, restore the course
directory (a `u_crawler` run recreates the source files; `merge-ucrawler.py`
recreates the canonical file) and let the next run pair them up.

### `first observation differs on shared fields`

```
[bridge] CONFLICT <uid>: first observation differs on shared fields
```

**Branch.** VTODO bridge, row 4: no state entry, both sides present, and their
`shared_signature` values differ (`caldir/bridge-vtodo.py:585-593`).

**Cause.** The bridge is seeing this UID for the first time — new state file,
discarded state after a version mismatch, or a manually removed entry — and the
two sides already disagree on `SUMMARY`, `DESCRIPTION`, `DUE` (day granularity),
`STATUS`, or `COMPLETED`. With no history it cannot tell which side moved.

**Resolution.** Compare the two files and make the shared fields match on the
side you consider wrong. Making the Google mirror match canonical is usually
right, since canonical came from Canvas. Once the signatures agree, the next run
takes row 5 and reconciles silently.

### `both sides changed shared fields`

```
[bridge] CONFLICT <uid>: both sides changed shared fields
```

**Branch.** VTODO bridge, row 11: state entry exists, both sides changed since
it was written, and their `shared_signature` values differ
(`caldir/bridge-vtodo.py:714-723`).

**Cause.** A genuine concurrent edit — for example Canvas moved the due date
while the task was also renamed or completed in Google.

**Resolution.** Decide which edit survives. To keep the Google edit, edit the
canonical file so the shared fields match, or vice versa. Do not delete the
state entry as a first move: that turns this into the previous conflict without
resolving the disagreement.

### `deleted canonically but changed in Google`

```
[bridge] CONFLICT <uid>: deleted canonically but changed in Google
[windows-bridge] CONFLICT <uid>: deleted canonically but changed in Google
```

**Branch.** Emitted by both bridges: VTODO row 7
(`caldir/bridge-vtodo.py:644-652`) and windows row 6
(`caldir/bridge-windows.py:443-451`). The canonical file is gone, the Google
mirror is still there, and the mirror changed since the state entry was written.
Read the prefix to tell which bridge tripped.

**Cause.** Canvas dropped the assignment (so `merge-ucrawler.py` removed the
canonical `assignment-*.ics`) while the user had edited the item in Google. The
bridge will not silently discard that edit.

**Resolution.** If the item is truly obsolete, delete the mirror file from
`unab/` or `unab-windows/` — the state entry is cleaned up on the following run.
If the edit matters, copy the item somewhere outside the managed directories
first, or change its UID so it stops matching the managed prefix and both
bridges leave it alone.

### `deleted in Google but changed canonically`

```
[bridge] CONFLICT <uid>: deleted in Google but changed canonically
```

**Branch.** VTODO bridge, row 9: the Google mirror file is gone and the
canonical file changed since the state entry was written
(`caldir/bridge-vtodo.py:673-681`).

**Cause.** The task was hard-deleted in Google while Canvas also updated the
assignment. Had the canonical side been unchanged, the bridge would simply
re-seed Google (row 8) — the canonical assignment is never deleted in response
to a Google deletion.

**Resolution.** To keep the assignment, delete its entry from the VTODO bridge
state file; the next run sees canonical-only with no state and re-seeds Google
(row 2). To keep it out of Google, mark it `STATUS:COMPLETED` or
`STATUS:CANCELLED` on the canonical side instead of hard-deleting — completion
state is a shared field and travels normally.

The windows bridge has no equivalent conflict: a Google deletion there is
re-seeded unconditionally (windows row 7).

### `managed event exists only in Google`

```
[windows-bridge] CONFLICT <uid>: managed event exists only in Google
```

**Branch.** Windows bridge, row 3: no state entry, a `u_crawler-window-` UID
present in `unab-windows/` and absent from every `<course>__windows/` directory
(`caldir/bridge-windows.py:383-391`).

**Cause.** Same shape as the VTODO "only in Google" conflict: a stale mirrored
event outliving its canonical source, usually after state loss or removal of the
canonical course collection.

**Resolution.** Delete the event from `unab-windows/` if the delivery window no
longer exists; restore the canonical `<course>__windows/` file if it went
missing by accident.

## Provisioning failures

Provisioning runs first (steps 1 and 2 of the pipeline). Under `set -eu` a
failure here aborts the run before any merge or sync happens.

### Radicale collection creation

```
[provision] failed <course>__<kind>: HTTP <code>
<response body>
```

**Cause.** The `MKCOL` request to
`${RADICALE_URL}/${RADICALE_USER}/<course>__<kind>/` returned a status outside
the accepted set `200 201 204 405` (`caldir/provision-radicale.sh:50-58`). The
script prints the body of `/tmp/radicale-response` after the status line.

`405` is accepted deliberately: it is what Radicale answers when the collection
already exists, which is a success for this script's purposes.

Common codes:

| Code | Meaning |
| --- | --- |
| `401` | `RADICALE_USER` / `RADICALE_PASSWORD` do not match `/etc/radicale/users`. That htpasswd file is gitignored and created by hand — no script provisions it. |
| `403` | authenticated as a different user than the path's owner; `[rights] type = owner_only` (`radicale/config/config:12-13`) forbids it |
| `000` | curl could not connect — the `radicale` service is down, or `RADICALE_URL` is wrong |

**Resolution.** Fix the credential or the URL and re-run. Note that
`.caldir/config.toml` is only written after a successful `MKCOL`
(`caldir/provision-radicale.sh:60-69`), so a failed provisioning leaves an empty
`<course>__<kind>/.caldir/` directory behind; the next successful run fills it
in.

Also note both variables are mandatory: an unset `RADICALE_USER` or
`RADICALE_PASSWORD` aborts immediately with
`RADICALE_USER required` / `RADICALE_PASSWORD required`
(`caldir/provision-radicale.sh:6-7`).

### Google provisioning

Every fatal message is printed to stderr as
`[google-provision] fatal: <message>` and exits 1
(`caldir/provision-google.py:71-76`).

| Message | Cause | Resolution |
| --- | --- | --- |
| `missing <path>` | `app_config.toml` or the session file is absent (`:79-86`). Expected at `/data/.config/caldir/providers/google/app_config.toml` and `.../session/<GOOGLE_ACCOUNT>.toml` | Confirm `./caldir/config` is mounted and populated, and that `GOOGLE_ACCOUNT` matches the session filename. `GOOGLE_ACCOUNT` is not set in `compose.yaml`, so the script's built-in default applies unless you set it. |
| `could not parse <path>: <exc>` | malformed TOML | Repair the file; the session file is rewritten in place by the refresh path, so a crashed refresh is a candidate cause |
| `Google session is not local OAuth` | `auth_mode` in the session file is not `"local"` (`:247-250`) | Re-run the OAuth flow in local mode. The script only knows how to refresh a local-mode session. |
| `existing Google OAuth session is missing scopes: <list>` | `granted_scopes` lacks one of `.../auth/tasks`, `.../auth/calendar.events`, `.../auth/calendar.calendarlist.readonly`, `.../auth/calendar.calendars` | Re-authorise requesting all four scopes. Editing `granted_scopes` by hand only moves the failure to the first API call. |
| `refreshed Google token is missing scopes: <list>; reconnect with caldir connect` | the refresh response reported a `scope` set without every `REQUIRED_SCOPES` entry | Reconnect with `caldir connect` and grant all four scopes. Provisioning never rewrites scopes to hide this failure. |
| `OAuth session is incomplete` | `access_token`, `refresh_token`, or `expires_at` missing or empty (`:287-294`) | Re-authorise; a session without a refresh token cannot be renewed |
| `could not update access_token in existing session` / `could not update expires_at in existing session` | the refresh succeeded but the regex rewrite matched neither line exactly once (`:218-228`). The script edits only those two lines by `^key = "…"` match, leaving `refresh_token`, `auth_mode`, and `granted_scopes` untouched | Restore the two keys to plain one-line `key = "value"` form and re-run |
| `Google refresh response did not contain access_token` | the token endpoint answered 200 without a token (`:337-341`) — usually a revoked or expired refresh token | Re-authorise |
| `multiple Google Task Lists named '<name>'` | two lists share the title (`:417-421`) | Rename or delete the duplicate in Google, or point `GOOGLE_UNAB_TASKLIST_NAME` at a unique title. The script refuses to guess which list is the mirror. |
| `multiple Google Calendars named '<name>'` | two calendars share the summary (`:516-520`) | Same remedy via `GOOGLE_UNAB_WINDOWS_CALENDAR` |
| `calendar '<name>' is not writable (accessRole='<role>')` | the matched calendar's `accessRole` is neither `owner` nor `writer` (`:525-535`) — typically a calendar shared read-only | Use a calendar you own, or have the owner grant write access |
| `Google Tasks did not return task list id` / `Google Calendar did not return a calendar id` | the create call returned 200 with no `id` (`:452-458`, `:565-571`) | Retry; check the resource was not partly created before retrying, to avoid a duplicate |
| `<METHOD> <url>: HTTP <code>: <body>` | any Google API error (`:138-147`) | Read the body. `401` after a successful refresh points at revoked consent; `403` usually means a missing scope or a disabled API in the Google Cloud project |
| `<METHOD> <url>: <exc>` | network-level failure, 30 s timeout (`:149-152`) | Check container networking and outbound HTTPS |

Note that creating the windows calendar may fail even when everything above is
in order: the Spanish comment at `:58-59` records that the token in use could not
create secondary calendars, which is why the script prefers an existing calendar
by name.

## Verification fails with exit 3

```
[verify] deadlines: 57 expected, 57 missing from mirror, 0 without X-GOOGLE-TASK-ID
[verify] deadlines missing: <uid>
```

**Cause.** A managed canonical UID within ±365 days has no Google mirror file,
or its mirror file lacks the provider id written after a successful push. On an
old caldir image this is the restore loop: a bridge restores a Google deletion,
then phase 2 treats the restoration as another remote deletion and removes it
again. The corrected caldir fork forgets the sync base after an applied delete,
so the restoration becomes a Create. See
[Incident 2026-10](incidents/2026-10-oauth-cron-sync.md).

**Resolution.** First check which caldir commit the image contains:

```sh
podman exec caldir cat /usr/local/share/caldir/BUILD_INFO
```

Compare `caldir_commit` with the pin in `caldir/Dockerfile`; rebuild and redeploy
if it is stale. If the image is current, run
`podman exec caldir /usr/local/bin/diagnose-google.py` and inspect the listed
UIDs in the canonical and mirror files. Do not delete the canonical assignment
to clear the error. Verification runs after phase 2, so exit 3 suppresses
`[caldir] … done` but does not roll back data already published.

## Mass removal is held back

```
[u_crawler-merge] WARNING holding N/M removals in <course>__<kind>: over the mass-delete limit ...
```

**Cause.** One merge would remove more than half of a course's managed files
(minimum 2). An empty or missing export removes nothing; this guard also holds
back a partial export that looks like a large Canvas deletion.

**Resolution.** Confirm that the u_crawler export is complete and that the
removals are intentional. If they are legitimate, set
`MERGE_ALLOW_MASS_DELETE=1` for one run. Otherwise fix the export first; the
canonical files were left in place.

## Duplicate u_crawler UID

```
RuntimeError: duplicate u_crawler UID <uid>: <path A> and <path B>
```

**Cause.** Two source export files of the same kind carry one UID, either in one
course or across courses. The merge aborts before writing that kind because the
bridges key mirrored items by UID.

**Resolution.** The message names both source paths. Remove or correct the
duplicate in the u_crawler export, then rerun; changing only the canonical copy
would not fix the next merge.

## Windows are not visible in Google Calendar

**Symptom.** The windows bridge reports pushes, but no delivery windows appear.
Provisioning may also warn that the calendar `is hidden in the calendar list` or
`is not selected (not displayed)`.

**Cause.** The calendar can be hidden or unselected in the user's calendar list.
Events can also be `cancelled` in Google, so updating them does not make them
visible. The original incident left the production cause open; see
[Incident 2026-10](incidents/2026-10-oauth-cron-sync.md).

**Resolution.** Run the GET-only diagnosis:

```sh
podman exec caldir /usr/local/bin/diagnose-google.py
```

Check `hidden` and `selected`, then the event statuses and the outcome for each
local `X-GOOGLE-EVENT-ID`. Enable the calendar in Google Calendar if it is hidden
or unselected. If the ids point to cancelled or unknown events, use those ids to
investigate the mirror mismatch rather than assuming another push will make the
events visible.

## Scheduled runs fail but the start-up run works

```
[caldir] 2026-10-22T19:00:01+00:00 provisioning Radicale
/usr/local/bin/provision-radicale.sh: 6: RADICALE_USER: RADICALE_USER required
```

**Cause.** The image predates the environment snapshot written by
`caldir/entrypoint.sh` and restored by `caldir/cron-run.sh`. cron starts jobs
with an empty environment, so every scheduled run lacked `RADICALE_*` and `TZ`;
only the `RUN_ON_START` run, which inherits the container environment, could
succeed. The `+00:00` offset on the cron-run timestamps, against `-03:00` on the
start-up run, is the tell.

**Resolution.** Rebuild the caldir image and recreate the container (see
[operations.md](operations.md#build-the-caldir-image)). Verify with
`podman exec caldir stat -c '%a %U' /run/caldir/cron.env` (`600 root`) and by
the next quarter-hour run reaching `[caldir] … done`. Snapshot errors are under
[Cron environment snapshot failures](#cron-environment-snapshot-failures).

## Google refresh fails with `invalid_grant`

```
[google-provision] fatal: POST https://oauth2.googleapis.com/token: HTTP 400: {
  "error": "invalid_grant",
```

**Cause.** The refresh token in the session file was revoked or expired. When
the OAuth app's publishing status is **Testing**, Google expires refresh tokens
after seven days, so this recurs weekly.

**Resolution.** In the Google Cloud console, set the consent screen's
publishing status to **In production** (a personal app does not need
verification; consent shows an "unverified app" warning). Then re-authorise in
local mode with all four scopes listed under
[Google provisioning](#google-provisioning).

## Duplicate UIDs

```
RuntimeError: duplicate canonical UID <uid>: <path A> and <path B>
RuntimeError: duplicate Google UID <uid>: <path A> and <path B>
```

**Cause.** Two `.ics` files in the same scanned scope carry the same managed
UID. Both bridges refuse to continue: `caldir/bridge-vtodo.py:390-394` and
`:415-420`, `caldir/bridge-windows.py:178-182` and `:202-209`. The canonical
scan spans **all** `<course>__deadlines/` (or `__windows/`) directories at once,
so the same assignment appearing under two courses trips it too.

Typical origins: a CalDAV client writing a copy under its own filename, a
restored backup landing beside the original, or a rename that left both files in
place.

This is a hard failure, not a conflict: the traceback exits 1, the run stops,
and no state is saved (`save_state` is never reached). See
[Exit codes](sync-semantics.md#exit-codes).

**Resolution.** The message names both paths. Keep `assignment-<id>.ics` and
move the other file out of the calendar tree.

For the canonical deadlines side, `caldir/dedupe-vtodo.py` automates exactly
that: it keeps `assignment-<id>.ics` (recreating it from the u_crawler source
when needed) and quarantines the rest under
`/data/.local/share/caldir/quarantine-vtodo/`. It is **not** in the image —
`caldir/Dockerfile` does not COPY it and `run-sync.sh` never calls it — so copy
both `dedupe-vtodo.py` and its `courses.py` dependency into the running container
and execute it by hand. It has no equivalent for the windows side or for the
Google mirror directories; clean those up manually. See
[dedupe-vtodo.py](sync-semantics.md#dedupe-vtodopy).

## Pipeline never reaches phase 2

**Symptom.** The log shows the run starting and progressing, then stopping at a
bridge:

```
[caldir] <timestamp> phase 1: sync providers
[caldir] <timestamp> bridge VTODO
[bridge] CONFLICT <uid>: ...
[bridge] done: 3 changed, 0 deleted, 1 conflicts
[caldir] VTODO bridge failed/conflicted (rc=2)
```

and never prints:

```
[caldir] <timestamp> phase 2: publish bridge results
[caldir] <timestamp> done
```

The same shape applies to `[caldir] windows bridge failed/conflicted (rc=2)`.

**How to spot it.** Two reliable checks: the absence of `[caldir] … done` at the
end of a run, and the `N conflicts` counter in the bridge's `done:` line being
non-zero (`caldir/bridge-vtodo.py:841-846`,
`caldir/bridge-windows.py:574-579`).

**Cause.** `run-sync.sh` captures each bridge's return code and exits with it
when non-zero, before reaching the second `caldir sync`
(`caldir/run-sync.sh:32-35`, `:43-46`). Phase 2 is the step that publishes what
the bridges wrote, so its absence means the local files were updated but nothing
reached Radicale or Google.

**Consequences.** One conflicting item blocks publication for **every**
calendar in that run, including untouched ones. Because the schedule is
`*/15 * * * *`, this repeats every fifteen minutes until the conflict is
resolved — the log fills with the same CONFLICT line. A conflict in
`bridge-vtodo.py` also means `bridge-windows.py` never runs at all that cycle.

Progress is not lost: both bridges save state before returning, so every
non-conflicting UID they reconciled is recorded and is not re-processed next
run.

**Resolution.** Resolve the conflict per
[Bridge conflicts](#bridge-conflicts). The next scheduled run then completes
through phase 2 and publishes the accumulated changes. A non-2 exit code here
means a hard failure instead — check
[Duplicate UIDs](#duplicate-uids) and the traceback in the log.

## Cron environment snapshot failures

```
[caldir] cron-run: fatal: env snapshot /run/caldir/cron.env is missing (did entrypoint.sh run?)
[caldir] cron-run: fatal: env snapshot /run/caldir/cron.env has mode <mode>, expected 600
[caldir] cron-run: fatal: env snapshot /run/caldir/cron.env is owned by uid <uid>, expected uid 0
```

The wrapper also refuses a symlink and exits 78 for these failures.

**Cause.** `entrypoint.sh` did not create the allowlisted environment snapshot,
or a manual hotfix changed its path, type, owner, or permissions. Cron starts
with an empty environment, so bypassing this file would lose required settings.
See [Incident 2026-10](incidents/2026-10-oauth-cron-sync.md).

**Resolution.** Recreate `caldir` from the current image so `entrypoint.sh`
writes `/run/caldir/cron.env` atomically as root with mode `0600`. Check only its
metadata, not its secret contents:

```sh
podman exec caldir stat -c '%a %U' /run/caldir/cron.env
```

Expected output is `600 root`. Do not print the snapshot or weaken the owner and
mode checks to make the run continue.
