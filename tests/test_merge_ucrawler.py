"""Pin the current behaviour of caldir/merge-ucrawler.py."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _load import load_script, write


def vtodo(uid="u_crawler-todo-1@u-crawler.local", summary="Assignment 1",
          extra=""):
    body = (
        "BEGIN:VCALENDAR\n"
        "VERSION:2.0\n"
        "PRODID:-//u_crawler//EN\n"
        "BEGIN:VTODO\n"
        f"UID:{uid}\n"
        "DTSTAMP:20260101T000000Z\n"
        f"SUMMARY:{summary}\n"
        "DUE:20260830T235900Z\n"
    )
    if extra:
        body += extra.rstrip("\n") + "\n"
    return body + "END:VTODO\nEND:VCALENDAR\n"


def vevent(uid="u_crawler-window-1@u-crawler.local", summary="Window 1"):
    return (
        "BEGIN:VCALENDAR\n"
        "VERSION:2.0\n"
        "BEGIN:VEVENT\n"
        f"UID:{uid}\n"
        "DTSTAMP:20260101T000000Z\n"
        f"SUMMARY:{summary}\n"
        "DTSTART:20260830T120000Z\n"
        "DTEND:20260830T235900Z\n"
        "END:VEVENT\n"
        "END:VCALENDAR\n"
    )


class PureHelpersTest(unittest.TestCase):
    """Helpers that do not touch the filesystem; ROOT is irrelevant here."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.mod = load_script(
            "merge-ucrawler.py",
            {"CALENDAR_ROOT": cls.tmp.name},
        )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_unfold_joins_continuation_lines(self):
        raw = "SUMMARY:Long\r\n  tail\r\nUID:x\r\n"
        self.assertEqual(
            self.mod.unfold(raw),
            ["SUMMARY:Long tail", "UID:x", ""],
        )

    def test_unfold_accepts_lone_cr_and_tab_continuations(self):
        raw = "A:1\r\tmore\rB:2"
        self.assertEqual(self.mod.unfold(raw), ["A:1more", "B:2"])

    def test_merge_vtodo_keeps_source_content(self):
        source = vtodo(summary="New title")
        existing = vtodo(summary="Old title")

        merged = self.mod.merge_vtodo(source, existing)

        self.assertIn("SUMMARY:New title", merged)
        self.assertNotIn("Old title", merged)

    def test_merge_vtodo_preserves_existing_user_state(self):
        source = vtodo(extra="STATUS:NEEDS-ACTION")
        existing = vtodo(
            extra=(
                "STATUS:COMPLETED\n"
                "COMPLETED:20260201T101500Z\n"
                "PERCENT-COMPLETE:100"
            )
        )

        merged = self.mod.merge_vtodo(source, existing)

        self.assertIn("STATUS:COMPLETED", merged)
        self.assertIn("COMPLETED:20260201T101500Z", merged)
        self.assertIn("PERCENT-COMPLETE:100", merged)
        self.assertNotIn("STATUS:NEEDS-ACTION", merged)

    def test_merge_vtodo_drops_user_state_absent_from_existing(self):
        source = vtodo(extra="STATUS:NEEDS-ACTION")
        existing = vtodo()

        merged = self.mod.merge_vtodo(source, existing)

        self.assertNotIn("STATUS:", merged)

    def test_merge_vtodo_output_is_crlf_with_single_trailing_crlf(self):
        merged = self.mod.merge_vtodo(vtodo(), vtodo())

        self.assertTrue(merged.endswith("END:VCALENDAR\r\n"))
        self.assertFalse(merged.endswith("\r\n\r\n"))
        # Every LF is part of a CRLF pair: no bare LF survives.
        self.assertEqual(merged.count("\n"), merged.count("\r\n"))


class SyncDeadlinesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.mod = load_script(
            "merge-ucrawler.py",
            {"CALENDAR_ROOT": str(self.root)},
        )
        self.source = self.root / "202615_course" / "deadlines"
        self.target = self.root / "202615_course__deadlines"

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_source_file_is_created(self):
        write(self.source / "assignment-1.ics", vtodo())

        self.assertEqual(self.mod.sync_deadlines(), (1, 0, 0))
        self.assertTrue((self.target / "assignment-1.ics").exists())

    def test_merge_rewrites_every_run_because_of_line_endings(self):
        # `merge_vtodo` emits CRLF, but the comparison is against
        # `read_text()`, which has already collapsed CRLF to LF. The two never
        # compare equal, so an unchanged deadline counts as "updated" on every
        # run. Content-wise the file is stable.
        write(self.source / "assignment-1.ics", vtodo())
        self.mod.sync_deadlines()

        self.assertEqual(self.mod.sync_deadlines(), (0, 1, 0))
        first = (self.target / "assignment-1.ics").read_bytes()

        self.assertEqual(self.mod.sync_deadlines(), (0, 1, 0))
        self.assertEqual(
            first,
            (self.target / "assignment-1.ics").read_bytes(),
        )
        self.assertIn(b"\r\n", first)

    def test_changed_source_updates_target(self):
        write(self.source / "assignment-1.ics", vtodo())
        self.mod.sync_deadlines()
        write(self.source / "assignment-1.ics", vtodo(summary="Renamed"))

        self.assertEqual(self.mod.sync_deadlines(), (0, 1, 0))
        self.assertIn(
            "SUMMARY:Renamed",
            (self.target / "assignment-1.ics").read_text(encoding="utf-8"),
        )

    def test_update_preserves_user_state_in_target(self):
        write(self.source / "assignment-1.ics", vtodo())
        self.mod.sync_deadlines()
        write(
            self.target / "assignment-1.ics",
            vtodo(extra="STATUS:COMPLETED"),
        )
        write(self.source / "assignment-1.ics", vtodo(summary="Renamed"))

        self.mod.sync_deadlines()
        merged = (self.target / "assignment-1.ics").read_text(encoding="utf-8")

        self.assertIn("SUMMARY:Renamed", merged)
        self.assertIn("STATUS:COMPLETED", merged)

    def test_removes_only_assignment_files_missing_from_source(self):
        write(self.source / "assignment-1.ics", vtodo())
        self.mod.sync_deadlines()
        personal = write(self.target / "personal.ics", vtodo(uid="me@local"))
        (self.source / "assignment-1.ics").unlink()

        self.assertEqual(self.mod.sync_deadlines(), (0, 0, 1))
        self.assertFalse((self.target / "assignment-1.ics").exists())
        self.assertTrue(personal.exists())

    def test_course_without_deadlines_dir_is_skipped(self):
        (self.root / "202615_course" / "windows").mkdir(parents=True)

        self.assertEqual(self.mod.sync_deadlines(), (0, 0, 0))
        self.assertFalse(self.target.exists())


class SyncWindowsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.mod = load_script(
            "merge-ucrawler.py",
            {"CALENDAR_ROOT": str(self.root)},
        )
        self.source = self.root / "202615_course" / "windows"
        self.target = self.root / "202615_course__windows"

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_file_is_copied(self):
        write(self.source / "assignment-1.ics", vevent())

        self.assertEqual(self.mod.sync_windows(), (1, 0))
        self.assertTrue((self.target / "assignment-1.ics").exists())

    def test_identical_file_is_left_alone(self):
        write(self.source / "assignment-1.ics", vevent())
        self.mod.sync_windows()

        self.assertEqual(self.mod.sync_windows(), (0, 0))

    def test_local_edit_is_overwritten_unconditionally(self):
        write(self.source / "assignment-1.ics", vevent())
        self.mod.sync_windows()
        write(
            self.target / "assignment-1.ics",
            vevent(summary="Edited by hand"),
        )

        self.assertEqual(self.mod.sync_windows(), (1, 0))
        self.assertEqual(
            (self.target / "assignment-1.ics").read_text(encoding="utf-8"),
            (self.source / "assignment-1.ics").read_text(encoding="utf-8"),
        )

    def test_removes_only_assignment_files_missing_from_source(self):
        write(self.source / "assignment-1.ics", vevent())
        self.mod.sync_windows()
        personal = write(self.target / "personal.ics", vevent(uid="me@local"))
        (self.source / "assignment-1.ics").unlink()

        self.assertEqual(self.mod.sync_windows(), (0, 1))
        self.assertTrue(personal.exists())


if __name__ == "__main__":
    unittest.main()
