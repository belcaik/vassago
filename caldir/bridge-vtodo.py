#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import os
import re
import sys

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(
    os.environ.get(
        "CALENDAR_ROOT",
        "/data/calendars",
    )
)

GOOGLE_DIR = Path(
    os.environ.get(
        "GOOGLE_TASKS_DIR",
        str(ROOT / "unab"),
    )
)

STATE_FILE = Path(
    os.environ.get(
        "BRIDGE_STATE_FILE",
        "/data/.local/share/caldir/vtodo-bridge-state.json",
    )
)

MANAGED_UID_PREFIX = "u_crawler-todo-"

COMMON_FIELDS = {
    "SUMMARY",
    "DESCRIPTION",
    "DUE",
    "STATUS",
    "COMPLETED",
}

RICH_FIELDS = {
    "PRIORITY",
    "URL",
    "DTSTART",
    "PERCENT-COMPLETE",
    "LOCATION",
    "ORGANIZER",
    "ATTENDEE",
    "ATTACH",
    "VALARM",
}

IGNORE_FOR_HASH = {
    "DTSTAMP",
    "LAST-MODIFIED",
    "SEQUENCE",
}

GOOGLE_PRIVATE_PREFIX = "X-GOOGLE-"


@dataclass
class Item:
    path: Path
    raw: str
    props: dict[str, list[str]]

    @property
    def uid(self) -> str:
        values = self.props.get("UID", [])

        if not values:
            raise ValueError(f"{self.path}: UID missing")

        return property_value(values[0])


def unfold(raw: str) -> list[str]:
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    physical = raw.split("\n")

    logical: list[str] = []

    for line in physical:
        if line.startswith((" ", "\t")) and logical:
            logical[-1] += line[1:]
        else:
            logical.append(line)

    return logical


def property_name(line: str) -> str | None:
    if ":" not in line:
        return None

    return line.split(":", 1)[0].split(";", 1)[0].upper()


def property_value(line: str) -> str:
    return line.split(":", 1)[1]


def find_vtodo(lines: list[str]) -> tuple[int, int]:
    start = None

    for index, line in enumerate(lines):
        upper = line.upper()

        if upper == "BEGIN:VTODO":
            start = index
            continue

        if upper == "END:VTODO" and start is not None:
            return start, index

    raise ValueError("VTODO component not found")


def parse_vtodo(path: Path) -> Item:
    raw = path.read_text(encoding="utf-8")
    lines = unfold(raw)

    start, end = find_vtodo(lines)

    props: dict[str, list[str]] = {}

    for line in lines[start + 1 : end]:
        name = property_name(line)

        if name is not None:
            props.setdefault(name, []).append(line)

    if "UID" not in props:
        raise ValueError(f"{path}: VTODO has no UID")

    return Item(
        path=path,
        raw=raw,
        props=props,
    )


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp = path.with_suffix(path.suffix + ".bridge.tmp")

    tmp.write_text(
        content,
        encoding="utf-8",
        newline="",
    )

    os.replace(tmp, path)


def semantic_hash(item: Item) -> str:
    values: list[str] = []

    for name in sorted(item.props):
        if name in IGNORE_FOR_HASH:
            continue

        if name.startswith(GOOGLE_PRIVATE_PREFIX):
            continue

        for line in item.props[name]:
            values.append(line)

    return hashlib.sha256(
        "\n".join(values).encode("utf-8")
    ).hexdigest()


def normalize_due_date(line: str) -> str | None:
    value = property_value(line)

    match = re.match(r"^(\d{4})(\d{2})(\d{2})", value)

    if match is None:
        return None

    return "".join(match.groups())


def shared_signature(item: Item) -> str:
    values: list[str] = []

    for field in sorted(COMMON_FIELDS):
        lines = item.props.get(field, [])

        if field == "DUE":
            for line in lines:
                normalized = normalize_due_date(line)

                if normalized is not None:
                    values.append(f"DUE-DATE:{normalized}")
                else:
                    values.append(line)

            continue

        values.extend(lines)

    return hashlib.sha256(
        "\n".join(values).encode("utf-8")
    ).hexdigest()


def google_due_from_canonical(
    line: str,
) -> str:
    normalized = normalize_due_date(line)

    if normalized is None:
        return line

    return f"DUE;VALUE=DATE:{normalized}"


def canonical_due_from_google(
    google_line: str,
    existing_line: str | None,
) -> str:
    new_date = normalize_due_date(google_line)

    if new_date is None:
        return google_line

    if existing_line is None:
        return f"DUE;VALUE=DATE:{new_date}"

    old_value = property_value(existing_line)

    # Existing DATE field: replace date directly.
    if "VALUE=DATE" in existing_line.upper():
        prefix = existing_line.split(":", 1)[0]
        return f"{prefix}:{new_date}"

    # Preserve time-of-day when canonical side had a datetime.
    #
    # 20260830T235900Z
    # becomes
    # 20260901T235900Z
    match = re.match(
        r"^\d{8}(T.*)$",
        old_value,
    )

    if match is not None:
        prefix = existing_line.split(":", 1)[0]
        return f"{prefix}:{new_date}{match.group(1)}"

    return f"DUE;VALUE=DATE:{new_date}"


def replace_fields(
    target: Item,
    source: Item,
    fields: set[str],
    *,
    direction: str,
) -> str:
    target_lines = unfold(target.raw)
    start, end = find_vtodo(target_lines)

    target_body = target_lines[start + 1 : end]

    kept: list[str] = []

    for line in target_body:
        name = property_name(line)

        if name in fields:
            continue

        kept.append(line)

    additions: list[str] = []

    for field in fields:
        source_lines = source.props.get(field, [])

        if field != "DUE":
            additions.extend(source_lines)
            continue

        if not source_lines:
            continue

        source_due = source_lines[0]

        if direction == "canonical-to-google":
            additions.append(
                google_due_from_canonical(source_due)
            )

        elif direction == "google-to-canonical":
            existing_due = target.props.get("DUE", [])
            additions.append(
                canonical_due_from_google(
                    source_due,
                    existing_due[0] if existing_due else None,
                )
            )

        else:
            additions.append(source_due)

    result = (
        target_lines[: start + 1]
        + kept
        + additions
        + target_lines[end:]
    )

    return "\r\n".join(result).rstrip("\r\n") + "\r\n"


def copy_rich_fields_to_google(
    google: Item,
    canonical: Item,
) -> str:
    google_lines = unfold(google.raw)
    start, end = find_vtodo(google_lines)

    body: list[str] = []

    for line in google_lines[start + 1 : end]:
        name = property_name(line)

        if name in RICH_FIELDS:
            continue

        if (
            name is not None
            and name.startswith("X-")
            and not name.startswith(GOOGLE_PRIVATE_PREFIX)
        ):
            continue

        body.append(line)

    additions: list[str] = []

    for field in sorted(RICH_FIELDS):
        additions.extend(
            canonical.props.get(field, [])
        )

    for name, lines in canonical.props.items():
        if (
            name.startswith("X-")
            and not name.startswith(GOOGLE_PRIVATE_PREFIX)
        ):
            additions.extend(lines)

    result = (
        google_lines[: start + 1]
        + body
        + additions
        + google_lines[end:]
    )

    return "\r\n".join(result).rstrip("\r\n") + "\r\n"


def load_canonical() -> dict[str, Item]:
    result: dict[str, Item] = {}

    for directory in sorted(
        ROOT.glob("202615_*__deadlines")
    ):
        if not directory.is_dir():
            continue

        for path in sorted(directory.glob("*.ics")):
            item = parse_vtodo(path)

            if not item.uid.startswith(MANAGED_UID_PREFIX):
                continue

            if item.uid in result:
                raise RuntimeError(
                    f"duplicate canonical UID {item.uid}: "
                    f"{result[item.uid].path} and {path}"
                )

            result[item.uid] = item

    return result


def load_google() -> dict[str, Item]:
    result: dict[str, Item] = {}

    if not GOOGLE_DIR.is_dir():
        raise RuntimeError(
            f"Google Tasks directory does not exist: {GOOGLE_DIR}"
        )

    for path in sorted(GOOGLE_DIR.glob("*.ics")):
        item = parse_vtodo(path)

        # Personal Google tasks use @google-tasks.com and remain untouched.
        if not item.uid.startswith(MANAGED_UID_PREFIX):
            continue

        if item.uid in result:
            raise RuntimeError(
                f"duplicate Google UID {item.uid}: "
                f"{result[item.uid].path} and {path}"
            )

        result[item.uid] = item

    return result


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {
            "version": 2,
            "items": {},
        }

    try:
        state = json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )
    except Exception as exc:
        raise RuntimeError(
            f"could not read bridge state {STATE_FILE}: {exc}"
        ) from exc

    # Old bridge state used a different layout.
    if state.get("version") != 2:
        return {
            "version": 2,
            "items": {},
        }

    return state


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    tmp = STATE_FILE.with_suffix(".tmp")

    tmp.write_text(
        json.dumps(
            state,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    os.replace(tmp, STATE_FILE)


def record_state(
    state: dict,
    uid: str,
    canonical: Item | None,
    google: Item | None,
) -> None:
    state.setdefault("items", {})[uid] = {
        "canonical_hash": (
            semantic_hash(canonical)
            if canonical is not None
            else None
        ),
        "google_hash": (
            semantic_hash(google)
            if google is not None
            else None
        ),
        "seen_at": datetime.now(timezone.utc).isoformat(),
    }


def refresh(path: Path) -> Item:
    return parse_vtodo(path)


def seed_google(
    canonical: Item,
) -> Item:
    target = GOOGLE_DIR / canonical.path.name

    atomic_write(
        target,
        canonical.raw,
    )

    google = refresh(target)

    # Google should receive a date-only DUE immediately so that the first
    # provider push does not create unnecessary canonicalization churn.
    projected = replace_fields(
        google,
        canonical,
        COMMON_FIELDS,
        direction="canonical-to-google",
    )

    atomic_write(target, projected)

    return refresh(target)


def reconcile(
    state: dict,
) -> tuple[int, int, int]:
    canonical = load_canonical()
    google = load_google()

    all_uids = sorted(
        set(canonical)
        | set(google)
        | set(state.get("items", {}))
    )

    changed = 0
    deleted = 0
    conflicts = 0

    for uid in all_uids:
        left = canonical.get(uid)
        right = google.get(uid)

        old = state.setdefault("items", {}).get(uid)

        # Never-existing / already fully deleted.
        if left is None and right is None:
            continue

        # First observation.
        if old is None:
            if left is not None and right is None:
                right = seed_google(left)

                print(
                    f"[bridge] seed Google <- {uid}"
                )

                changed += 1
                record_state(
                    state,
                    uid,
                    left,
                    right,
                )
                continue

            if left is None and right is not None:
                # A managed UID appearing only in Google is suspicious:
                # don't invent a course destination.
                print(
                    f"[bridge] CONFLICT {uid}: "
                    "managed task exists only in Google "
                    "and has no canonical course",
                    file=sys.stderr,
                )

                conflicts += 1
                continue

            assert left is not None
            assert right is not None

            if shared_signature(left) != shared_signature(right):
                print(
                    f"[bridge] CONFLICT {uid}: "
                    "first observation differs on shared fields",
                    file=sys.stderr,
                )

                conflicts += 1
                continue

            rich = copy_rich_fields_to_google(
                right,
                left,
            )

            if rich != right.raw:
                atomic_write(
                    right.path,
                    rich,
                )

                right = refresh(right.path)
                changed += 1

            record_state(
                state,
                uid,
                left,
                right,
            )

            continue

        old_left_hash = old.get("canonical_hash")
        old_right_hash = old.get("google_hash")

        left_hash = (
            semantic_hash(left)
            if left is not None
            else None
        )

        right_hash = (
            semantic_hash(right)
            if right is not None
            else None
        )

        left_changed = (
            left_hash != old_left_hash
        )

        right_changed = (
            right_hash != old_right_hash
        )

        # Canonical deletion => Google deletion, but only if Google itself
        # did not change meanwhile.
        if left is None and right is not None:
            if right_changed:
                print(
                    f"[bridge] CONFLICT {uid}: "
                    "deleted canonically but changed in Google",
                    file=sys.stderr,
                )

                conflicts += 1
                continue

            right.path.unlink()

            print(
                f"[bridge] delete Google <- {uid}"
            )

            deleted += 1

            record_state(
                state,
                uid,
                None,
                None,
            )

            continue

        # Google deletion means user explicitly deleted the task.
        if right is None and left is not None:
            if left_changed:
                print(
                    f"[bridge] CONFLICT {uid}: "
                    "deleted in Google but changed canonically",
                    file=sys.stderr,
                )

                conflicts += 1
                continue

            # Do NOT delete the canonical academic assignment itself.
            #
            # A Google deletion is represented as completed/cancelled state
            # only through normal task edits; hard-delete from Google must not
            # erase the Canvas-derived source of truth.
            #
            # Re-seed it instead.
            right = seed_google(left)

            print(
                f"[bridge] restore Google task <- {uid} "
                "(canonical assignment still exists)"
            )

            changed += 1

            record_state(
                state,
                uid,
                left,
                right,
            )

            continue

        assert left is not None
        assert right is not None

        if not left_changed and not right_changed:
            continue

        if left_changed and right_changed:
            if shared_signature(left) != shared_signature(right):
                print(
                    f"[bridge] CONFLICT {uid}: "
                    "both sides changed shared fields",
                    file=sys.stderr,
                )

                conflicts += 1
                continue

            rich = copy_rich_fields_to_google(
                right,
                left,
            )

            if rich != right.raw:
                atomic_write(
                    right.path,
                    rich,
                )

                right = refresh(right.path)
                changed += 1

            record_state(
                state,
                uid,
                left,
                right,
            )

            continue

        if left_changed:
            projected = replace_fields(
                right,
                left,
                COMMON_FIELDS,
                direction="canonical-to-google",
            )

            atomic_write(
                right.path,
                projected,
            )

            right = refresh(right.path)

            rich = copy_rich_fields_to_google(
                right,
                left,
            )

            atomic_write(
                right.path,
                rich,
            )

            right = refresh(right.path)

            print(
                f"[bridge] Google <- canonical {uid}"
            )

            changed += 1

        elif right_changed:
            merged = replace_fields(
                left,
                right,
                COMMON_FIELDS,
                direction="google-to-canonical",
            )

            atomic_write(
                left.path,
                merged,
            )

            left = refresh(left.path)

            # Push canonical-only metadata back into the local Google mirror.
            rich = copy_rich_fields_to_google(
                right,
                left,
            )

            atomic_write(
                right.path,
                rich,
            )

            right = refresh(right.path)

            print(
                f"[bridge] canonical <- Google {uid}"
            )

            changed += 1

        record_state(
            state,
            uid,
            left,
            right,
        )

    return changed, deleted, conflicts


def main() -> int:
    print(
        f"[bridge] canonical: {ROOT}/202615_*__deadlines"
    )
    print(
        f"[bridge] Google Tasks: {GOOGLE_DIR}"
    )

    state = load_state()

    changed, deleted, conflicts = reconcile(
        state
    )

    save_state(state)

    print(
        "[bridge] done: "
        f"{changed} changed, "
        f"{deleted} deleted, "
        f"{conflicts} conflicts"
    )

    return 2 if conflicts else 0


if __name__ == "__main__":
    raise SystemExit(main())
