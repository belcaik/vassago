# Context handoff

What an agent needs to know before touching anything in this repository. Read
this with [`AGENTS.md`](../AGENTS.md) and [`docs/decisions.md`](decisions.md).

## Current state

Course discovery is structural (`caldir/courses.py`, ADR-0009): a top-level
directory with a non-empty `deadlines/` or `windows/` export is a source course,
and canonical collections use the `<course>__<kind>` suffix. No academic term
code is hardcoded. Old canonical collections stay visible to the bridges after
their source disappears.

The merge compares content without universal-newline normalisation, treats an
empty export as no deletion, guards mass removals, and rejects duplicate source
UIDs. `verify-sync.py` is the final pipeline step and can exit 3 instead of
letting a non-converged run print `done`. The image builds caldir from a pinned
commit (ADR-0008); that fork forgets a sync base after an applied delete so a
bridge restore becomes a Create in phase 2.

The runtime host is a separate machine with its own gitignored state directories
(`.env`, `radicale/config/users`, `radicale/data/`, `caldir/config/`,
`caldir/state/`, `caldir/calendars/`, `u-crawler/config/`). See
[docs/migrations.md](migrations.md) for deployment and
[docs/incidents/2026-10-oauth-cron-sync.md](incidents/2026-10-oauth-cron-sync.md)
for the production evidence still needed.

## Known issues and operational constraints

- **Old-term removal is deliberately manual.** A new term needs no code change,
  but canonical collections from old terms do not disappear automatically.
  Removing their Google items is an explicit operator decision.
- **A personal account is the in-code default for `GOOGLE_ACCOUNT`.** It is not
  a secret, but it is personal data in a tracked file and is not wired through
  `compose.yaml`. Do not copy the value into any new file.
- **A host-specific address is hardcoded in `compose.yaml`** for Radicale port
  publication, tying the Compose file to one host's network layout. Do not copy
  the value into any new file.
- **`caldir/dedupe-vtodo.py` has hardcoded paths and is not in the image.** It
  is a manual repair tool only. It imports `courses.py`, so copy both files into
  the container when using it.
- **A bridge state-file version mismatch silently starts fresh.** Unrecognised
  state is discarded without a warning. The next run treats every item as a
  first observation, re-seeding everything and raising a conflict for any managed
  item that exists only in Google.
- **Two production causes remain open.** Why the original Google tasks vanished
  and why delivery windows were not visible require the GET-only
  `diagnose-google.py` evidence described in the incident.

## Assumptions

These are inferences, not verified facts. Do not restate them as facts.

- **Podman on the runtime host is inferred, not proven.** The `:Z` / `:z` / `:U`
  volume flags and the `localhost/` image prefix are Podman/SELinux idioms.
- **The licences of the `belcaik/caldir` fork and of `u_crawler` are unknown.**
  Do not claim a licence for either.
- **The `u_crawler` output format is inferred from the consumer side only.** Its
  directory layout is the structural contract; there is no producer-side
  specification in this repository.
- **The Radicale `users` file is hand-managed.** Nothing in the repository
  creates or updates it; the assumption is that an operator ran `htpasswd`.

## Open questions for the maintainer

- Should the host address in `compose.yaml` and the personal default in
  `caldir/provision-google.py` stay in tracked files, or move to `.env` /
  environment overrides?
- Where should CI run, given that the meaningful validation needs a container
  runtime and the repository targets a single self-hosted machine?
- Should `MERGE_ALLOW_MASS_DELETE` also be required for the bridges'
  canonical-deleted path?

## Do not

The same rules as [`AGENTS.md`](../AGENTS.md), in short:

- Do not add dependencies — no pip, npm or cargo packages, no new packages in
  the Dockerfile. Stdlib Python, POSIX `sh`, git, and optionally a container
  runtime.
- Do not hardcode academic term codes or bypass structural course discovery.
- Do not write real secrets, and do not copy the personal account default or the
  host address out of the tracked files into any new file.
- Do not change bridge authority rules, Compose schedules, or runtime behavior
  without the required ADR.

## Recommended next task

Run `diagnose-google.py` on the production host before and after deploying the
current image, then record counts and id outcomes (never tokens) in the incident.
