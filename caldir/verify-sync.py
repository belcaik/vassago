#!/usr/bin/env python3
"""Post-sync check: every canonical item must be published to Google.

Runs after phase 2. For each managed UID in the canonical collections it
requires a file in the Google mirror carrying that UID *and* the provider id
caldir writes back after a successful push (X-GOOGLE-TASK-ID for tasks,
X-GOOGLE-EVENT-ID for events). A UID missing from the mirror, or present
without a provider id, means the run did not converge — for example a bridge
restore that caldir undid in phase 2 — and the run must not end in "done".

Items caldir would not sync anyway (outside its default +-365 day window) are
skipped. Read-only: never writes a file.

Exit codes: 0 all published, 3 at least one item unpublished.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import courses


ROOT = Path(os.environ.get("CALENDAR_ROOT", "/data/calendars"))
GOOGLE_TASKS_DIR = Path(
    os.environ.get("GOOGLE_TASKS_DIR", str(ROOT / "unab"))
)
GOOGLE_WINDOWS_DIR = Path(
    os.environ.get("GOOGLE_WINDOWS_DIR", str(ROOT / "unab-windows"))
)

# Mirrors caldir-cli's DEFAULT_PAST_DAYS / DEFAULT_FUTURE_DAYS.
SYNC_WINDOW_DAYS = 365

# (kind, component, managed UID prefix, date property, provider id property)
CHECKS = (
    ("deadlines", "VTODO", "u_crawler-todo-", "DUE", "X-GOOGLE-TASK-ID"),
    ("windows", "VEVENT", "u_crawler-window-", "DTSTART", "X-GOOGLE-EVENT-ID"),
)

DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})")


def component_props(raw: str, component: str) -> dict[str, str]:
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")

    lines: list[str] = []

    for line in raw.split("\n"):
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)

    props: dict[str, str] = {}
    inside = False

    for line in lines:
        upper = line.upper()

        if upper == f"BEGIN:{component}":
            inside = True
            continue

        if upper == f"END:{component}":
            break

        if inside and ":" in line:
            name = line.split(":", 1)[0].split(";", 1)[0].upper()
            props.setdefault(name, line.split(":", 1)[1].strip())

    return props


def in_sync_window(value: str | None, today: date) -> bool:
    if not value:
        return True

    match = DATE_RE.match(value)

    if match is None:
        return True

    day = date(*(int(part) for part in match.groups()))
    window = timedelta(days=SYNC_WINDOW_DAYS)

    return today - window <= day <= today + window


def load(directory: Path, component: str) -> list[dict[str, str]]:
    if not directory.is_dir():
        return []

    return [
        component_props(path.read_bytes().decode("utf-8"), component)
        for path in sorted(directory.glob("*.ics"))
    ]


def unpublished(
    kind: str,
    component: str,
    prefix: str,
    date_prop: str,
    id_prop: str,
    mirror: Path,
    today: date,
) -> tuple[int, list[str], list[str]]:
    expected: set[str] = set()

    for directory in courses.canonical_dirs(ROOT, kind):
        for props in load(directory, component):
            uid = props.get("UID", "")

            if not uid.startswith(prefix):
                continue

            if in_sync_window(props.get(date_prop), today):
                expected.add(uid)

    published: dict[str, bool] = {}

    for props in load(mirror, component):
        uid = props.get("UID", "")

        if uid in expected:
            published[uid] = published.get(uid, False) or bool(
                props.get(id_prop)
            )

    missing = sorted(expected - set(published))
    no_id = sorted(uid for uid, ok in published.items() if not ok)

    return len(expected), missing, no_id


def main() -> int:
    today = date.today()
    failed = False

    mirrors = {
        "deadlines": GOOGLE_TASKS_DIR,
        "windows": GOOGLE_WINDOWS_DIR,
    }

    for kind, component, prefix, date_prop, id_prop in CHECKS:
        total, missing, no_id = unpublished(
            kind, component, prefix, date_prop, id_prop, mirrors[kind], today
        )

        print(
            f"[verify] {kind}: {total} expected, "
            f"{len(missing)} missing from mirror, "
            f"{len(no_id)} without {id_prop}"
        )

        for uid in missing[:10]:
            print(f"[verify] {kind} missing: {uid}", file=sys.stderr)

        for uid in no_id[:10]:
            print(f"[verify] {kind} unpublished: {uid}", file=sys.stderr)

        failed |= bool(missing or no_id)

    return 3 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
