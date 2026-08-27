# ADR-0001: Filesystem ICS as the integration bus

- Status: Accepted (retroactive — documents a decision already implemented)
- Date: 2026-08-26

## Context

Two independently developed containers must exchange academic calendar data:
`u-crawler` extracts deadlines and delivery windows from the university LMS, and
`caldir` turns them into canonical collections that are published to CalDAV and
mirrored to Google. The two run on the same host, on the same Compose network,
under the same project.

Any coupling between them has to survive the fact that `u_crawler` is an
external image with its own release cycle (see ADR-0005) and that `caldir` is a
Rust binary built from a fork (see ADR-0006). A shared API or a message queue
would mean a wire protocol both sides implement, version, and keep compatible,
plus a broker to run.

## Decision

The two containers exchange data through a bind-mounted directory of `.ics`
files. `./caldir/calendars` on the host is mounted into `u-crawler` as
`/home/appuser/Caldir` and into `caldir` as `/data/calendars`; nothing else
connects them. `u_crawler` writes `202615_<course>/deadlines/*.ics` (VTODO) and
`202615_<course>/windows/*.ics` (VEVENT); `merge-ucrawler.py` is the only reader
of those directories and copies them into the canonical
`202615_<course>__deadlines` / `202615_<course>__windows` collections.

The bus carries no coordination signal. `u_crawler` runs on its own cron
schedule (`0 5 * * *`) and `caldir` on its own (`*/15 * * * *`); the merge simply
observes whatever is on disk at the moment it runs. Ordering is not enforced,
and no lock is taken.

Writes into the canonical side are atomic: content goes to a `.tmp` sibling and
is moved into place with `os.replace`, so a reader never sees a half-written
`.ics`.

## Consequences

- No shared schema code, no broker, no network protocol between the producer and
  the consumer. Either side can be replaced by anything that reads or writes the
  same files.
- The contract is entirely conventional: directory names, the `202615_` term
  prefix, the `assignment-<id>.ics` filename, and the managed UID prefixes.
  Nothing validates it; a producer that renames a directory silently stops
  being seen.
- Deletion is inferred, not signalled. `merge-ucrawler.py` deletes a canonical
  file only when its name starts with `assignment-` and no longer exists on the
  source side, so any hand-added `.ics` in a canonical collection survives — and
  so a `u_crawler` run that fails halfway and writes fewer files reads as
  deletions.
- Debugging and manual repair are `cat`, `ls`, and a text editor. The quarantine
  flow in `dedupe-vtodo.py` is possible only because the state is plain files.
- The bind mount uses the shared-relabel flag (`:z`) precisely because two
  containers touch the same directory, unlike the private (`:Z`) mounts.

## Evidence

- `compose.yaml:38` and `compose.yaml:66` — the same host directory
  `./caldir/calendars` mounted into both `caldir` (`/data/calendars`) and
  `u-crawler` (`/home/appuser/Caldir`), with the shared `:z` flag; the comment at
  `compose.yaml:65` names it as shared.
- `compose.yaml:20-44,46-69` — the two services declare no ports, no links, and
  no environment referring to each other; the only overlap is the volume.
- `compose.yaml:27,57` — independent cron schedules (`*/15 * * * *` versus
  `0 5 * * *`); no handshake between the producer and the consumer.
- `caldir/merge-ucrawler.py:140-157,201-213` — the reader: globs
  `202615_*/deadlines/*.ics` and `202615_*/windows/*.ics` into
  `202615_*__deadlines` / `202615_*__windows`.
- `caldir/merge-ucrawler.py:121-132` — atomic write via `.tmp` + `os.replace`.
- `caldir/merge-ucrawler.py:184-192,226-232` — deletion restricted to files whose
  name starts with `assignment-`, with the comment stating that other ICS files
  in the collection are left alone.
- `caldir/run-sync.sh:19-20` — `merge-ucrawler.py` is invoked once per pipeline
  run, reading whatever the shared directory currently holds.
