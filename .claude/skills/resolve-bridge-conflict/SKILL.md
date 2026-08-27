---
name: resolve-bridge-conflict
description: Use when the caldir logs show a "[bridge] CONFLICT" or "[windows-bridge] CONFLICT" line, when run-sync.sh exits 2, or when a bridge raises a duplicate-UID RuntimeError.
---

# Resolve a bridge conflict

A bridge refuses to guess. When the two sides of a mirrored item disagree in a
way no rule covers, it logs a `CONFLICT` line, leaves both sides untouched, and
exits 2. `run-sync.sh` then aborts, so **phase 2 never runs** and nothing is
published until a human picks a winner. Every conflict is resolved by editing
exactly one side and re-running.

## 1. Identify the item

From the logs, capture the UID and the message text after it:

```sh
docker compose logs caldir | grep CONFLICT
```

`[bridge]` is `bridge-vtodo.py` (deadlines ↔ Google Tasks). `[windows-bridge]`
is `bridge-windows.py` (windows → Google Calendar). The two have different
authority rules — the prefix decides which section you read next.

## 2. Map the message

Look the exact message text up in
[docs/troubleshooting.md](../../../docs/troubleshooting.md#bridge-conflicts),
which has one subsection per message and names the state that produced it.

## 3. Inspect both sides and the state

Read the item's `.ics` on the canonical side and on the Google mirror side, and
its entry in the bridge state JSON. The three tell you which side changed since
the last successful run. Paths for the state files are in
[docs/sync-semantics.md](../../../docs/sync-semantics.md#state-files); directory
layout is in [docs/architecture.md](../../../docs/architecture.md#directory-layout).

These files live under the gitignored runtime directories. Read them on the
runtime host, and never copy their contents into the repo, a commit, or a
report.

## 4. Decide the winner

Apply the authority rules in
[docs/sync-semantics.md](../../../docs/sync-semantics.md#bridge-vtodopy) and
[docs/sync-semantics.md](../../../docs/sync-semantics.md#bridge-windowspy). The
one outcome no resolution may take: deleting a canonical assignment. If the
Google side is gone or wrong, the fix is to restore Google from canonical, not
to drop the canonical file.

## 5. Apply the change to one side only

Edit or delete on the losing side, then leave the winning side alone. Editing
both sides in the same pass recreates the conflict on the next run, because the
bridge compares each side against the same stored hash.

## 6. Re-run and confirm

```sh
docker compose exec caldir /usr/local/bin/run-sync.sh
```

The run is clean when the log reaches `phase 2: publish bridge results` and
then `[caldir] … done`. Stopping before phase 2 means a conflict remains — go
back to step 1 with the new UID.

## Duplicate UIDs

A bridge raising `RuntimeError: duplicate canonical UID …` (or
`duplicate Google UID …`) is not a conflict: two files claim one managed UID,
and the bridge fails closed rather than picking one. The repair tool is
`caldir/dedupe-vtodo.py`, which keeps `assignment-<id>.ics` and quarantines the
rest — see
[docs/sync-semantics.md](../../../docs/sync-semantics.md#dedupe-vtodopy) and
[docs/troubleshooting.md](../../../docs/troubleshooting.md#duplicate-uids).

`dedupe-vtodo.py` is **not** in the caldir image: `caldir/Dockerfile` does not
COPY it. It also hardcodes its paths, so it must run inside the container
against `/data/calendars`. Getting it in there — copying the file into the
running container or exposing it through a bind mount — is the operator's
choice; ask rather than inventing one. Re-run `run-sync.sh` afterwards.
