#!/usr/bin/env python3
"""One-off repair: keep one canonical file per managed UID, quarantine the rest.

On 2026-10-29 caldir (fork `5bf58e0`) wrote canonical files back under its own
slug, and `merge-ucrawler.py` (then matching by filename) re-created
`assignment-<id>.ics` beside each one. Every managed UID ended up in two
files, caldir pushed the re-created copies (without the completion the bridge
had brought in from Google) and wrote them back over the other copy, and
`bridge-vtodo.py` stopped on the duplicate UID.

The last successful bridge run recorded a semantic hash of every canonical
item. The keeper is the copy whose hash matches it. When no copy does, the
copy that matches once its STATUS, COMPLETED and PERCENT-COMPLETE are taken
from the Google Tasks mirror is kept, with those fields restored. A UID with
no provable keeper is left alone and the script exits 1: guessing here would
reach Google as a user edit. `caldir/dedupe-vtodo.py` prefers
`assignment-<id>.ics` and would keep the wrong copy.

Moves files into `<quarantine>/<collection>/` and rewrites only the keepers it
restores; deletes nothing. Dry run unless `--apply`.

    quarantine-duplicate-canonical.py <calendars dir> <state dir> <quarantine dir> [--apply]
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path

CALDIR = Path(__file__).resolve().parents[1] / "caldir"

USER_STATE_FIELDS = ("STATUS", "COMPLETED", "PERCENT-COMPLETE")


def load_bridge(name: str):
    sys.path.insert(0, str(CALDIR))
    spec = importlib.util.spec_from_file_location(name, CALDIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)

    return module


VTODO = load_bridge("bridge-vtodo")
VEVENT = load_bridge("bridge-windows")

KINDS = (
    ("deadlines", VTODO, VTODO.parse_vtodo, "vtodo-bridge-state.json"),
    ("windows", VEVENT, VEVENT.parse_vevent, "windows-bridge-state.json"),
)


def uid_of(item) -> str:
    return item.props["UID"][0].split(":", 1)[1].strip()


def with_google_state(item, google) -> str:
    """The item's file with its user-state fields replaced by Google's."""
    lines = VTODO.unfold(item.raw)
    start, end = VTODO.find_vtodo(lines)
    body = [
        line
        for line in lines[start + 1 : end]
        if VTODO.property_name(line) not in USER_STATE_FIELDS
    ]

    for field in USER_STATE_FIELDS:
        body.extend(google.props.get(field, []))

    merged = lines[: start + 1] + body + lines[end:]

    return "\r\n".join(merged).rstrip("\r\n") + "\r\n"


def keeper_rank(item) -> tuple[bool, float, str]:
    return (
        item.path.name.startswith("assignment-"),
        item.path.stat().st_mtime,
        item.path.name,
    )


def plan(calendars: Path, state_dir: Path):
    """(uid, keeper, restored raw or None, rest) per duplicate; unprovable UIDs."""
    planned = []
    unprovable = []
    google = {
        uid_of(item): item
        for item in map(VTODO.parse_vtodo, (calendars / "unab").glob("*.ics"))
    }

    for kind, bridge, parse, state_file in KINDS:
        state = json.loads((state_dir / state_file).read_text())["items"]

        for collection in sorted(calendars.glob(f"*__{kind}")):
            groups = defaultdict(list)

            for path in sorted(collection.glob("*.ics")):
                item = parse(path)

                if uid_of(item).startswith(bridge.MANAGED_UID_PREFIX):
                    groups[uid_of(item)].append(item)

            for uid, items in sorted(groups.items()):
                if len(items) < 2:
                    continue

                recorded = state.get(uid, {}).get("canonical_hash")
                matching = [i for i in items if bridge.semantic_hash(i) == recorded]
                restored = None

                if not matching and kind == "deadlines" and uid in google:
                    for item in items:
                        raw = with_google_state(item, google[uid])
                        candidate = VTODO.Item(
                            path=item.path,
                            raw=raw,
                            props=parse_raw(raw),
                        )

                        if VTODO.semantic_hash(candidate) == recorded:
                            matching, restored = [item], raw
                            break

                if not matching:
                    unprovable.append((uid, collection.name))
                    continue

                keeper = min(matching, key=keeper_rank)
                rest = [i.path for i in items if i is not keeper]
                planned.append((uid, keeper.path, restored, rest))

    return planned, unprovable


def parse_raw(raw: str) -> dict[str, list[str]]:
    lines = VTODO.unfold(raw)
    start, end = VTODO.find_vtodo(lines)
    props: dict[str, list[str]] = {}

    for line in lines[start + 1 : end]:
        name = VTODO.property_name(line)

        if name is not None:
            props.setdefault(name, []).append(line)

    return props


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4) or argv[3:] not in ([], ["--apply"]):
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 64

    calendars, state_dir, quarantine = map(Path, argv[:3])
    apply = argv[3:] == ["--apply"]

    planned, unprovable = plan(calendars, state_dir)

    for uid, collection in unprovable:
        print(f"SKIP {uid} in {collection}: no copy matches the bridge state",
              file=sys.stderr)

    for uid, keeper, restored, rest in planned:
        verb = "quarantine" if apply else "would quarantine"
        how = "keep, restoring Google's completion," if restored else "keep"
        names = ", ".join(path.name for path in rest)
        print(f"{uid}: {how} {keeper.name}; {verb} {names}")

        if not apply:
            continue

        target_dir = quarantine / keeper.parent.name
        target_dir.mkdir(parents=True, exist_ok=True)

        for path in rest:
            target = target_dir / path.name

            if target.exists():
                print(f"skip {path}: {target} exists", file=sys.stderr)
                continue

            # The quarantine may be another mount: move, not rename.
            shutil.move(path, target)

        if restored is not None:
            shutil.copy2(keeper, target_dir / f"{keeper.name}.before-restore")
            VTODO.atomic_write(keeper, restored)

    return 1 if unprovable else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
