# Context handoff

What an agent needs to know before touching anything in this repository. Read
this with [`AGENTS.md`](../AGENTS.md) and [`docs/decisions.md`](decisions.md).

## Current state

The repository has exactly one tracked commit predating this bootstrap:
`4124b05 chore: bootstrap Vassago calendar sync stack`. It tracks twelve files:
`compose.yaml`, `.gitignore`, `radicale/config/config`, and nine files under
`caldir/`.

The runtime host is a separate machine that is already running this stack, with
its own gitignored state directories (`.env`, `radicale/config/users`,
`radicale/data/`, `caldir/config/`, `caldir/state/`, `caldir/calendars/`,
`u-crawler/config/`). Nothing in the repository names it, and nothing here
describes its state. It must keep working: a change that would require an
operator to intervene on that host is a breaking change, whatever it does to the
tests.

This bootstrap added documentation, validation, and skills only. **Zero tracked
files were modified.** The behaviour of the stack today is exactly the behaviour
of commit `4124b05`.

## Known fragilities (documented, deliberately not fixed here)

Each of these is real, each is understood, and none was touched. Do not "fix"
one as a drive-by; each needs its own approved change.

- **The `caldir` fork clone is unpinned.** `caldir/Dockerfile:10-14` clones
  `--branch vtodo-support --depth 1`, so two builds of the same Dockerfile can
  produce different binaries and nothing records which commit is in a given
  image. See [ADR-0006](adr/0006-caldir-built-from-vtodo-support-branch.md).
- **The term prefix `202615_` is hardcoded in five files.** They must change in
  lockstep or the pipeline silently half-migrates:
  `caldir/provision-radicale.sh:79`, `caldir/merge-ucrawler.py:140,201`,
  `caldir/dedupe-vtodo.py:36`, `caldir/bridge-vtodo.py:379,827`,
  `caldir/bridge-windows.py:167,558`.
- **`caldir/Dockerfile:68` has a trailing space after the line-continuation
  backslash.** The Dockerfile parser tolerates it and the image builds; linters
  flag it. Changing it is a lint-only edit and needs its own approval.
- **A personal account is the in-code default for `GOOGLE_ACCOUNT`**
  (`caldir/provision-google.py:30-33`). It is not a secret, but it is personal
  data sitting in a tracked file, and it is also not wired through
  `compose.yaml` — the container uses the default. Do not copy the value into
  any new file.
- **A host-specific address is hardcoded in `compose.yaml:10`** for the Radicale
  port publication, which ties the Compose file to one host's network layout. Do
  not copy the value into any new file.
- **`caldir/dedupe-vtodo.py` has hardcoded paths and is not in the image.**
  `ROOT` and `QUARANTINE` are literals with no environment override
  (`caldir/dedupe-vtodo.py:11-12`), and the Dockerfile does not `COPY` it
  (`caldir/Dockerfile:47-53`). It runs by hand, against paths that only exist
  inside a container or on a host laid out the same way.
- **`merge-ucrawler.py` rewrites every canonical deadline file on every run.**
  `merge_vtodo` emits CRLF, but the "did it change?" comparison is against
  `target_path.read_text()`, which has already collapsed CRLF to LF
  (`caldir/merge-ucrawler.py:166-181`). The two never compare equal, so each
  deadline counts as "updated" on every run and its file is rewritten with
  identical bytes. Content is stable; only the mtime and the `updated` counter
  in the `[u_crawler-merge]` log line are misleading. Pinned by
  `tests/test_merge_ucrawler.py` (`test_merge_rewrites_every_run_because_of_line_endings`).
- **A bridge state-file version mismatch silently starts fresh.**
  `caldir/bridge-vtodo.py:444-448` (version 2) and
  `caldir/bridge-windows.py:230-234` (version 1) discard an unrecognised state
  file and return an empty one, with no warning. The next run then treats every
  item as a first observation, re-seeding everything and raising a conflict for
  any managed item that exists only in Google.

## Assumptions

These are inferences, not verified facts. Do not restate them as facts.

- **Podman on the runtime host is inferred, not proven.** The `:Z` / `:z` / `:U`
  volume flags and the `localhost/` image prefix are Podman/SELinux idioms, but
  nothing states the runtime. This development machine has only Docker.
- **The meaning of `202615` is assumed** to be an academic term identifier.
  Nothing in the repository defines it.
- **The licences of the `belcaik/caldir` fork and of `u_crawler` are unknown.**
  Do not claim a licence for either.
- **The `u_crawler` output format is inferred from the consumer side only** —
  from what `merge-ucrawler.py`, the two bridges, and `dedupe-vtodo.py` read.
  There is no producer-side specification in this repository.
- **The Radicale `users` file is hand-managed.** Nothing in the repository
  creates or updates it; the assumption is that an operator ran `htpasswd`.

## Open questions for the maintainer

- Should the host address in `compose.yaml:10` and the personal default in
  `caldir/provision-google.py:30-33` stay in tracked files, or move to `.env` /
  environment overrides?
- Is `caldir/dedupe-vtodo.py` being outside the image intentional (a deliberate
  break-glass tool) or an oversight?
- Where should CI run, given that the meaningful validation needs a container
  runtime and the repository targets a single self-hosted machine?

## Do not

The same rules as [`AGENTS.md`](../AGENTS.md), in short:

- Do not edit or reformat any tracked file: `compose.yaml`, `.gitignore`,
  `radicale/config/config`, anything under `caldir/`.
- Do not add dependencies — no pip, npm or cargo packages, no new packages in
  the Dockerfile. Stdlib Python, POSIX `sh`, git, and optionally a container
  runtime.
- Do not write real secrets, and do not copy the personal account default or the
  host address out of the tracked files into any new file.
- Do not refactor the application, change Compose, change schedules, or change
  runtime behaviour. Document what is.

## Recommended next task

Two things, and nothing else:

1. A `rotate-academic-term` skill that changes the term prefix across all five
   files in lockstep and states what it leaves behind (existing collections and
   bridge state for the previous term).
2. A separately approved, lint-only cleanup of the trailing space at
   `caldir/Dockerfile:68`.
