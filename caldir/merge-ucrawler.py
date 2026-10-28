#!/usr/bin/env python3

from __future__ import annotations

import os
import sys
from pathlib import Path

import courses


ROOT = Path(os.environ.get("CALENDAR_ROOT", "/data/calendars"))

# A run that would remove more than this share of a course's managed files is
# held back: an empty or half-written u_crawler export must not read as "every
# assignment was deleted in Canvas". MERGE_ALLOW_MASS_DELETE=1 lifts it once.
MASS_DELETE_SHARE = 0.5
MASS_DELETE_MIN = 2

# Estos campos pueden ser modificados por Thunderbird / Google.
# u_crawler no debe pisarlos en cada regeneración.
USER_STATE_FIELDS = {
    "STATUS",
    "COMPLETED",
    "PERCENT-COMPLETE",
}


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


def find_component(
    lines: list[str],
    component: str,
) -> tuple[int, int]:
    begin = f"BEGIN:{component}"
    end = f"END:{component}"

    start = None

    for index, line in enumerate(lines):
        upper = line.upper()

        if upper == begin:
            start = index
            continue

        if upper == end and start is not None:
            return start, index

    raise ValueError(f"{component} not found")


def component_properties(
    lines: list[str],
    start: int,
    end: int,
) -> dict[str, list[str]]:
    props: dict[str, list[str]] = {}

    for line in lines[start + 1 : end]:
        name = property_name(line)

        if name is not None:
            props.setdefault(name, []).append(line)

    return props


def merge_vtodo(
    source_raw: str,
    existing_raw: str,
) -> str:
    source = unfold(source_raw)
    existing = unfold(existing_raw)

    source_start, source_end = find_component(source, "VTODO")
    existing_start, existing_end = find_component(existing, "VTODO")

    existing_props = component_properties(
        existing,
        existing_start,
        existing_end,
    )

    preserved: list[str] = []

    for field in USER_STATE_FIELDS:
        preserved.extend(existing_props.get(field, []))

    source_body: list[str] = []

    for line in source[source_start + 1 : source_end]:
        name = property_name(line)

        if name in USER_STATE_FIELDS:
            continue

        source_body.append(line)

    merged = (
        source[: source_start + 1]
        + source_body
        + preserved
        + source[source_end:]
    )

    return "\r\n".join(merged).rstrip("\r\n") + "\r\n"


# Bytes, not read_text(): universal-newline decoding turns CRLF into LF, so a
# CRLF file never compared equal to its own merge and was rewritten every run.
def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    temp = path.with_suffix(path.suffix + ".tmp")

    temp.write_text(
        content,
        encoding="utf-8",
        newline="",
    )

    os.replace(temp, path)


def component_uid(raw: str) -> str | None:
    for line in unfold(raw):
        if property_name(line) == "UID":
            return line.split(":", 1)[1].strip()

    return None


def check_unique_uids(kind: str) -> None:
    """Fail before writing anything if two source files share a UID.

    The bridges key everything by UID, so the same assignment exported under
    two courses (or twice in one) would otherwise merge into one mirror item
    and flip between both sources on every run.
    """
    seen: dict[str, Path] = {}

    for course in courses.source_courses(ROOT, kind):
        for path in sorted((ROOT / course / kind).glob("*.ics")):
            uid = component_uid(path.read_bytes().decode("utf-8"))

            if uid is None:
                raise RuntimeError(f"{path}: no UID")

            if uid in seen:
                raise RuntimeError(
                    f"duplicate u_crawler UID {uid}: {seen[uid]} and {path}"
                )

            seen[uid] = path


def removal_allowed(
    target_dir: Path,
    doomed: list[str],
    managed_total: int,
) -> bool:
    if not doomed:
        return True

    limit = max(MASS_DELETE_MIN, int(managed_total * MASS_DELETE_SHARE))

    if len(doomed) <= limit:
        return True

    if os.environ.get("MERGE_ALLOW_MASS_DELETE") == "1":
        print(
            f"[u_crawler-merge] mass delete allowed by override: "
            f"{target_dir.name}: {len(doomed)}/{managed_total}",
            file=sys.stderr,
        )
        return True

    print(
        f"[u_crawler-merge] WARNING holding {len(doomed)}/{managed_total} "
        f"removals in {target_dir.name}: over the mass-delete limit "
        f"({limit}); set MERGE_ALLOW_MASS_DELETE=1 for one run if intended",
        file=sys.stderr,
    )
    return False


def sync_deadlines() -> tuple[int, int, int]:
    created = 0
    updated = 0
    removed = 0

    check_unique_uids("deadlines")

    for course in courses.source_courses(ROOT, "deadlines"):
        source_dir = ROOT / course / "deadlines"

        target_dir = ROOT / courses.canonical_name(course, "deadlines")
        target_dir.mkdir(parents=True, exist_ok=True)

        source_files = {
            path.name: path
            for path in source_dir.glob("*.ics")
        }

        target_files = {
            path.name: path
            for path in target_dir.glob("*.ics")
        }

        for filename, source_path in sorted(source_files.items()):
            target_path = target_dir / filename
            source_raw = source_path.read_bytes().decode("utf-8")

            if not target_path.exists():
                atomic_write(target_path, source_raw)
                created += 1
                continue

            existing_raw = target_path.read_bytes().decode("utf-8")

            try:
                merged_raw = merge_vtodo(
                    source_raw,
                    existing_raw,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"failed merging {source_path} -> {target_path}: {exc}"
                ) from exc

            if merged_raw != existing_raw:
                atomic_write(target_path, merged_raw)
                updated += 1

        # Sólo eliminamos archivos pertenecientes al esquema de u_crawler.
        # No tocamos cualquier otro ICS que pudiera existir en el calendario.
        managed = [
            filename
            for filename in sorted(target_files)
            if filename.startswith("assignment-")
        ]
        doomed = [
            filename for filename in managed if filename not in source_files
        ]

        if removal_allowed(target_dir, doomed, len(managed)):
            for filename in doomed:
                target_files[filename].unlink()
                removed += 1

    return created, updated, removed


def sync_windows() -> tuple[int, int]:
    updated = 0
    removed = 0

    check_unique_uids("windows")

    for course in courses.source_courses(ROOT, "windows"):
        source_dir = ROOT / course / "windows"

        target_dir = ROOT / courses.canonical_name(course, "windows")
        target_dir.mkdir(parents=True, exist_ok=True)

        source_files = {
            path.name: path
            for path in source_dir.glob("*.ics")
        }

        for filename, source_path in sorted(source_files.items()):
            target_path = target_dir / filename
            source_raw = source_path.read_bytes().decode("utf-8")

            if (
                not target_path.exists()
                or target_path.read_bytes().decode("utf-8") != source_raw
            ):
                atomic_write(target_path, source_raw)
                updated += 1

        managed = [
            path
            for path in sorted(target_dir.glob("*.ics"))
            if path.name.startswith("assignment-")
        ]
        doomed = [path for path in managed if path.name not in source_files]

        if removal_allowed(
            target_dir,
            [path.name for path in doomed],
            len(managed),
        ):
            for path in doomed:
                path.unlink()
                removed += 1

    return updated, removed


def main() -> int:
    deadline_created, deadline_updated, deadline_removed = sync_deadlines()
    window_updated, window_removed = sync_windows()

    print(
        "[u_crawler-merge] "
        f"deadlines: {deadline_created} created, "
        f"{deadline_updated} updated, "
        f"{deadline_removed} removed; "
        f"windows: {window_updated} updated, "
        f"{window_removed} removed"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
