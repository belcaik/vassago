# ADR-0008: Several academic terms can be processed at once

- Status: Superseded by [ADR-0009](0009-structural-course-discovery.md)
- Date: 2026-10-25

## Context

The term prefix was a single literal (`202615_`) hardcoded in five scripts,
and `scripts/check.sh` required every script to carry exactly one prefix.
When the 2026 second-semester courses arrived from u_crawler as `202625_*`,
the pipeline skipped them without any error.

Swapping the literal from `202615_` to `202625_` is not safe either. Once the
bridges stop scanning the old canonical directories, every old-term UID
becomes "deleted canonically":

- items untouched in Google are deleted from Google Tasks and Calendar;
- items the user completed or edited in Google raise
  `deleted canonically but changed in Google`, the bridge exits 2, and phase 2
  is skipped for every calendar, including the new term
  ([ADR-0003](0003-two-phase-sync-fail-closed-bridges.md)).

A semester change therefore needs a period where both terms are live.

## Decision

Each of the five scripts declares a list of prefixes instead of one literal:
`TERM_PREFIXES = ("202615_", "202625_")` in Python and
`TERM_PREFIXES="202615_ 202625_"` in `provision-radicale.sh`. Every glob that
used the literal now iterates over that list.

The lockstep rule stays, applied to the set: every script must list exactly
the same prefixes. `scripts/check.sh` (step 4) and
`tests/test_repo_invariants.py` enforce set equality instead of a single value.

The list remains a source constant, not an environment variable, so
`compose.yaml` is unchanged and the image stays the only place that decides
which terms are live.

## Consequences

- Rotating terms is a two-step change: add the new prefix when its courses
  appear, and drop the old one only after the old term's mirrors in `unab/` and
  `unab-windows/` have been removed or are no longer wanted. Dropping a prefix
  is the moment old-term items get deleted from Google, as described above.
- A UID that appears under both terms trips the existing duplicate-UID guard,
  which is the desired fail-closed behaviour.
- Any change to the list still requires rebuilding the caldir image, because
  the scripts are `COPY`'d into it.
