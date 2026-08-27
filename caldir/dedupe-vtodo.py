#!/usr/bin/env python3

from __future__ import annotations

import re
import shutil
from collections import defaultdict
from pathlib import Path


ROOT = Path("/data/calendars")
QUARANTINE = Path("/data/.local/share/caldir/quarantine-vtodo")

UID_RE = re.compile(
    r"^UID:(u_crawler-todo-(\d+)@u-crawler\.local)\s*$",
    re.MULTILINE,
)


def uid_of(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    match = UID_RE.search(text)

    if not match:
        return None

    return match.group(1), match.group(2)


def main() -> int:
    QUARANTINE.mkdir(parents=True, exist_ok=True)

    groups_seen = 0
    files_quarantined = 0

    for canonical_dir in sorted(ROOT.glob("202615_*__deadlines")):
        course = canonical_dir.name.removesuffix("__deadlines")
        source_dir = ROOT / course / "deadlines"

        if not source_dir.is_dir():
            continue

        by_uid: dict[str, list[Path]] = defaultdict(list)

        for path in canonical_dir.glob("*.ics"):
            parsed = uid_of(path)
            if parsed:
                uid, _ = parsed
                by_uid[uid].append(path)

        for uid, files in sorted(by_uid.items()):
            if len(files) <= 1:
                continue

            groups_seen += 1

            assignment_id = uid.removeprefix(
                "u_crawler-todo-"
            ).removesuffix(
                "@u-crawler.local"
            )

            preferred_name = f"assignment-{assignment_id}.ics"
            preferred = canonical_dir / preferred_name
            source = source_dir / preferred_name

            if preferred.exists():
                keeper = preferred

            elif source.exists():
                # Recreate canonical filename from authoritative u_crawler
                # source. merge-ucrawler will reconcile fields afterwards.
                shutil.copy2(source, preferred)
                keeper = preferred

            else:
                # Fail closed: no authoritative source means we do not
                # arbitrarily decide which duplicate is canonical.
                print(
                    f"[dedupe] SKIP {uid}: "
                    "authoritative source not found"
                )
                continue

            print(f"[dedupe] {uid}")
            print(f"  keep: {keeper.name}")

            qdir = QUARANTINE / course / assignment_id
            qdir.mkdir(parents=True, exist_ok=True)

            for path in files:
                if path == keeper:
                    continue

                destination = qdir / path.name

                counter = 1
                while destination.exists():
                    destination = qdir / (
                        f"{path.stem}.{counter}{path.suffix}"
                    )
                    counter += 1

                shutil.move(path, destination)

                print(
                    f"  quarantine: {path.name}"
                )

                files_quarantined += 1

    print(
        "[dedupe] done: "
        f"{groups_seen} duplicate groups, "
        f"{files_quarantined} files quarantined"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
