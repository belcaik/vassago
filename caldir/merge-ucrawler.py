#!/usr/bin/env python3

from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(os.environ.get("CALENDAR_ROOT", "/data/calendars"))

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


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    temp = path.with_suffix(path.suffix + ".tmp")

    temp.write_text(
        content,
        encoding="utf-8",
        newline="",
    )

    os.replace(temp, path)


def sync_deadlines() -> tuple[int, int, int]:
    created = 0
    updated = 0
    removed = 0

    for course_dir in sorted(ROOT.glob("202615_*")):
        source_dir = course_dir / "deadlines"

        if not source_dir.is_dir():
            continue

        target_dir = ROOT / f"{course_dir.name}__deadlines"
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
            source_raw = source_path.read_text(encoding="utf-8")

            if not target_path.exists():
                atomic_write(target_path, source_raw)
                created += 1
                continue

            existing_raw = target_path.read_text(encoding="utf-8")

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
        for filename, target_path in sorted(target_files.items()):
            if not filename.startswith("assignment-"):
                continue

            if filename not in source_files:
                target_path.unlink()
                removed += 1

    return created, updated, removed


def sync_windows() -> tuple[int, int]:
    updated = 0
    removed = 0

    for course_dir in sorted(ROOT.glob("202615_*")):
        source_dir = course_dir / "windows"

        if not source_dir.is_dir():
            continue

        target_dir = ROOT / f"{course_dir.name}__windows"
        target_dir.mkdir(parents=True, exist_ok=True)

        source_files = {
            path.name: path
            for path in source_dir.glob("*.ics")
        }

        for filename, source_path in sorted(source_files.items()):
            target_path = target_dir / filename
            source_raw = source_path.read_text(encoding="utf-8")

            if (
                not target_path.exists()
                or target_path.read_text(encoding="utf-8") != source_raw
            ):
                atomic_write(target_path, source_raw)
                updated += 1

        for target_path in sorted(target_dir.glob("*.ics")):
            if not target_path.name.startswith("assignment-"):
                continue

            if target_path.name not in source_files:
                target_path.unlink()
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
