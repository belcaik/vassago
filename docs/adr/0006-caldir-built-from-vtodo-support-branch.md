# ADR-0006: caldir is built here from the `vtodo-support` branch

- Status: Accepted (retroactive — documents a decision already implemented)
- Date: 2026-08-26

## Context

The pipeline needs a `caldir` that can carry VTODO to a CalDAV collection — the
deadline collections are task collections, and the generated collection configs
declare `caldav_supports_tasks = true` (ADR-0002). That capability lives on the
`vtodo-support` branch of `github.com/belcaik/caldir`, a fork maintained by the
same author as this repository. No published image provides it.

## Decision

The `caldir` image is built inside this repository. `caldir/Dockerfile` is a
two-stage build: stage one clones `https://github.com/belcaik/caldir.git` with
`--depth 1 --branch vtodo-support`, removes `rust-toolchain.toml` so the image's
own Rust toolchain is used, builds the whole workspace in release mode, and
collects the `caldir` binary together with the `google`, `outlook`, `icloud`,
`caldav` and `webcal` provider binaries. Stage two is `debian:bookworm-slim`
with `ca-certificates`, `cron`, `curl`, `python3` and `tzdata`, plus the
pipeline scripts.

The build is not wired into Compose. The `caldir` service references
`localhost/calendar-caldir:vtodo-support` as an `image:` and has no `build:` key,
so the image must be built by hand and tagged exactly that. The image is not
published to any registry.

## Consequences

- The clone is a branch reference, not a commit or a tag. Two builds run at
  different times can produce different binaries from the same Dockerfile, with
  nothing recording which commit went into a given image. There is no way, from
  a running container, to tell which revision of `vtodo-support` it holds. That
  cost is real and is accepted here as-is.
- `--depth 1` means the built image contains no history to identify the commit
  after the fact either.
- Removing `rust-toolchain.toml` means the fork's own pinned toolchain is
  deliberately ignored in favour of the `rust:1.97.0-bookworm` base. A future
  fork change that depends on a newer toolchain fails at build time, not at
  runtime.
- Because Compose carries no `build:` key, `up` on a host without the image fails
  rather than building it; the build is a documented manual prerequisite. The
  flip side is that a redeploy never silently rebuilds and swaps the binary.
- The image is local-only, so every host that runs this stack must build it
  independently, and two hosts can end up on different fork revisions under the
  same tag.
- The image tag names the branch, so the tag stays constant across rebuilds and
  gives no version signal at all.
- The whole workspace is built, so providers the stack does not use (Outlook,
  iCloud, webcal) are compiled and shipped, adding build time and image size but
  keeping the image usable if another provider is configured later.
- The fork's licence is not recorded in this repository.

## Evidence

- `caldir/Dockerfile:1` — stage one on `rust:1.97.0-bookworm`.
- `caldir/Dockerfile:10-14` — `git clone --depth 1 --branch vtodo-support
  https://github.com/belcaik/caldir.git`: a moving branch reference, no commit
  pin.
- `caldir/Dockerfile:18-20` — `rm -f rust-toolchain.toml` before building.
- `caldir/Dockerfile:22` — `cargo build --release --workspace`.
- `caldir/Dockerfile:24-30` — the six binaries copied out of the build stage,
  including the unused Outlook, iCloud and webcal providers.
- `caldir/Dockerfile:33-42` — the slim runtime stage and its package set.
- `caldir/Dockerfile:45-53` — provider binaries and the pipeline scripts copied
  into `/usr/local/bin/`.
- `caldir/Dockerfile:71-74` — `ENV HOME=/data`, `WORKDIR /data`, and the
  entrypoint.
- `compose.yaml:20-21` — `image: localhost/calendar-caldir:vtodo-support`, with
  no `build:` key anywhere in the service.
- `caldir/run-sync.sh:4-9` — the pipeline resolves `caldir` from `PATH` and exits
  127 if the binary is missing, which is what a wrongly built or wrongly tagged
  image looks like at runtime.
- `caldir/provision-radicale.sh:68` — `caldav_supports_tasks = true`, the
  capability that requires this branch.
