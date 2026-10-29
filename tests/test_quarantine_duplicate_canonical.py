"""scripts/quarantine-duplicate-canonical.py: the 2026-10-29 one-off repair."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from _load import REPO_ROOT, load_script, write

SCRIPT = REPO_ROOT / "scripts" / "quarantine-duplicate-canonical.py"

VTODO = load_script("bridge-vtodo.py")
VEVENT = load_script("bridge-windows.py")

UID = "u_crawler-todo-1@u-crawler.local"
WINDOW_UID = "u_crawler-window-1@u-crawler.local"


def task(uid=UID, state="", dtstamp="20260101T000000Z"):
    return (
        "BEGIN:VCALENDAR\nBEGIN:VTODO\n"
        f"UID:{uid}\nDTSTAMP:{dtstamp}\nSUMMARY:Task\nDUE:20260830T235900Z\n"
        f"{state}"
        "END:VTODO\nEND:VCALENDAR\n"
    )


DONE = "STATUS:COMPLETED\nCOMPLETED:20260901T120000Z\n"


def window(uid=WINDOW_UID, summary="Window"):
    return (
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\n"
        f"UID:{uid}\nSUMMARY:{summary}\nDTSTART:20260830T120000Z\n"
        "END:VEVENT\nEND:VCALENDAR\n"
    )


class QuarantineDuplicateCanonicalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.calendars = root / "calendars"
        self.state = root / "state"
        self.quarantine = root / "quarantine"
        self.deadlines = self.calendars / "202615_c__deadlines"
        self.windows = self.calendars / "202625_c__windows"
        self.mirror = self.calendars / "unab"
        self.mirror.mkdir(parents=True)
        self.recorded = {"vtodo": {}, "windows": {}}
        self.save_state()

    def tearDown(self):
        self.tmp.cleanup()

    def record(self, kind, uid, raw):
        path = Path(self.tmp.name) / "recorded.ics"
        write(path, raw)
        bridge, parse = (
            (VTODO, VTODO.parse_vtodo) if kind == "vtodo" else (VEVENT, VEVENT.parse_vevent)
        )
        self.recorded[kind][uid] = {"canonical_hash": bridge.semantic_hash(parse(path))}
        self.save_state()

    def save_state(self):
        self.state.mkdir(exist_ok=True)
        for kind, name in (("vtodo", "vtodo-bridge-state.json"), ("windows", "windows-bridge-state.json")):
            (self.state / name).write_text(json.dumps({"items": self.recorded[kind]}))

    def run_script(self, *args, check=True):
        return subprocess.run(
            ["python3", str(SCRIPT), str(self.calendars), str(self.state),
             str(self.quarantine), *args],
            capture_output=True,
            text=True,
            check=check,
        )

    def names(self, directory):
        return sorted(path.name for path in directory.glob("*.ics"))

    def test_dry_run_moves_nothing(self):
        self.record("vtodo", UID, task(state=DONE))
        write(self.deadlines / "_task__a.ics", task(state=DONE, dtstamp="20261029T012500Z"))
        write(self.deadlines / "assignment-1.ics", task())

        result = self.run_script()

        self.assertIn(f"{UID}: keep _task__a.ics; would quarantine assignment-1.ics", result.stdout)
        self.assertEqual(self.names(self.deadlines), ["_task__a.ics", "assignment-1.ics"])

    def test_the_copy_matching_the_bridge_state_is_kept(self):
        # The open copy is the older caldir one; the state says completed.
        self.record("vtodo", UID, task(state=DONE))
        old = write(self.deadlines / "_task__a.ics", task())
        os.utime(old, (100, 100))
        write(self.deadlines / "assignment-1.ics", task(state=DONE))

        self.run_script("--apply")

        self.assertEqual(self.names(self.deadlines), ["assignment-1.ics"])
        self.assertEqual(self.names(self.quarantine / self.deadlines.name), ["_task__a.ics"])

    def test_completion_lost_in_both_copies_is_restored_from_google(self):
        self.record("vtodo", UID, task(state=DONE))
        write(self.deadlines / "_task__a.ics", task())
        write(self.deadlines / "assignment-1.ics", task())
        write(self.mirror / "g.ics", task(state=DONE + "X-GOOGLE-TASK-ID:abc\n"))

        result = self.run_script("--apply")

        self.assertIn("restoring Google's completion", result.stdout)
        self.assertEqual(self.names(self.deadlines), ["_task__a.ics"])
        kept = VTODO.parse_vtodo(self.deadlines / "_task__a.ics")
        self.assertEqual(VTODO.semantic_hash(kept), self.recorded["vtodo"][UID]["canonical_hash"])
        self.assertTrue(
            (self.quarantine / self.deadlines.name / "_task__a.ics.before-restore").exists()
        )

    def test_a_uid_with_no_provable_keeper_is_left_alone(self):
        self.record("vtodo", UID, task(state=DONE))
        write(self.deadlines / "_task__a.ics", task())
        write(self.deadlines / "assignment-1.ics", task())

        result = self.run_script("--apply", check=False)

        self.assertEqual(result.returncode, 1)
        self.assertIn(f"SKIP {UID}", result.stderr)
        self.assertEqual(self.names(self.deadlines), ["_task__a.ics", "assignment-1.ics"])

    def test_windows_keep_the_caldir_copy_matching_the_state(self):
        self.record("windows", WINDOW_UID, window())
        write(self.windows / "2026-08-30__w.ics", window())
        write(self.windows / "assignment-1.ics", window())

        self.run_script("--apply")

        self.assertEqual(self.names(self.windows), ["2026-08-30__w.ics"])

    def test_unique_and_personal_items_are_untouched(self):
        write(self.deadlines / "assignment-1.ics", task())
        write(self.deadlines / "me-1.ics", task(uid="me@local"))
        write(self.deadlines / "me-2.ics", task(uid="me@local"))

        result = self.run_script("--apply")

        self.assertEqual(result.stdout, "")
        self.assertEqual(
            self.names(self.deadlines), ["assignment-1.ics", "me-1.ics", "me-2.ics"]
        )


if __name__ == "__main__":
    unittest.main()
