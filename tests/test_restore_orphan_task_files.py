"""scripts/restore-orphan-task-files.py: the 2026-10 one-off migration."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from _load import REPO_ROOT, write

SCRIPT = REPO_ROOT / "scripts" / "restore-orphan-task-files.py"


def task(uid):
    return (
        "BEGIN:VCALENDAR\r\nBEGIN:VTODO\r\n"
        f"UID:{uid}\r\nSUMMARY:Task\r\nX-GOOGLE-TASK-ID:x\r\n"
        "END:VTODO\r\nEND:VCALENDAR\r\n"
    )


class RestoreOrphanTaskFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mirror = Path(self.tmp.name) / "unab"
        self.bases = self.mirror / ".caldir" / "state" / "bases"
        self.bases.mkdir(parents=True)

        write(self.bases / "a", task("orphan@google-tasks.com"))
        write(self.bases / "b", task("present@google-tasks.com"))
        write(self.bases / "c", task("u_crawler-todo-1@u-crawler.local"))
        write(self.mirror / "present.ics", task("present@google-tasks.com"))

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, *args):
        return subprocess.run(
            ["python3", str(SCRIPT), str(self.mirror), *args],
            capture_output=True,
            text=True,
            check=True,
        )

    def files(self):
        return sorted(path.name for path in self.mirror.glob("*.ics"))

    def test_dry_run_writes_nothing(self):
        result = self.run_script()

        self.assertIn("would restore orphan@google-tasks.com", result.stdout)
        self.assertEqual(self.files(), ["present.ics"])

    def test_apply_restores_only_derived_uids_without_a_file(self):
        self.run_script("--apply")

        self.assertEqual(self.files(), ["present.ics", "restored-orphan.ics"])
        self.assertEqual(
            (self.mirror / "restored-orphan.ics").read_bytes(),
            (self.bases / "a").read_bytes(),
        )

    def test_second_apply_is_a_no_op(self):
        self.run_script("--apply")

        result = self.run_script("--apply")

        self.assertEqual(result.stdout, "")
        self.assertEqual(self.files(), ["present.ics", "restored-orphan.ics"])


if __name__ == "__main__":
    unittest.main()
