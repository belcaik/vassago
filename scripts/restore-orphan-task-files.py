#!/usr/bin/env python3
"""One-off migration: recreate Google Tasks mirror files caldir still has a base for.

A task pulled from Google under its derived UID (`<id>@google-tasks.com`)
whose mirror file was later removed, while caldir kept its sync base, reads as
"deleted locally": the first caldir that lists every page of the task list
(fork `3b32b5c`+) deletes it from Google. This recreates each such file from
the base caldir recorded, so the task stays put and `bridge-vtodo.py` can
adopt it as the Google side of its assignment.

Only adds files; never deletes or edits one. Dry run unless `--apply`.

    restore-orphan-task-files.py <mirror dir> [--apply]
"""

from __future__ import annotations

import sys
from pathlib import Path

DERIVED_SUFFIX = "@google-tasks.com"


def uid_of(raw: str) -> str | None:
    for line in raw.replace("\r\n", "\n").split("\n"):
        if line.upper().startswith("UID:"):
            return line[4:].strip()

    return None


def orphans(mirror: Path) -> list[tuple[str, Path]]:
    present = {
        uid_of(path.read_bytes().decode("utf-8"))
        for path in mirror.glob("*.ics")
    }

    found = []

    for base in sorted((mirror / ".caldir" / "state" / "bases").glob("*")):
        uid = uid_of(base.read_bytes().decode("utf-8"))

        if uid and uid.endswith(DERIVED_SUFFIX) and uid not in present:
            found.append((uid, base))

    return found


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-") or len(argv) > 2:
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 64

    mirror = Path(argv[0])
    apply = argv[1:] == ["--apply"]

    for uid, base in orphans(mirror):
        target = mirror / f"restored-{uid.split('@', 1)[0]}.ics"

        if target.exists():
            print(f"skip {uid}: {target.name} exists", file=sys.stderr)
            continue

        print(f"{'restore' if apply else 'would restore'} {uid} -> {target.name}")

        if apply:
            target.write_bytes(base.read_bytes())

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
