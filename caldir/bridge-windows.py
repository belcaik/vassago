#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import os
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
        "GOOGLE_WINDOWS_DIR",
        str(ROOT / "unab-windows"),
    )
)

STATE_FILE = Path(
    os.environ.get(
        "WINDOWS_BRIDGE_STATE_FILE",
        "/data/.local/share/caldir/windows-bridge-state.json",
    )
)

MANAGED_UID_PREFIX = "u_crawler-window-"

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


def find_vevent(lines: list[str]) -> tuple[int, int]:
    start = None

    for index, line in enumerate(lines):
        upper = line.upper()

        if upper == "BEGIN:VEVENT":
            start = index
            continue

        if upper == "END:VEVENT" and start is not None:
            return start, index

    raise ValueError("VEVENT component not found")


def parse_vevent(path: Path) -> Item:
    raw = path.read_text(encoding="utf-8")
    lines = unfold(raw)

    start, end = find_vevent(lines)

    props: dict[str, list[str]] = {}

    for line in lines[start + 1 : end]:
        name = property_name(line)

        if name is not None:
            props.setdefault(name, []).append(line)

    if "UID" not in props:
        raise ValueError(f"{path}: VEVENT has no UID")

    return Item(
        path=path,
        raw=raw,
        props=props,
    )


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp = path.with_suffix(
        path.suffix + ".bridge.tmp"
    )

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


def load_canonical() -> dict[str, Item]:
    result: dict[str, Item] = {}

    for directory in sorted(
        ROOT.glob("202615_*__windows")
    ):
        if not directory.is_dir():
            continue

        for path in sorted(directory.glob("*.ics")):
            item = parse_vevent(path)

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
            f"Google windows directory missing: {GOOGLE_DIR}"
        )

    for path in sorted(GOOGLE_DIR.glob("*.ics")):
        item = parse_vevent(path)

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
            "version": 1,
            "items": {},
        }

    try:
        state = json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )
    except Exception as exc:
        raise RuntimeError(
            f"could not read state {STATE_FILE}: {exc}"
        ) from exc

    if state.get("version") != 1:
        return {
            "version": 1,
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


def refresh(path: Path) -> Item:
    return parse_vevent(path)


def merge_canonical_into_google(
    google: Item,
    canonical: Item,
) -> str:
    google_lines = unfold(google.raw)
    canonical_lines = unfold(canonical.raw)

    g_start, g_end = find_vevent(google_lines)
    c_start, c_end = find_vevent(canonical_lines)

    google_private: list[str] = []

    for line in google_lines[g_start + 1 : g_end]:
        name = property_name(line)

        if (
            name is not None
            and name.startswith(GOOGLE_PRIVATE_PREFIX)
        ):
            google_private.append(line)

    canonical_body: list[str] = []

    for line in canonical_lines[c_start + 1 : c_end]:
        name = property_name(line)

        if (
            name is not None
            and name.startswith(GOOGLE_PRIVATE_PREFIX)
        ):
            continue

        canonical_body.append(line)

    result = (
        google_lines[: g_start + 1]
        + canonical_body
        + google_private
        + google_lines[g_end:]
    )

    return "\r\n".join(result).rstrip("\r\n") + "\r\n"


def seed_google(canonical: Item) -> Item:
    target = GOOGLE_DIR / canonical.path.name

    atomic_write(
        target,
        canonical.raw,
    )

    return refresh(target)


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

        if left is None and right is None:
            continue

        if old is None:
            if left is not None and right is None:
                right = seed_google(left)

                print(
                    f"[windows-bridge] seed Google <- {uid}"
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
                print(
                    f"[windows-bridge] CONFLICT {uid}: "
                    "managed event exists only in Google",
                    file=sys.stderr,
                )

                conflicts += 1
                continue

            assert left is not None
            assert right is not None

            merged = merge_canonical_into_google(
                right,
                left,
            )

            if merged != right.raw:
                atomic_write(
                    right.path,
                    merged,
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

        if left is None and right is not None:
            if right_changed:
                print(
                    f"[windows-bridge] CONFLICT {uid}: "
                    "deleted canonically but changed in Google",
                    file=sys.stderr,
                )

                conflicts += 1
                continue

            right.path.unlink()

            print(
                f"[windows-bridge] delete Google <- {uid}"
            )

            deleted += 1

            record_state(
                state,
                uid,
                None,
                None,
            )

            continue

        if right is None and left is not None:
            right = seed_google(left)

            print(
                f"[windows-bridge] restore Google <- {uid}"
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

        if right_changed and not left_changed:
            # u_crawler/Canvas is authoritative for delivery windows.
            # Manual Google edits are overwritten intentionally.
            merged = merge_canonical_into_google(
                right,
                left,
            )

            atomic_write(
                right.path,
                merged,
            )

            right = refresh(right.path)

            print(
                f"[windows-bridge] canonical -> Google {uid}"
            )

            changed += 1

            record_state(
                state,
                uid,
                left,
                right,
            )

            continue

        if left_changed:
            merged = merge_canonical_into_google(
                right,
                left,
            )

            atomic_write(
                right.path,
                merged,
            )

            right = refresh(right.path)

            print(
                f"[windows-bridge] Google <- canonical {uid}"
            )

            changed += 1

            record_state(
                state,
                uid,
                left,
                right,
            )

            continue

    return changed, deleted, conflicts


def main() -> int:
    print(
        f"[windows-bridge] canonical: "
        f"{ROOT}/202615_*__windows"
    )

    print(
        f"[windows-bridge] Google: "
        f"{GOOGLE_DIR}"
    )

    state = load_state()

    changed, deleted, conflicts = reconcile(
        state
    )

    save_state(state)

    print(
        "[windows-bridge] done: "
        f"{changed} changed, "
        f"{deleted} deleted, "
        f"{conflicts} conflicts"
    )

    return 2 if conflicts else 0


if __name__ == "__main__":
    raise SystemExit(main())
