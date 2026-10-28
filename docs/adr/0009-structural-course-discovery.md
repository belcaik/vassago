# ADR-0009: Courses are discovered from u_crawler's layout, and a run must converge

- Status: Accepted — replaces the term-prefix lockstep rule
- Date: 2026-10-28
- Supersedes: [ADR-0008](0008-overlapping-academic-terms.md) "several
  academic terms can be processed at once" (merged from
  `fix/cron-env-and-term-202625`, `7071071`), which kept a hardcoded list of
  prefixes.

## Context

Five scripts globbed the literal `202615_*`. When u_crawler started exporting
`202625_*` courses, they were skipped without any error. Replacing the
literal with a list (`("202615_", "202625_")`) only moves the hardcode: every
new term still needs a code change and an image rebuild, and dropping an old
term from the list makes its canonical items look deleted, which deletes
them from Google.

During the same incident, `run-sync.sh` printed `[caldir] done` after a run in
which the VTODO bridge restored 57 tasks and phase 2 deleted the same 57 again
(see [docs/incidents/2026-10-oauth-cron-sync.md](../incidents/2026-10-oauth-cron-sync.md)).
Nothing checked that the run had actually converged.

`merge-ucrawler.py` also removed every canonical `assignment-*.ics` missing
from the u_crawler export, so an empty or half-written export would have read
as "every assignment was deleted in Canvas".

## Decision

1. **Structural discovery.** `caldir/courses.py` is the only place that knows
   what a course is:
   - a *source course* is a top-level directory under the calendar root with a
     `deadlines/` or `windows/` subdirectory holding at least one `.ics`;
   - a *canonical collection* is a directory named `<course>__deadlines` or
     `<course>__windows`;
   - course names must match `[A-Za-z0-9][A-Za-z0-9._-]*` (they end up in
     Radicale URLs and TOML) and never end in `__deadlines`/`__windows`.
   No academic term code appears in any pipeline script; `scripts/check.sh`
   step 4 and `tests/test_repo_invariants.py` reject one. A manifest written by
   u_crawler was considered and rejected: u_crawler is an external image
   (ADR-0005) and its directory layout already is the contract.
2. **Old terms stay.** The bridges read every canonical collection, whether or
   not its source still exists. A course disappearing from the export is not a
   deletion. Removing an old term from Google is a deliberate manual step.
3. **Absence is not deletion** in `merge-ucrawler.py`: an empty or missing
   export removes nothing; removing more than half of a course's managed files
   in one run (minimum 2) is held back with a warning unless
   `MERGE_ALLOW_MASS_DELETE=1`; two export files with the same UID abort the
   merge before anything is written.
4. **Convergence check.** `verify-sync.py` runs after phase 2 as the last
   step of `run-sync.sh`. Every canonical managed UID inside caldir's default
   sync window (±365 days) must be present in its Google mirror carrying the
   provider id caldir writes back after a push (`X-GOOGLE-TASK-ID`,
   `X-GOOGLE-EVENT-ID`). Otherwise it exits 3 and the run does not print
   `done`.
5. No empty collections: Radicale collections and canonical directories are
   created only for exports with at least one `.ics`.

The bridge authority rules (ADR-0004) are unchanged, including "a task deleted
in Google is restored while its canonical assignment exists". What changed
is that the restore now sticks, because caldir forgets a sync base once a
delete is applied (fork commit `c157a8e`, pinned through ADR-0010).

## Consequences

- A new term is picked up on the next run with no code change or rebuild.
- `run-sync.sh` gains a step after phase 2; the order of the existing steps
  is unchanged.
- A legitimate mass removal in Canvas needs one run with
  `MERGE_ALLOW_MASS_DELETE=1`.
- A course whose export becomes empty keeps its canonical items until they
  are removed by hand.
- `dedupe-vtodo.py` imports `courses.py`; copy both when using it.

## Evidence

- `caldir/courses.py`, `caldir/verify-sync.py`
- `caldir/merge-ucrawler.py` — `check_unique_uids`, `removal_allowed`
- `tests/test_courses.py`, `tests/test_pipeline_cycle.py`
