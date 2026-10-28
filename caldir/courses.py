#!/usr/bin/env python3
"""Course discovery shared by the Vassago pipeline scripts.

u_crawler writes one directory per course under the calendar root:

    <course>/deadlines/*.ics   VTODO assignments
    <course>/windows/*.ics     VEVENT delivery windows

merge-ucrawler.py derives one canonical collection per course and kind:

    <course>__deadlines/       <course>__windows/

Discovery is structural. A course is any top-level directory that holds a
`deadlines/` or `windows/` subdirectory with at least one `.ics` file; its name
is never matched against an academic term code, so a new term is picked up
without a code change. Canonical collections are found by their `__<kind>`
suffix, which keeps courses whose u_crawler source has disappeared (old
terms) visible to the bridges instead of treating them as deleted.

Usable as a module (`import courses`) and from sh:

    courses.py sources <kind>     course names with a non-empty <kind>/ source
    courses.py canonical <kind>   course names with a <course>__<kind>/ dir
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


KINDS = ("deadlines", "windows")

# Course names end up in Radicale URLs, TOML strings and file names.
COURSE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _check_kind(kind: str) -> None:
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; expected one of {KINDS}")


def is_course_name(name: str) -> bool:
    if not COURSE_NAME_RE.match(name):
        return False

    # A canonical collection is never a course of its own.
    return not any(name.endswith(f"__{kind}") for kind in KINDS)


def canonical_name(course: str, kind: str) -> str:
    _check_kind(kind)
    return f"{course}__{kind}"


def has_ics(directory: Path) -> bool:
    return directory.is_dir() and any(directory.glob("*.ics"))


def source_courses(root: Path, kind: str) -> list[str]:
    """Courses whose u_crawler `<course>/<kind>/` holds at least one ICS."""
    _check_kind(kind)

    if not root.is_dir():
        return []

    result = []

    for path in sorted(root.iterdir()):
        if not path.is_dir() or not is_course_name(path.name):
            continue

        if has_ics(path / kind):
            result.append(path.name)

    return result


def canonical_courses(root: Path, kind: str) -> list[str]:
    """Courses that already have a `<course>__<kind>/` canonical collection.

    Includes courses whose u_crawler source is gone: their canonical items
    stay authoritative until removed deliberately.
    """
    _check_kind(kind)

    if not root.is_dir():
        return []

    suffix = f"__{kind}"
    result = []

    for path in sorted(root.iterdir()):
        if not path.is_dir() or not path.name.endswith(suffix):
            continue

        course = path.name[: -len(suffix)]

        if is_course_name(course):
            result.append(course)

    return result


def canonical_dirs(root: Path, kind: str) -> list[Path]:
    return [
        root / canonical_name(course, kind)
        for course in canonical_courses(root, kind)
    ]


def main(argv: list[str]) -> int:
    import os

    usage = "usage: courses.py {sources|canonical} {deadlines|windows}"

    if len(argv) != 2 or argv[0] not in {"sources", "canonical"}:
        print(usage, file=sys.stderr)
        return 64

    command, kind = argv

    if kind not in KINDS:
        print(usage, file=sys.stderr)
        return 64

    root = Path(os.environ.get("CALENDAR_ROOT", "/data/calendars"))

    if command == "sources":
        names = source_courses(root, kind)
    else:
        names = canonical_courses(root, kind)

    for name in names:
        print(name)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
