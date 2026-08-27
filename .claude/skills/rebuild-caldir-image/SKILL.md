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

The Dockerfile clones `https://github.com/belcaik/caldir.git` at
`--branch vtodo-support --depth 1`, which is a moving branch and not a pinned
commit. Two builds on different days can produce different binaries. Record the
build date when the result matters, and see
[docs/adr/0006-caldir-built-from-vtodo-support-branch.md](../../../docs/adr/0006-caldir-built-from-vtodo-support-branch.md).

## 3. Verify the image contents

The stage-2 `chmod +x` block in `caldir/Dockerfile` is the authoritative list.
Check every binary and script it names is present:

```sh
docker run --rm --entrypoint sh localhost/calendar-caldir:vtodo-support -c 'ls -l /usr/local/bin'
```

Expect the six Rust binaries (`caldir`, `caldir-provider-google`,
`caldir-provider-outlook`, `caldir-provider-icloud`, `caldir-provider-caldav`,
`caldir-provider-webcal`) plus `entrypoint.sh`, `provision-radicale.sh`,
`provision-google.py`, `merge-ucrawler.py`, `bridge-vtodo.py`,
`bridge-windows.py`, and `run-sync.sh`. `dedupe-vtodo.py` is deliberately
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
bridges, and phase 2. The run succeeded when you see:

```
[caldir] <timestamp> done
```

An exit before that line ends the run at the step whose message came last. Exit
code 2 from a bridge means conflicts — use the `resolve-bridge-conflict` skill.
