# Sync semantics

Who wins, in every case. Three programs write calendar data, and each has a
different authority rule:

- `merge-ucrawler.py` — u_crawler wins on content, the local file wins on user
  state (`STATUS`, `COMPLETED`, `PERCENT-COMPLETE`).
- `bridge-vtodo.py` — two-way for a small set of shared fields; ambiguity is a
  conflict, never a guess.
- `bridge-windows.py` — canonical wins, always; Google edits are overwritten.

For where these run in the pipeline, see
[Data flow](architecture.md#data-flow).

## merge-ucrawler.py

Source: `<course>/deadlines/` and `<course>/windows/`.
Target: `<course>__deadlines/` and `<course>__windows/`.
Root is `CALENDAR_ROOT`, default `/data/calendars`.

`caldir/courses.py` discovers a source course structurally: it is a top-level
directory whose `deadlines/` or `windows/` export contains at least one `.ics`.
Course names must match `[A-Za-z0-9][A-Za-z0-9._-]*`; no academic term code is
hardcoded. Canonical collections are discovered by suffix independently of the
source export, so old terms remain visible to the bridges. Canonical directories
and Radicale collections are created only for non-empty exports.

### Deadlines (VTODO)

Per source file:

| Case | Action |
| --- | --- |
| target file absent | copy source verbatim, count as created |
| target file present | `merge_vtodo(source, existing)`; write only if the result differs from the existing content |

`merge_vtodo` rebuilds the VTODO body from the **source**, dropping every source
line whose property name is in `USER_STATE_FIELDS`, and appends the **existing**
file's lines for those same fields:

```
USER_STATE_FIELDS = {"STATUS", "COMPLETED", "PERCENT-COMPLETE"}
```

Those three are what Thunderbird or Google Tasks write when the user ticks a
task off. Everything else — `SUMMARY`, `DESCRIPTION`, `DUE`, `URL`, and any
other property — comes from u_crawler and overwrites whatever was in the
canonical file.

Lines outside the `BEGIN:VTODO` / `END:VTODO` range are taken from the source
as well, so calendar-level properties follow u_crawler.

A `merge_vtodo` failure (for example, no VTODO component in either file) is
re-raised as `RuntimeError: failed merging <source> -> <target>: ...`, which
aborts the run.

Existing content is read with `read_bytes()` before UTF-8 decoding, so universal
newline conversion cannot make an unchanged CRLF file look different. An
unchanged merge is not rewritten and is not counted as `updated`.

### Windows (VEVENT)

No field-level merge: if the target is absent or its content differs from the
source, the source overwrites it. Delivery windows carry no user state worth
preserving. This comparison also starts from `read_bytes()` and preserves line
endings.

### Deletion rule

Both halves consider a canonical file for deletion only when **both** hold:

1. its name starts with `assignment-`, and
2. no file of that name exists in the non-empty source export.

An empty or missing export is not a deletion and removes nothing. In addition,
a run that would remove more than half of a course's managed files (minimum 2)
is held back with `WARNING holding N/M removals` unless
`MERGE_ALLOW_MASS_DELETE=1` is set for that run.

Any other `.ics` in a canonical directory — a personal event, a hand-written
task — is never removed. The Spanish comment states the intent directly:
"Sólo eliminamos archivos pertenecientes al esquema de u_crawler."

Before each kind is merged, `check_unique_uids` scans all of its source export
files. Two files carrying the same UID abort with `duplicate u_crawler UID`
before that kind writes anything.

### Writes

`atomic_write` writes `<name>.ics.tmp` and `os.replace()`s it over the target,
creating parent directories as needed.

## bridge-vtodo.py

Canonical `<course>__deadlines/` <-> the Google Tasks mirror directory,
default `<CALENDAR_ROOT>/unab` (`GOOGLE_TASKS_DIR`,
`caldir/bridge-vtodo.py:23-28`).

Only UIDs starting with `u_crawler-todo-` are considered
(`caldir/bridge-vtodo.py:37`, `:387`, `:413`). Personal Google tasks — whose
UIDs use `@google-tasks.com` — are skipped and never touched.

### Field sets

| Set | Fields | Direction |
| --- | --- | --- |
| `COMMON_FIELDS` (`:39-45`) | `SUMMARY`, `DESCRIPTION`, `DUE`, `STATUS`, `COMPLETED` | both ways |
| `RICH_FIELDS` (`:47-57`) | `PRIORITY`, `URL`, `DTSTART`, `PERCENT-COMPLETE`, `LOCATION`, `ORGANIZER`, `ATTENDEE`, `ATTACH`, `VALARM` | canonical -> Google only |
| non-Google `X-` properties | any `X-…` not starting with `X-GOOGLE-` | canonical -> Google only (`:342-347`, `:358-363`) |
| `IGNORE_FOR_HASH` (`:59-63`) | `DTSTAMP`, `LAST-MODIFIED`, `SEQUENCE` | excluded from change detection |

`RICH_FIELDS` are one-way because Google Tasks cannot represent them: they are
carried in the local mirror file so a CalDAV client reading it still sees them,
but a change to them on the Google side is meaningless and is discarded on the
next push.

`copy_rich_fields_to_google` (`:327-372`) rebuilds the Google-side body by
dropping every `RICH_FIELDS` line and every non-`X-GOOGLE-` `X-` line, then
appending the canonical versions of both.

### X-GOOGLE- handling

`X-GOOGLE-*` properties are Google's own bookkeeping. They are:

- excluded from `semantic_hash` (`:171-172`), so their presence or churn never
  registers as a Google-side change;
- preserved on the Google side by `copy_rich_fields_to_google`, which strips
  other `X-` properties but keeps `X-GOOGLE-` ones (`:342-347`);
- never copied to the canonical side (`:358-363` skips them when pushing).

### DUE normalisation

Google Tasks stores due dates at day granularity, canonical VTODOs usually
carry a datetime. The bridge normalises in both directions rather than treating
the difference as a change.

**Canonical -> Google** (`google_due_from_canonical`, `:217-225`): take the
leading `YYYYMMDD` of the canonical `DUE` value and emit
`DUE;VALUE=DATE:YYYYMMDD`. If the value has no leading 8-digit date, the line is
passed through unchanged.

**Google -> canonical** (`canonical_due_from_google`, `:228-261`): take the
date from the Google line, then

| Existing canonical `DUE` | Result |
| --- | --- |
| absent | `DUE;VALUE=DATE:<new date>` |
| has `VALUE=DATE` | same prefix, new date |
| a datetime (`^\d{8}(T.*)$`) | same prefix, new date, **original time-of-day kept** |
| anything else | `DUE;VALUE=DATE:<new date>` |

The code comment gives the concrete case: canonical `20260830T235900Z` with a
Google move to 1 September becomes `20260901T235900Z` (`:247-251`). Moving a
task in Google moves the day; the 23:59 deadline time from Canvas survives.

### shared_signature vs semantic_hash

Two different hashes, doing two different jobs.

`semantic_hash` (`:164-179`) covers **all** properties of the component except
`IGNORE_FOR_HASH` and `X-GOOGLE-*`. It answers "did this side change since the
last run?" — it is compared against the hash stored in the state file.

`shared_signature` (`:193-214`) covers **only** `COMMON_FIELDS`, with `DUE`
reduced to `DUE-DATE:YYYYMMDD` so a datetime and a date-only value with the same
day compare equal. It answers "do the two sides agree on what a user would call
the same task?" — it is compared between canonical and Google.

The pairing is what keeps the bridge quiet: a run where both sides changed but
`shared_signature` still matches (typically Google rewriting its own
representation) is resolved by pushing rich fields, not raised as a conflict.

### Decision table

`reconcile()` (`:525-822`). `left` = canonical, `right` = Google mirror,
`old` = the state entry for this UID. "changed" means `semantic_hash` differs
from the hash recorded in `old`.

| # | State entry | Canonical | Google | Action |
| --- | --- | --- | --- | --- |
| 1 | any | absent | absent | skip (`:548`) |
| 2 | none | present | absent | `seed_google`: copy the canonical file into the mirror, then project `COMMON_FIELDS` with a date-only `DUE`. Logs `[bridge] seed Google <- <uid>` (`:553-566`, `:499-522`) |
| 3 | none | absent | present | CONFLICT `managed task exists only in Google and has no canonical course` (`:569-580`) |
| 4 | none | present | present, `shared_signature` differs | CONFLICT `first observation differs on shared fields` (`:585-593`) |
| 5 | none | present | present, `shared_signature` equal | copy rich fields to Google; record state (`:595-614`) |
| 6 | exists | absent | present, unchanged | delete the Google mirror file, record the UID as absent on both sides. Logs `[bridge] delete Google <- <uid>` (`:643-669`) |
| 7 | exists | absent | present, changed | CONFLICT `deleted canonically but changed in Google` (`:644-652`) |
| 8 | exists | present, unchanged | absent | re-seed Google. The canonical assignment is **never** deleted in response to a Google deletion. Logs `[bridge] restore Google task <- <uid> (canonical assignment still exists)` (`:672-706`) |
| 9 | exists | present, changed | absent | CONFLICT `deleted in Google but changed canonically` (`:673-681`) |
| 10 | exists | unchanged | unchanged | skip (`:711-712`) |
| 11 | exists | changed | changed, `shared_signature` differs | CONFLICT `both sides changed shared fields` (`:714-723`) |
| 12 | exists | changed | changed, `shared_signature` equal | copy rich fields to Google; record state (`:725-746`) |
| 13 | exists | changed | unchanged | project `COMMON_FIELDS` canonical -> Google (date-only `DUE`), then rich fields. Logs `[bridge] Google <- canonical <uid>` (`:748-779`) |
| 14 | exists | unchanged | changed | merge `COMMON_FIELDS` Google -> canonical (time-of-day preserved), then push rich fields back into the mirror. Logs `[bridge] canonical <- Google <uid>` (`:781-813`) |

Row 8 is the deliberate asymmetry, stated in the code comment at `:683-689`: a
hard delete in Google must not erase the Canvas-derived source of truth. To
drop a task, complete or cancel it instead — `STATUS` and `COMPLETED` are
`COMMON_FIELDS` and travel back to canonical through row 14.

A duplicate managed UID on either side raises `RuntimeError`
(`:390-394`, `:415-420`) — see [Exit codes](#exit-codes).

## bridge-windows.py

Canonical `<course>__windows/` -> the Google Calendar mirror directory, default
`<CALENDAR_ROOT>/unab-windows` (`GOOGLE_WINDOWS_DIR`,
`caldir/bridge-windows.py:22-27`). Managed prefix: `u_crawler-window-`
(`:36`).

**Canonical is authoritative.** There is no field-level two-way merge and no
`COMMON_FIELDS` set. Every write direction is canonical -> Google, via
`merge_canonical_into_google`, which takes the entire canonical VEVENT body and
re-attaches the `X-GOOGLE-*` lines from the Google file, plus the
Google-assigned properties in `GOOGLE_ASSIGNED` (`ORGANIZER`) when the
canonical file does not set them. Any other Google-side edit is discarded.
Keeping `ORGANIZER` matters: caldir writes it back after every push but never
sends it, so dropping it re-armed an update for every window on every run
(the `Pushed: 57 updated` symptom of the 2026-10 incident). The code says so at `:495-496`:

```
# u_crawler/Canvas is authoritative for delivery windows.
# Manual Google edits are overwritten intentionally.
```

`semantic_hash` (`:145-160`) is identical in shape to the VTODO one: all
properties except `IGNORE_FOR_HASH` (`DTSTAMP`, `LAST-MODIFIED`, `SEQUENCE`),
`GOOGLE_ASSIGNED` and `X-GOOGLE-*`. Because the hash rule changed, the first
run after the upgrade rewrites each window's mirror once.

### Decision table

`reconcile()` (`:340-552`).

| # | State entry | Canonical | Google | Action |
| --- | --- | --- | --- | --- |
| 1 | any | absent | absent | skip (`:361-362`) |
| 2 | none | present | absent | `seed_google`: copy the canonical file into the mirror verbatim. Logs `[windows-bridge] seed Google <- <uid>` (`:365-381`, `:308-316`) |
| 3 | none | absent | present | CONFLICT `managed event exists only in Google` (`:383-391`) |
| 4 | none | present | present | `merge_canonical_into_google`; write only if it differs (`:393-417`) |
| 5 | exists | absent | present, unchanged | delete the Google mirror file. Logs `[windows-bridge] delete Google <- <uid>` (`:442-468`) |
| 6 | exists | absent | present, changed | CONFLICT `deleted canonically but changed in Google` (`:443-451`) |
| 7 | exists | present | absent | re-seed Google unconditionally — no change check on the canonical side, unlike the VTODO bridge. Logs `[windows-bridge] restore Google <- <uid>` (`:470-486`) |
| 8 | exists | unchanged | unchanged | skip (`:491-492`) |
| 9 | exists | unchanged | changed | overwrite Google from canonical. Logs `[windows-bridge] canonical -> Google <uid>` (`:494-522`) |
| 10 | exists | changed | changed or unchanged | overwrite Google from canonical. Logs `[windows-bridge] Google <- canonical <uid>` (`:524-550`) |

Row 10 catches both "only canonical changed" and "both changed" — because
canonical wins either way, the two need no distinction. That is why this bridge
emits only two CONFLICT messages where the VTODO bridge emits five.

## dedupe-vtodo.py

A **manual** repair tool. It is not COPY'd into the image and `run-sync.sh`
never calls it. To use it, run it by hand inside the container with both
`dedupe-vtodo.py` and its `courses.py` dependency mounted or copied in.

Paths are hardcoded with no environment overrides:

- root `/data/calendars`
- quarantine `/data/.local/share/caldir/quarantine-vtodo`

For each `<course>__deadlines/` directory it groups `.ics` files by managed UID,
matched against `^UID:(u_crawler-todo-(\d+)@u-crawler\.local)\s*$`, and processes
only groups holding more than one file.

### Keeper selection

The assignment id is extracted from the UID, giving the expected filename
`assignment-<id>.ics`. Then, in order:

| Condition | Keeper |
| --- | --- |
| `<canonical dir>/assignment-<id>.ics` exists | that file |
| otherwise, `<course>/deadlines/assignment-<id>.ics` exists | copied (`shutil.copy2`) into the canonical directory; that copy becomes the keeper. `merge-ucrawler.py` reconciles its fields on the next run |
| neither exists | **fail closed**: print `[dedupe] SKIP <uid>: authoritative source not found` and leave the whole group untouched |

The fail-closed branch is the point of the tool: with no authoritative
u_crawler source, it refuses to pick a winner rather than guessing.

Every non-keeper file in the group is `shutil.move`d to
`/data/.local/share/caldir/quarantine-vtodo/<course>/<assignment_id>/`, with a
`.1`, `.2`, … suffix inserted if a name is already taken there.
Nothing is deleted. The script always returns 0.

## Phase 2 and convergence

The caldir fork forgets a sync base after an applied delete. When a bridge
restores an item whose Google copy was deleted, phase 2 therefore treats the
restoration as a Create instead of interpreting it as another remote deletion
and deleting the local file again. The bridge authority rules are unchanged.

`verify-sync.py` then checks every managed canonical UID within caldir's default
±365-day sync window. A matching Google mirror file must exist and carry
`X-GOOGLE-TASK-ID` for a deadline or `X-GOOGLE-EVENT-ID` for a window. Items
outside that window are skipped. The check is read-only and runs after phase 2;
`[caldir] … done` is printed only when it passes.

## Exit codes

Both bridges return `2 if conflicts else 0`. A crash — most commonly a
duplicate-UID `RuntimeError` — propagates as a Python traceback and the usual
interpreter exit code 1.

| Code | Meaning | Effect on `run-sync.sh` |
| --- | --- | --- |
| 0 | reconciliation completed, no conflicts | pipeline continues |
| 2 | at least one conflict; every non-conflicting UID was still reconciled and its state recorded | the run logs `[caldir] VTODO bridge failed/conflicted (rc=2)` (or `windows bridge …`) to stderr and exits 2 — **phase 2 never runs** |
| other | hard failure: unparseable ICS, missing Google mirror directory, duplicate managed UID | same handling — the run exits with that code before phase 2 |

Both bridges call `save_state()` before returning, so a conflicting run still
persists progress for everything it did reconcile. A crashing run does not reach
`save_state`, so no state is written at all.

Because phase 2 is what publishes, a single conflict in either bridge blocks
publication of **all** calendars for that run, including ones with nothing wrong.
Conflicts are sticky: they recur every 15 minutes until resolved. See
[Pipeline never reaches phase 2](troubleshooting.md#pipeline-never-reaches-phase-2).

`verify-sync.py` returns 0 when every expected item is published and 3 when any
item is missing from its mirror or lacks its provider id. Exit 3 ends the run
without `[caldir] … done`; because verification is last, it does not roll back
changes already published by phase 2. See
[Verification fails with exit 3](troubleshooting.md#verification-fails-with-exit-3).

## State files

Both bridges keep a JSON file recording, per UID, what each side looked like at
the end of the last successful reconciliation. That record is the only reason
the bridges can tell "changed" from "unchanged".

| Bridge | Default path | Env override | `version` |
| --- | --- | --- | --- |
| `bridge-vtodo.py` | `/data/.local/share/caldir/vtodo-bridge-state.json` | `BRIDGE_STATE_FILE` (`:30-35`) | 2 |
| `bridge-windows.py` | `/data/.local/share/caldir/windows-bridge-state.json` | `WINDOWS_BRIDGE_STATE_FILE` (`:29-34`) | 1 |

Both live under `./caldir/state` on the host and are gitignored.

Schema (`caldir/bridge-vtodo.py:474-492`, `caldir/bridge-windows.py:319-337`):

```json
{
  "version": 2,
  "items": {
    "u_crawler-todo-12345@u-crawler.local": {
      "canonical_hash": "<sha256 hex>",
      "google_hash": "<sha256 hex>",
      "seen_at": "2026-08-26T12:00:00+00:00"
    }
  }
}
```

Either hash is `null` when that side was absent at record time. `seen_at` is a
UTC ISO-8601 timestamp and is informational — nothing reads it back. Files are
written sorted and indented, atomically via a `.tmp` sibling
(`bridge-vtodo.py:453-471`).

**Missing file** — treated as empty state at the declared version
(`bridge-vtodo.py:428-432`, `bridge-windows.py:215-219`).

**Version mismatch** — a file whose `version` is not the expected value is
discarded wholesale and replaced by empty state
(`bridge-vtodo.py:443-448`, `bridge-windows.py:230-234`). Nothing is migrated,
and no warning is printed. Every UID is then a first observation, which for a
task present on both sides means row 4/5 of the VTODO table: it passes silently
if the shared fields agree, and raises `first observation differs on shared
fields` if they do not.

**Unreadable file** — a parse error raises
`RuntimeError: could not read bridge state <path>: ...` and the run fails
(`bridge-vtodo.py:434-441`).

**Deleting one item's entry** makes the next run treat that UID as a first
observation while leaving every other UID's history intact. That is the
narrowest available reset, and the standard remedy for a stuck conflict —
see [Bridge conflicts](troubleshooting.md#bridge-conflicts).
