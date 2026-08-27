---
name: validate-vassago
description: Use when validating a change to this repo, before claiming a fix works, or when scripts/check.sh reports a failing check and you need to interpret it.
---

# Validate Vassago

## Run

```sh
sh scripts/check.sh
```

Run it from the repository root. It needs only `sh`, `git`, `python3`, and
optionally `docker`. A non-zero exit means at least one check below failed.

## Interpret a failure

The checks run in this order; find the one that failed and act on it.

1. **Syntax** — `sh -n` on every `*.sh`, `python3 -m py_compile` on every
   `*.py`. The message names the file and line. Fix the syntax; nothing else in
   the run is meaningful until it passes.
2. **Compose config** — `docker compose --env-file .env.example config -q`.
   Skipped with a notice when `docker` is absent; a skip is not a failure. A
   real failure means `compose.yaml` no longer parses or references an
   undefined variable — check that every `${VAR}` it uses has a line in
   `.env.example`.
3. **Secret guard** — `git ls-files` must contain none of the gitignored secret
   or runtime paths. A hit means a credential or live calendar file got staged
   or committed. Remove it from the index before anything else, and check
   `.gitignore` still covers it. See [docs/security.md](../../../docs/security.md#what-is-gitignored).
4. **Term-prefix lockstep** — the `202615_` literal must be identical in
   `provision-radicale.sh`, `merge-ucrawler.py`, `bridge-vtodo.py`,
   `bridge-windows.py`, and `dedupe-vtodo.py`. A failure means one script was
   rotated to a new term and the others were not; the pipeline would silently
   process an empty set. Rotate all five together — see
   [docs/operations.md](../../../docs/operations.md#term-rotation).
5. **Dockerfile COPY coverage** — every `/usr/local/bin/<script>` invoked by
   `caldir/run-sync.sh` must have a matching `COPY` in `caldir/Dockerfile`. A
   failure means the next image build produces a container whose pipeline dies
   at that step. Add the `COPY` (and the `chmod +x` beside it), then rebuild
   with the `rebuild-caldir-image` skill.
6. **Unit tests** — `python3 -m unittest discover -s tests`. Failures point at
   the merge or bridge semantics documented in
   [docs/sync-semantics.md](../../../docs/sync-semantics.md). Treat a failure as
   a behaviour change until proven otherwise: the authority rules are the
   contract, not the test.
7. **Docs path check** — `scripts/check_docs_paths.py` resolves every relative
   path referenced from Markdown under `docs/`, `AGENTS.md`, `README.md`, and
   `.claude/skills/`. A failure names the file and the dead path. Either the
   target moved or the link has a typo.

## Tests alone

While iterating on merge or bridge semantics, skip the rest:

```sh
python3 -m unittest discover -s tests -v
```

Finish with the full `sh scripts/check.sh` before reporting the change as done.
