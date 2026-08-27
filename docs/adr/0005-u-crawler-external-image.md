# ADR-0005: u_crawler stays an external, pinned image

- Status: Accepted (retroactive — documents a decision already implemented)
- Date: 2026-08-26

## Context

`u_crawler` is the component that logs into the university LMS and extracts
assignments and delivery windows. It carries the LMS-specific scraping logic and
the credentials to reach it — concerns that have nothing to do with calendar
reconciliation, and a change cadence driven by the LMS rather than by this
repository.

This repository is the *integration* half: merge, provision, bridge, publish.

## Decision

`u_crawler` is consumed as a published container image,
`ghcr.io/belcaik/unab-sync-content:0.1.0-beta.1`, pinned to that exact tag. Its
source is developed in its own project and is not vendored, submoduled, or built
here — this repository holds no `u_crawler` code and no `build:` key for it.

Its entire contract with this repository is the shared directory plus the naming
conventions the consumer side depends on:

- Course directories named `202615_<course>` directly under the shared root
  (`/home/appuser/Caldir` inside the crawler; `/data/calendars` inside `caldir`).
- A `deadlines/` subdirectory holding VTODO `.ics` files.
- A `windows/` subdirectory holding VEVENT `.ics` files.
- Files named `assignment-<id>.ics` — the only names the merge is allowed to
  delete, and the name `dedupe-vtodo.py` reconstructs when repairing.
- VTODO UIDs of the form `u_crawler-todo-<id>@u-crawler.local`; VEVENT UIDs
  starting with `u_crawler-window-`. Anything not carrying the managed prefix is
  ignored by the bridges.
- One VTODO (respectively one VEVENT) component per file, since the parsers take
  the first complete component they find.

Runtime configuration lives in `./u-crawler/config` (gitignored) and the crawler
runs on its own daily schedule.

## Consequences

- LMS credentials and scraping logic never enter this repository, its git
  history, or its image.
- The image tag is pinned, so the producer side of the bus is reproducible even
  though the consumer side is not (see ADR-0006).
- The conventions above are unenforced on the producer side. A change to the
  UID scheme, the filename pattern, or the directory layout in a future
  `u_crawler` release breaks the merge or, worse, makes the bridges quietly
  ignore items whose UID prefix no longer matches. Bumping the tag is a
  contract change, not just a version bump.
- Nothing here can fix a crawler bug; a wrong deadline is fixed upstream and
  arrives on the next crawler run.
- The crawler's output is only ever observed from the consumer side. This
  repository's description of the format is inferred from what
  `merge-ucrawler.py`, the bridges, and `dedupe-vtodo.py` read, not from the
  producer's own specification.
- The crawler's licence is not stated anywhere in this repository.

## Evidence

- `compose.yaml:46-49` — the service uses the published image
  `ghcr.io/belcaik/unab-sync-content:0.1.0-beta.1` with no `build:` key.
- `compose.yaml:62-66` — its only volumes: a private config directory and the
  shared calendars directory.
- `compose.yaml:51-60` — `PUID`/`PGID`, timezone, its own daily
  `CRON_SCHEDULE: "0 5 * * *"` and `RUN_ON_START`.
- `.gitignore:17-18` — `u-crawler/config/` is not tracked.
- `caldir/merge-ucrawler.py:140-146,201-207` — the consumer expects
  `202615_*/deadlines` and `202615_*/windows` under the shared root.
- `caldir/merge-ucrawler.py:186-192` — only `assignment-`-prefixed files are
  eligible for deletion.
- `caldir/bridge-vtodo.py:37` — `MANAGED_UID_PREFIX = "u_crawler-todo-"`.
- `caldir/bridge-windows.py:36` — `MANAGED_UID_PREFIX = "u_crawler-window-"`.
- `caldir/bridge-vtodo.py:409-424` — non-managed UIDs in the Google directory are
  skipped; the comment names personal Google tasks as the case.
- `caldir/dedupe-vtodo.py:14-17` — the full managed UID form
  `u_crawler-todo-(\d+)@u-crawler.local`.
- `caldir/dedupe-vtodo.py:63` — the canonical filename `assignment-<id>.ics`.
- `caldir/bridge-vtodo.py:110-123` and `caldir/bridge-windows.py:89-102` — the
  parsers return the first complete `VTODO` / `VEVENT` component in the file.
- The repository tracks no `u_crawler` source: `git ls-files` lists only
  `compose.yaml`, `.gitignore`, `radicale/config/config` and `caldir/*`.
