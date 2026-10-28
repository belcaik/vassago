---
name: rebuild-caldir-image
description: Use when a script under caldir/ or caldir/Dockerfile changed and the caldir container must be rebuilt, or when the caldir service is running stale code.
---

# Rebuild the caldir image

`compose.yaml` has no `build:` key for `caldir`, so the image is never built
implicitly. It must be built by hand under exactly the tag the compose file
names, or the service keeps running the old image.

## 1. Read the tag

```sh
grep -A1 '^  caldir:' compose.yaml
```

Use the `image:` value verbatim — currently
`localhost/calendar-caldir:vtodo-support`. The `localhost/` prefix is part of
the tag; keep it even when building with Docker.

## 2. Build

Build context is `caldir/`. Use whichever engine the host runs.

```sh
docker build -t localhost/calendar-caldir:vtodo-support caldir/
```

```sh
podman build -t localhost/calendar-caldir:vtodo-support caldir/
```

The Dockerfile fetches exactly `ARG CALDIR_COMMIT` from
`https://github.com/belcaik/caldir.git` and verifies `HEAD` against that full
SHA. It also checks the four Google OAuth scopes and records `BUILD_INFO`. An
unchanged commit reuses the compiled layer, so script-only changes do not
recompile Rust. See
[ADR-0008](../../../docs/adr/0008-caldir-pinned-to-a-commit.md).

## 3. Verify the image contents

The stage-2 `chmod +x` block in `caldir/Dockerfile` is the authoritative list.
Check every binary and script it names is present:

```sh
docker run --rm --entrypoint sh localhost/calendar-caldir:vtodo-support -c 'ls -l /usr/local/bin'
docker run --rm --entrypoint cat localhost/calendar-caldir:vtodo-support /usr/local/share/caldir/BUILD_INFO
```

Expect the six Rust binaries (`caldir`, `caldir-provider-google`,
`caldir-provider-outlook`, `caldir-provider-icloud`, `caldir-provider-caldav`,
`caldir-provider-webcal`) plus `entrypoint.sh`, `cron-run.sh`,
`provision-radicale.sh`, `provision-google.py`, `courses.py`,
`merge-ucrawler.py`, `bridge-vtodo.py`, `bridge-windows.py`, `verify-sync.py`,
`diagnose-google.py`, and `run-sync.sh`. `dedupe-vtodo.py` is deliberately
absent. A missing script means its `COPY` line is gone — fix the Dockerfile and
rebuild rather than copying the file into a running container.

## 4. Restart only caldir

```sh
docker compose up -d --no-deps caldir
```

`--no-deps` keeps `radicale` and `u-crawler` untouched. Never `down` the whole
project for an image swap.

## 5. Watch the first run

`RUN_ON_START=true`, so the new container executes the pipeline immediately:

```sh
docker compose logs -f caldir
```

Follow the `[caldir]` step lines through provisioning, merge, phase 1, both
bridges, and phase 2, then the `[verify]` result. The run succeeded when
verification passes and you see:

```
[caldir] <timestamp> done
```

An exit before that line ends the run at the step whose message came last. Exit
code 2 from a bridge means conflicts — use the `resolve-bridge-conflict` skill.
Exit 3 from `verify-sync.py` means publication did not converge; see
[docs/troubleshooting.md](../../../docs/troubleshooting.md#verification-fails-with-exit-3).
