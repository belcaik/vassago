# ADR-0003: Two-phase sync with fail-closed bridges

- Status: Accepted (retroactive — documents a decision already implemented)
- Date: 2026-08-26

## Context

Three parties write to the same set of assignments: the LMS (through
`u_crawler`), the user through a CalDAV client against Radicale, and the user
through Google Tasks / Google Calendar. `caldir sync` moves files between the
local collections and the remote providers, but it has no notion of the
relationship between a canonical course collection and its Google mirror — that
relationship lives in `bridge-vtodo.py` and `bridge-windows.py`, which reconcile
two local directories against a stored per-UID hash pair.

Reconciling requires the bridges to see the *current* remote state, and their
results only reach the remotes if something publishes afterwards. A single
`caldir sync` cannot do both.

## Decision

`run-sync.sh` runs a fixed pipeline, under `set -eu`, in this order:

1. `provision-radicale.sh`
2. `provision-google.py`
3. `merge-ucrawler.py`
4. `caldir sync` — "phase 1: sync providers"
5. `bridge-vtodo.py`
6. `bridge-windows.py`
7. `caldir sync` — "phase 2: publish bridge results"

Phase 1 pulls provider state down into the local directories. The bridges then
reconcile entirely on the local filesystem — they never talk to Google or
Radicale — and phase 2 publishes whatever they wrote.

The bridges are fail-closed. Each returns exit code 2 when it counted any
conflict. `run-sync.sh` captures the return code of each bridge explicitly
(temporarily disabling `set -e` around the call so the code can be read) and, if
it is non-zero, prints a message and exits with that same code. Phase 2 is
therefore never reached: a conflicted run publishes nothing.

## Consequences

- The bridges reason about a consistent snapshot: everything they compare came
  down in phase 1 of the same run.
- Bridge output is not lost. Local edits made by the bridges are pushed in phase
  2 of the same run, not deferred to the next cron tick.
- **A single conflict blocks publication for everything.** The bridges are
  whole-run gates, not per-item ones: `bridge-vtodo.py` finishing with one
  conflicted UID stops the pipeline before `bridge-windows.py` even runs, and
  before phase 2 publishes the changes that reconciled cleanly. This is
  deliberate — the alternative is publishing a partially reconciled state whose
  divergence nobody has looked at — but it means one stuck assignment freezes
  outbound sync for every course until a human resolves it.
- The bridges are not atomic with respect to each other. A run that conflicts in
  `bridge-vtodo.py` has already written that bridge's state file and its
  non-conflicting file edits to disk; those edits simply wait for a later,
  clean run to be published.
- Because the cron schedule is every 15 minutes and a failed run exits non-zero
  rather than retrying, an unresolved conflict reproduces itself on every tick,
  and the conflict message reappears in the container logs each time.
- Provisioning runs before every sync, so a newly appearing course is
  provisioned in the same run that first merges it.
- The very first run is invoked directly by the entrypoint with `|| true`, so a
  conflicting first run does not prevent the container from reaching `cron -f`
  and staying up.

## Evidence

- `caldir/run-sync.sh:2` — `set -eu`: any un-guarded step failing aborts the run.
- `caldir/run-sync.sh:4-9` — the `caldir` binary is resolved first; absence is a
  fatal exit 127.
- `caldir/run-sync.sh:13-20` — provisioning (Radicale, Google) then the
  `u_crawler` merge, before any sync.
- `caldir/run-sync.sh:22-24` — phase 1, labelled "sync providers", run from
  `/data/calendars`.
- `caldir/run-sync.sh:26-35` — `bridge-vtodo.py` invoked with `set +e`, its return
  code captured, and a non-zero code re-raised as the pipeline's exit code.
- `caldir/run-sync.sh:37-46` — the same guard for `bridge-windows.py`.
- `caldir/run-sync.sh:48-50` — phase 2, labelled "publish bridge results",
  reachable only after both bridges returned 0.
- `caldir/bridge-vtodo.py:848` — `return 2 if conflicts else 0`.
- `caldir/bridge-windows.py:581` — the same exit contract.
- `caldir/bridge-vtodo.py:16-35` — the bridge reads only local paths
  (`CALENDAR_ROOT`, `GOOGLE_TASKS_DIR`, `BRIDGE_STATE_FILE`); no network client is
  imported.
- `caldir/bridge-windows.py:15-34` — the same, for the windows bridge.
- `caldir/entrypoint.sh:17-20` — the initial run is `run-sync.sh || true`, so a
  conflicted first run still leaves the container running.
- `caldir/entrypoint.sh:6-11` and `compose.yaml:27` — the 15-minute cron entry
  that re-runs the whole pipeline.
