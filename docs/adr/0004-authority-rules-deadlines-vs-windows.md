# ADR-0004: Split authority — deadlines negotiate, windows do not

- Status: Accepted (retroactive — documents a decision already implemented)
- Date: 2026-08-26

## Context

The two kinds of data this stack syncs look alike on disk — ICS files under a
`202615_<course>` directory — but they mean different things to the user.

A *deadline* is an assignment (VTODO). The LMS owns what it says: title,
description, due date. The user owns what they have done about it: whether it is
started, finished, or how far along it is. Those two owners write to the same
object, from different places (the LMS via `u_crawler`, the user via Thunderbird
against Radicale or via Google Tasks).

A *window* is a delivery availability period (VEVENT). It describes when the LMS
will accept a submission. Nothing the user does changes that; a user edit to a
window is a note-to-self at best, and a false record at worst.

## Decision

Deadlines and windows get different authority rules.

**Deadlines — LMS owns content, the user owns task state, shared fields are
bidirectional.**

`merge-ucrawler.py` regenerates each canonical VTODO from the `u_crawler` source
on every run, but before writing it strips `STATUS`, `COMPLETED` and
`PERCENT-COMPLETE` out of the source body and re-attaches the values found in the
existing canonical file. The comment says why: those fields can be modified by
Thunderbird or Google, and `u_crawler` must not stamp over them on every
regeneration.

`bridge-vtodo.py` then treats a set of fields as *shared* and bidirectional:
`SUMMARY`, `DESCRIPTION`, `DUE`, `STATUS`, `COMPLETED`. Whichever side changed
since the last observation wins for those fields — canonical-to-Google when the
canonical side changed, Google-to-canonical when Google changed. A second set of
fields (`PRIORITY`, `URL`, `DTSTART`, `PERCENT-COMPLETE`, `LOCATION`,
`ORGANIZER`, `ATTENDEE`, `ATTACH`, `VALARM`, and non-Google `X-` properties)
flows canonical-to-Google only, because Google Tasks does not round-trip them.
When both sides changed but their shared signatures agree, the change is not a
disagreement and only the rich fields are pushed. When both changed and the
shared signatures differ, it is a conflict.

Deletion is asymmetric on purpose. A hard delete in Google of a task whose
canonical assignment is unchanged does **not** delete the canonical assignment:
the bridge re-seeds the Google file from the canonical one. The comment states
the rule — a Google deletion is to be expressed as completed/cancelled task
state through normal edits, and a hard delete must not erase the Canvas-derived
source of truth.

**Windows — the LMS is authoritative, Google edits are overwritten.**

`bridge-windows.py` has no shared-field concept and no Google-to-canonical path.
Whenever the two sides differ it calls `merge_canonical_into_google`, which
rebuilds the Google file from the canonical VEVENT body and keeps only the
`X-GOOGLE-*` lines from the Google side. The branch that fires when only the
Google side changed says so directly:

> `# u_crawler/Canvas is authoritative for delivery windows.`
> `# Manual Google edits are overwritten intentionally.`

## Consequences

- Ticking an assignment off in Google Tasks or Thunderbird survives the next
  `u_crawler` run and propagates to the other side; a due-date change in the LMS
  reaches both without discarding that state.
- Deleting a task in Google does not remove the assignment; it comes back on the
  next run, by design. Users who expect delete-to-mean-gone will see the task
  reappear.
- Editing a delivery window in Google is silently pointless: the edit is
  overwritten on the next run and no conflict is raised for it. Only the
  `X-GOOGLE-*` properties Google itself attaches are preserved.
- Because the windows bridge never merges Google back, a canonical window and its
  Google copy cannot drift; the only conflicts it can raise are structural — a
  managed event existing only in Google, or a canonical deletion racing a Google
  change.
- The two bridges disagree about a Google-side deletion: `bridge-vtodo.py` treats
  it as a conflict when the canonical side also changed, whereas
  `bridge-windows.py` re-seeds unconditionally. That follows from windows having
  no negotiated state to lose.
- The rich-field set flowing one way means Google is a lossy view of a deadline.
  The canonical collection, not Google, is what a repair should be based on.

## Evidence

- `caldir/merge-ucrawler.py:11-17` — `USER_STATE_FIELDS` = `STATUS`, `COMPLETED`,
  `PERCENT-COMPLETE`, with the comment that Thunderbird/Google may modify them
  and `u_crawler` must not overwrite them.
- `caldir/merge-ucrawler.py:96-118` — `merge_vtodo` keeps the source body but
  drops the user-state properties from it and re-appends the existing ones.
- `caldir/merge-ucrawler.py:159-182` — the canonical deadline file is otherwise
  rewritten from source on every run.
- `caldir/merge-ucrawler.py:215-224` — windows are overwritten wholesale whenever
  the source differs; no field is preserved.
- `caldir/bridge-vtodo.py:39-45` — `COMMON_FIELDS` (bidirectional shared set).
- `caldir/bridge-vtodo.py:47-57` — `RICH_FIELDS` (canonical-to-Google only).
- `caldir/bridge-vtodo.py:748-780` — canonical changed: shared fields projected to
  Google, then rich fields.
- `caldir/bridge-vtodo.py:781-813` — Google changed: shared fields merged into
  canonical with the Google-to-canonical `DUE` rule, then rich fields pushed back.
- `caldir/bridge-vtodo.py:714-746` — both sides changed: equal shared signatures
  means rich-field copy only, differing signatures means conflict.
- `caldir/bridge-vtodo.py:228-261` — `canonical_due_from_google` keeps the
  canonical time-of-day when Google returns a date-only `DUE`.
- `caldir/bridge-vtodo.py:672-706` — Google deletion with an unchanged canonical
  side re-seeds Google; the comment states that a hard delete must not erase the
  Canvas-derived source of truth.
- `caldir/bridge-windows.py:494-522` — the Google-only-changed branch, carrying
  the comment "u_crawler/Canvas is authoritative for delivery windows. Manual
  Google edits are overwritten intentionally."
- `caldir/bridge-windows.py:264-305` — `merge_canonical_into_google` rebuilds the
  Google body from canonical and retains only `X-GOOGLE-*` lines.
- `caldir/bridge-windows.py:470-486` — Google deletion re-seeds unconditionally,
  with no conflict branch.
