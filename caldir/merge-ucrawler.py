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

# caldir re-serializes and renames a canonical file whenever it writes one
# (a pull, or the write-back after a push): the name becomes its own slug and
# DTSTAMP moves. Canonical files are therefore matched to their source by UID,
# and compared without DTSTAMP, so neither change reads as an edit.
VOLATILE_FIELDS = {"DTSTAMP"}

MANAGED_UID_PREFIXES = {
    "deadlines": "u_crawler-todo-",
    "windows": "u_crawler-window-",
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


def same_content(raw: str, other: str, component: str) -> bool:
    """Whether two files carry the same component, ignoring serialization.

    Property order, line endings, folding, DTSTAMP and trailing blanks (caldir
    trims SUMMARY) are not content.
    """

    def signature(text: str) -> list[str]:
        lines = unfold(text)
        start, end = find_component(lines, component)

        return sorted(
            line.rstrip()
            for line in lines[start + 1 : end]
            if property_name(line) not in VOLATILE_FIELDS | {None}
        )

    return signature(raw) == signature(other)


def files_by_uid(directory: Path) -> dict[str, Path]:
    """Canonical files by UID, whatever caldir named them.

    Two files with one UID abort the run before anything is written: the
    bridges would refuse them anyway, and merging into either would be a guess.
    """
    found: dict[str, Path] = {}

    for path in sorted(directory.glob("*.ics")):
        uid = component_uid(path.read_bytes().decode("utf-8"))

        if uid is None:
            continue

        if uid in found:
            raise RuntimeError(
                f"duplicate canonical UID {uid}: {found[uid]} and {path}"
            )

        found[uid] = path

    return found


def sources_by_uid(source_dir: Path) -> dict[str, Path]:
    return {
        component_uid(path.read_bytes().decode("utf-8")): path
        for path in sorted(source_dir.glob("*.ics"))
    }


def new_target(target_dir: Path, source_path: Path) -> Path:
    target_path = target_dir / source_path.name

    # Another item already holds the source's name: never write over it.
    if target_path.exists():
        raise RuntimeError(
            f"{target_path} exists under another UID; "
            f"refusing to create {source_path.name} over it"
        )

    return target_path


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

        source_files = sources_by_uid(source_dir)
        target_files = files_by_uid(target_dir)

        for uid, source_path in sorted(source_files.items()):
            source_raw = source_path.read_bytes().decode("utf-8")

            if uid not in target_files:
                atomic_write(new_target(target_dir, source_path), source_raw)
                created += 1
                continue

            target_path = target_files[uid]
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

            if not same_content(merged_raw, existing_raw, "VTODO"):
                atomic_write(target_path, merged_raw)
                updated += 1

        # Sólo eliminamos archivos pertenecientes al esquema de u_crawler.
        # No tocamos cualquier otro ICS que pudiera existir en el calendario.
        managed = [
            uid
            for uid in sorted(target_files)
            if uid.startswith(MANAGED_UID_PREFIXES["deadlines"])
        ]
        doomed = [uid for uid in managed if uid not in source_files]

        if removal_allowed(
            target_dir,
            [target_files[uid].name for uid in doomed],
            len(managed),
        ):
            for uid in doomed:
                target_files[uid].unlink()
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

        source_files = sources_by_uid(source_dir)
        target_files = files_by_uid(target_dir)

        for uid, source_path in sorted(source_files.items()):
            source_raw = source_path.read_bytes().decode("utf-8")

            if uid not in target_files:
                atomic_write(new_target(target_dir, source_path), source_raw)
                updated += 1
                continue

            target_path = target_files[uid]

            if not same_content(
                source_raw,
                target_path.read_bytes().decode("utf-8"),
                "VEVENT",
            ):
                atomic_write(target_path, source_raw)
                updated += 1

        managed = [
            uid
            for uid in sorted(target_files)
            if uid.startswith(MANAGED_UID_PREFIXES["windows"])
        ]
        doomed = [uid for uid in managed if uid not in source_files]

        if removal_allowed(
            target_dir,
            [target_files[uid].name for uid in doomed],
            len(managed),
        ):
            for uid in doomed:
                target_files[uid].unlink()
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
