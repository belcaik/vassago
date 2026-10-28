# Incident 2026-10: OAuth scopes, cron environment and non-converging sync

- Window: October 2026 (diagnosed on `baphomet`; repository work 2026-10-28)
- Impact: `202625_*` courses never synced; scheduled runs failed before
  provisioning; Google Tasks oscillated (57 restored / 57 deleted per run)
  while runs reported `done`; delivery windows not visible in Google Calendar.
- Plan and evidence: [docs/plans/sync-refactor.md](../plans/sync-refactor.md)

## Timeline (as reported, not re-observed here)

1. `202625_*` courses appeared in the u_crawler export and were ignored.
2. `provision-google.py` failed with `existing Google OAuth session is
   missing scopes: https://www.googleapis.com/auth/calendar.calendars`.
3. The deployed `caldir-provider-google` generated consent URLs with three
   scopes. `grep -aqF` on the binary showed `calendar.calendars` absent,
   although `vtodo-support` declared it.
4. Rebuilds had been failing with `no space left on device` (`/` at ~95 %),
   so the old image kept running. After pruning unused images the rebuild
   succeeded.
5. Cron ran every 15 minutes but each run died with
   `provision-radicale.sh: 6: RADICALE_USER: RADICALE_USER required`, logging
   in UTC.
6. A run restored 57 tasks in `bridge-vtodo.py`; phase 2 deleted the same 57
   locally as remote deletions; the run ended in `[caldir] done`.
7. 57 windows processed, `Pushed: 57 updated`, nothing visible in Google
   Calendar.

## Hotfixes made during the incident and where they are now

| Hotfix | Where it existed | Status in Git |
| --- | --- | --- |
| `export -p` snapshot + cron line sourcing it (`/etc/caldir.env`) | branch `fix/cron-env-and-term-202625` (`7071071`), merged into `main` as PR #2 | Superseded by `caldir/cron-run.sh` + `entrypoint.sh` (allowlisted variables, `0600`, owner check, `flock`). Replaced when `refactor/sync-discovery` merged `main`. |
| Same idea with `/run/caldir-cron.env` and `umask 077` | suggested for Baphomet only | Not in any branch. Whether it was applied inside the running container is **unknown**; check with the commands below. |
| `TERM_PREFIXES = ("202615_", "202625_")` in five scripts + ADR-0008 | branch `fix/cron-env-and-term-202625`, merged into `main` as PR #2 | Superseded by structural discovery (ADR-0009). |
| Pruning unused images, rebuilding | Baphomet only | Procedure documented in [docs/operations.md](../operations.md#disk-space-on-the-build-host). |

To find out what Baphomet actually runs (read-only):

```sh
podman inspect localhost/calendar-caldir:vtodo-support --format '{{.Id}} {{.Created}}'
podman diff caldir                      # files changed inside the container
podman exec caldir sh -c 'ls -l /etc/cron.d/caldir /etc/caldir.env /run/caldir-cron.env 2>&1; cat /etc/cron.d/caldir'
podman exec caldir sh -c 'for s in /usr/local/bin/*.py /usr/local/bin/*.sh; do sha256sum "$s"; done'
```

Compare the last output with `sha256sum caldir/*.py caldir/*.sh` on the
checkout that was used to build the image.

## Root causes and fixes

| Problem | Root cause | Change | Regression test | Status |
| --- | --- | --- | --- | --- |
| `202625_*` ignored | Literal `202615_*` globs in five scripts | Structural discovery in `caldir/courses.py`; check.sh step 4 forbids term codes (ADR-0009) | `tests/test_courses.py`, `test_new_course_in_a_future_term_is_added_automatically` | Fixed in repo |
| Missing `calendar.calendars` in binary | `git clone --branch` in a cached `RUN`: layer reused after the branch moved; failed rebuilds (disk) kept the old image | `ARG CALDIR_COMMIT` pinned SHA, HEAD check, scope `grep` in build, `BUILD_INFO`, labels (ADR-0008) | check.sh step 6, `tests/test_repo_invariants.py`; build-time scope check | Fixed in repo; needs a build on the host |
| Scopes lost on refresh | `refresh_local` replaced `granted_scopes` with the response's optional `scope` | Keep recorded scopes when `scope` is omitted; use it when present (fork `e144fa0`). `provision-google.py` validates a reported `scope`, never writes scopes | caldir `session::store::tests::*refresh_scope*`; `tests/test_provision_google.py` | Fixed in fork branch (not pushed) |
| Consent URL scopes not asserted | No test on the URL actually sent | Helper builds an encoded URL; test checks the four scopes (fork `564203d`) | caldir `connect::tests::the_consent_url_requests_the_full_scope_set` | Fixed in fork branch (not pushed) |
| Cron without env / UTC | cron jobs start with an empty environment | `entrypoint.sh` snapshot to `/run/caldir/cron.env` + `cron-run.sh`; `TZ` in the cron.d header | `tests/test_cron_env.py` | Fixed in repo |
| 57 restored / 57 deleted, run says `done` | caldir sync bases were append-only: a restored file with a known UID and no remote copy is a "remote deletion" | caldir forgets a base after an applied delete (fork `c157a8e`); `verify-sync.py` makes a non-converged run exit 3 | caldir `connection::tests::a_locally_recreated_item_is_created_after_an_incoming_delete`; `test_google_deletion_is_restored_and_converges`, `test_append_only_bases_loop_is_reported_not_done` | Fixed in repo + fork branch |
| Tasks "vanish" from the Google listing | `caldir-provider-google` `list_tasks` read only the first page of `tasks.list` (default 20 items, no `nextPageToken` handling); with ~77 tasks exactly 57 are unseen and look remotely deleted. Without this fix, forgetting sync bases alone would create 57 duplicates per run | Follow `nextPageToken` with `maxResults=100`, fail on any page error (fork) | caldir `google_tasks::client` paging tests | Fixed in fork branch (not pushed); confirm the real list size with `diagnose-google.py` |
| Deploying the fixes above would create a new Google task per pushed task on every run (found in a dry run on a copy of production state, deploy aborted) | Google Tasks keeps no iCalendar UID: the provider lists every task as `<id>@google-tasks.com`, while the pushed local file keeps `u_crawler-todo-N` plus `X-GOOGLE-TASK-ID`. Diffed by UID, each pushed task is a remote delete plus a new remote task; once bases are forgotten, the restored file is pushed as a create | Before diffing, a remote task takes the UID of the one local task file carrying its `X-GOOGLE-TASK-ID`; bases are never used as a mapping; a local copy under the derived UID is dropped locally (fork `3ca66aa`); fields Google Tasks cannot hold are taken from the local file (fork `5bf58e0`), belcaik/caldir#3. Bootstrap: `bridge-vtodo.py` adopts the derived copy of a missing assignment instead of re-seeding it; `scripts/restore-orphan-task-files.py` recreates derived mirror files caldir still has a base for, so phase 1 does not delete them from Google | caldir `connection::tests::a_pushed_task_listed_under_a_derived_uid_does_not_churn`, `fields_google_tasks_cannot_hold_are_not_a_remote_edit` and 8 more; `test_google_copy_under_derived_uid_is_adopted_not_reseeded`; `tests/test_restore_orphan_task_files.py` | Fixed in fork branch and repo; dry run on the host before deploy |
| `Pushed: 57 updated` on every run | `bridge-windows.py` merge kept only `X-GOOGLE-*` from the mirror and dropped `ORGANIZER`, which Google returns and caldir's PATCH never sends; the dropped line also changed the bridge hash. Every run re-armed an update for every window | `ORGANIZER` is kept from the mirror (unless the canonical sets it) and excluded from the hash; authority rules unchanged | `test_google_assigned_organizer_does_not_cause_perpetual_updates` | Fixed in repo; other round-trip gaps (DTSTART form, DESCRIPTION) need a production read |
| Windows not visible | Unconfirmed. Candidates from code review: calendar hidden/unselected (and `provision-google.py` listed calendars without `showHidden`, so a hidden one was invisible to it and a namesake could be created); events still `cancelled` while the PATCH reports success; wrong calendar id | `showHidden=true` when resolving the calendar; warning on hidden/unselected; `diagnose-google.py` lists namesakes, event status per local id | `test_hidden_calendar_is_found_not_duplicated`, `test_reports_cancelled_events_and_uses_only_get` | **Open**, needs `diagnose-google.py` on the host |
| Deadlines rewritten every run | CRLF vs `read_text()` comparison in `merge-ucrawler.py` | Compare raw bytes | `test_unchanged_deadline_is_not_rewritten` | Fixed in repo |
| Empty export could delete canonical items | `merge-ucrawler.py` removed anything missing from the export | Empty export removes nothing; mass-removal guard; duplicate-UID abort | `test_emptied_source_is_not_a_deletion`, `test_mass_removal_is_held_back`, `test_duplicate_uid_across_courses_aborts_before_writing` | Fixed in repo |
| Dockerfile `\ ` continuation | Backslash followed by a space in the `chmod` list | Removed; check.sh step 6 rejects it | `tests/test_repo_invariants.py` | Fixed in repo |

## Follow-ups

- Run `diagnose-google.py` on Baphomet before and after the deploy and record
  the output (counts only) here.
- Decide whether `MERGE_ALLOW_MASS_DELETE` should also be needed for the
  bridges' canonical-deleted path.
