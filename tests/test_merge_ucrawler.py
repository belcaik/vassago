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


def caldir_copy(raw, dtstamp="20261029T012500Z"):
    """The same item as caldir writes it back: CRLF, new DTSTAMP, own order."""
    lines = raw.rstrip("\n").split("\n")
    lines = [
        f"DTSTAMP:{dtstamp}" if line.startswith("DTSTAMP:") else line
        for line in lines
    ]
    begin = next(i for i, line in enumerate(lines) if line.startswith("BEGIN:V") and line != "BEGIN:VCALENDAR")
    end = next(i for i, line in enumerate(lines) if line.startswith("END:V") and line != "END:VCALENDAR")
    body = list(reversed(lines[begin + 1 : end]))
    return "\r\n".join(lines[: begin + 1] + body + lines[end:]) + "\r\n"


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

    def test_unchanged_deadline_is_not_rewritten(self):
        # Regression: the comparison used read_text(), which collapses CRLF,
        # so every deadline was rewritten and counted "updated" on every run.
        write(self.source / "assignment-1.ics", vtodo())
        self.assertEqual(self.mod.sync_deadlines(), (1, 0, 0))
        first = (self.target / "assignment-1.ics").read_bytes()

        self.assertEqual(self.mod.sync_deadlines(), (0, 0, 0))
        self.assertEqual(self.mod.sync_deadlines(), (0, 0, 0))
        self.assertEqual(
            first,
            (self.target / "assignment-1.ics").read_bytes(),
        )
        self.assertIn(b"\r\n", first)

    def test_lf_target_with_the_same_content_is_left_alone(self):
        # Line endings are serialization, not content: no rewrite, no push.
        write(self.source / "assignment-1.ics", vtodo())
        self.target.mkdir(parents=True)
        (self.target / "assignment-1.ics").write_bytes(vtodo().encode())

        self.assertEqual(self.mod.sync_deadlines(), (0, 0, 0))

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
        write(
            self.source / "assignment-2.ics",
            vtodo(uid="u_crawler-todo-2@u-crawler.local"),
        )
        self.mod.sync_deadlines()
        personal = write(self.target / "personal.ics", vtodo(uid="me@local"))
        (self.source / "assignment-1.ics").unlink()

        self.assertEqual(self.mod.sync_deadlines(), (0, 0, 1))
        self.assertFalse((self.target / "assignment-1.ics").exists())
        self.assertTrue(personal.exists())

    def test_file_renamed_by_caldir_is_matched_by_uid(self):
        # Production 2026-10-29: caldir wrote the canonical file back under
        # its own slug; matching by filename re-created assignment-1.ics next
        # to it, and the bridge stopped on the duplicate UID.
        write(self.source / "assignment-1.ics", vtodo())
        renamed = write(
            self.target / "_task__2026-08-30T2359__assignment-1.ics",
            caldir_copy(vtodo(extra="STATUS:COMPLETED")),
        )
        before = renamed.read_bytes()

        self.assertEqual(self.mod.sync_deadlines(), (0, 0, 0))
        self.assertEqual(sorted(p.name for p in self.target.glob("*.ics")), [renamed.name])
        self.assertEqual(renamed.read_bytes(), before)

    def test_source_change_updates_the_renamed_file_in_place(self):
        write(self.source / "assignment-1.ics", vtodo(summary="Renamed"))
        renamed = write(
            self.target / "_task__2026-08-30T2359__assignment-1.ics",
            caldir_copy(vtodo(extra="STATUS:COMPLETED")),
        )

        self.assertEqual(self.mod.sync_deadlines(), (0, 1, 0))
        self.assertEqual(sorted(p.name for p in self.target.glob("*.ics")), [renamed.name])
        merged = renamed.read_text(encoding="utf-8")
        self.assertIn("SUMMARY:Renamed", merged)
        self.assertIn("STATUS:COMPLETED", merged)

    def test_trailing_blanks_caldir_trims_are_not_an_edit(self):
        write(self.source / "assignment-1.ics", vtodo(summary="Caso   "))
        write(self.target / "_task__x.ics", caldir_copy(vtodo(summary="Caso")))

        self.assertEqual(self.mod.sync_deadlines(), (0, 0, 0))

    def test_duplicate_canonical_uid_aborts_before_writing(self):
        write(self.source / "assignment-1.ics", vtodo(summary="Renamed"))
        first = write(self.target / "assignment-1.ics", vtodo())
        second = write(self.target / "_task__x.ics", caldir_copy(vtodo()))

        with self.assertRaisesRegex(RuntimeError, "duplicate canonical UID"):
            self.mod.sync_deadlines()

        self.assertNotIn("Renamed", first.read_text(encoding="utf-8"))
        self.assertNotIn("Renamed", second.read_text(encoding="utf-8"))

    def test_a_file_of_another_uid_under_the_source_name_is_not_overwritten(self):
        write(self.source / "assignment-1.ics", vtodo())
        other = write(self.target / "assignment-1.ics", vtodo(uid="me@local"))

        with self.assertRaisesRegex(RuntimeError, "under another UID"):
            self.mod.sync_deadlines()

        self.assertIn("UID:me@local", other.read_text(encoding="utf-8"))

    def test_renamed_file_of_an_assignment_gone_from_source_is_removed(self):
        write(self.source / "assignment-1.ics", vtodo())
        write(
            self.source / "assignment-2.ics",
            vtodo(uid="u_crawler-todo-2@u-crawler.local"),
        )
        self.mod.sync_deadlines()
        renamed = write(
            self.target / "_task__assignment-2.ics",
            caldir_copy(vtodo(uid="u_crawler-todo-2@u-crawler.local")),
        )
        (self.target / "assignment-2.ics").unlink()
        (self.source / "assignment-2.ics").unlink()

        self.assertEqual(self.mod.sync_deadlines(), (0, 0, 1))
        self.assertFalse(renamed.exists())

    def test_emptied_source_is_not_a_deletion(self):
        write(self.source / "assignment-1.ics", vtodo())
        self.mod.sync_deadlines()
        (self.source / "assignment-1.ics").unlink()

        self.assertEqual(self.mod.sync_deadlines(), (0, 0, 0))
        self.assertTrue((self.target / "assignment-1.ics").exists())

    def test_course_without_deadlines_dir_is_skipped(self):
        (self.root / "202615_course" / "windows").mkdir(parents=True)

        self.assertEqual(self.mod.sync_deadlines(), (0, 0, 0))
        self.assertFalse(self.target.exists())

    def test_every_term_is_merged_in_one_run(self):
        # During a semester transition both terms are processed in one run
        # (ADR-0008's intent). Discovery is structural (ADR-0009), so a term
        # nobody listed is merged too instead of being silently skipped.
        write(self.source / "assignment-1.ics", vtodo())

        new_term = self.root / "202625_other" / "deadlines"
        write(new_term / "assignment-2.ics", vtodo(uid="u_crawler-todo-2@u-crawler.local"))

        other_term = self.root / "199901_old" / "deadlines"
        write(other_term / "assignment-3.ics", vtodo(uid="u_crawler-todo-3@u-crawler.local"))

        self.assertEqual(self.mod.sync_deadlines(), (3, 0, 0))
        self.assertTrue((self.target / "assignment-1.ics").exists())
        self.assertTrue(
            (self.root / "202625_other__deadlines" / "assignment-2.ics").exists()
        )
        self.assertTrue(
            (self.root / "199901_old__deadlines" / "assignment-3.ics").exists()
        )


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

    def test_file_renamed_by_caldir_is_matched_by_uid(self):
        write(self.source / "assignment-1.ics", vevent())
        renamed = write(
            self.target / "2026-08-30T1200__window-1.ics",
            caldir_copy(vevent()),
        )

        self.assertEqual(self.mod.sync_windows(), (0, 0))
        self.assertEqual(sorted(p.name for p in self.target.glob("*.ics")), [renamed.name])

    def test_source_change_overwrites_the_renamed_file_in_place(self):
        write(self.source / "assignment-1.ics", vevent(summary="Moved"))
        renamed = write(
            self.target / "2026-08-30T1200__window-1.ics",
            caldir_copy(vevent()),
        )

        self.assertEqual(self.mod.sync_windows(), (1, 0))
        self.assertEqual(sorted(p.name for p in self.target.glob("*.ics")), [renamed.name])
        self.assertIn("SUMMARY:Moved", renamed.read_text(encoding="utf-8"))

    def test_removes_only_assignment_files_missing_from_source(self):
        write(self.source / "assignment-1.ics", vevent())
        write(
            self.source / "assignment-2.ics",
            vevent(uid="u_crawler-window-2@u-crawler.local"),
        )
        self.mod.sync_windows()
        personal = write(self.target / "personal.ics", vevent(uid="me@local"))
        (self.source / "assignment-1.ics").unlink()

        self.assertEqual(self.mod.sync_windows(), (0, 1))
        self.assertTrue(personal.exists())


if __name__ == "__main__":
    unittest.main()
