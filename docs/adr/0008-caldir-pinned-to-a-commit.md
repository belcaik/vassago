# ADR-0008: caldir is built from a pinned commit

- Status: Accepted — supersedes the branch-reference part of ADR-0006
- Date: 2026-10-28

## Context

ADR-0006 built `caldir` with `git clone --depth 1 --branch vtodo-support`. That
clone ran in a cached `RUN` layer whose text never changed, so podman reused it
after the fork moved: the image kept a `caldir-provider-google` without the
`calendar.calendars` OAuth scope even after the fork was fixed, and nothing in
the image said which commit it held.

## Decision

`caldir/Dockerfile` takes `ARG CALDIR_REPO` and `ARG CALDIR_COMMIT` (a full
40-hex SHA), fetches exactly that commit (`git init`, `git fetch --depth 1
origin "$CALDIR_COMMIT"`, `git checkout FETCH_HEAD`) and fails unless
`git rev-parse HEAD` equals it. Bumping the fork means editing the default of
`CALDIR_COMMIT` (or passing `--build-arg`); that changes the layer key and
rebuilds, while an unchanged value reuses the compiled layer.

The build fails unless `caldir-provider-google` contains all four OAuth scope
strings the pipeline needs (`calendar.calendarlist.readonly`,
`calendar.events`, `calendar.calendars`, `tasks`). Build, scope check, copy
and `cargo clean` share one `RUN`, so `target/` is never cached. The image
carries `/usr/local/share/caldir/BUILD_INFO` (repo, commit, `rustc` version,
sha256 of each binary, `VASSAGO_REVISION`) and the labels
`org.opencontainers.image.revision` and `io.vassago.caldir.commit`.

Everything else in ADR-0006 stands: two stages, six binaries, no `build:` key,
local-only `localhost/calendar-caldir:vtodo-support` tag.

## Consequences

- Rebuilds are reproducible from the Dockerfile alone; a running container
  answers "which commit?" with `cat /usr/local/share/caldir/BUILD_INFO` or
  `podman inspect`.
- Picking up a fork fix is an explicit commit to this repository.
- The fork must keep the pinned commit reachable; GitHub serves
  fetch-by-SHA for any commit reachable from a ref.
- `scripts/check.sh` step 6 and `tests/test_repo_invariants.py` reject a
  non-SHA `CALDIR_COMMIT`, any `--branch`, and a continuation backslash
  followed by whitespace.

## Evidence

- `caldir/Dockerfile` — `ARG CALDIR_COMMIT`, the fetch/verify `RUN`, the
  build/scope-check/`cargo clean` `RUN`, `BUILD_INFO` and `LABEL`.
- `scripts/check.sh` — step 6.
