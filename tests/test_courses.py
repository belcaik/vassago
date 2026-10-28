"""Course discovery (caldir/courses.py) and its use by provision-radicale.sh."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from _load import CALDIR_DIR, load_script, write

ICS = "BEGIN:VCALENDAR\nBEGIN:VTODO\nUID:x\nEND:VTODO\nEND:VCALENDAR\n"


class DiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.mod = load_script("courses.py")

    def tearDown(self):
        self.tmp.cleanup()

    def course(self, name, kind="deadlines", files=1):
        directory = self.root / name / kind
        directory.mkdir(parents=True, exist_ok=True)
        for index in range(files):
            write(directory / f"assignment-{index}.ics", ICS)

    def test_current_previous_and_future_terms_are_found(self):
        for name in ("202615_math", "202625_physics", "202715_future"):
            self.course(name)

        self.assertEqual(
            self.mod.source_courses(self.root, "deadlines"),
            ["202615_math", "202625_physics", "202715_future"],
        )

    def test_name_is_not_matched_against_a_term_code(self):
        self.course("electivo-ingles")

        self.assertEqual(
            self.mod.source_courses(self.root, "deadlines"),
            ["electivo-ingles"],
        )

    def test_new_course_is_picked_up_on_next_call(self):
        self.course("202615_math")
        before = self.mod.source_courses(self.root, "deadlines")
        self.course("202625_new")

        self.assertEqual(before, ["202615_math"])
        self.assertEqual(
            self.mod.source_courses(self.root, "deadlines"),
            ["202615_math", "202625_new"],
        )

    def test_canonical_collections_are_not_courses(self):
        self.course("202615_math")
        # A stray deadlines/ under a canonical dir must not create a course.
        self.course("202615_math__deadlines")
        self.course("202615_math__windows", kind="windows")

        self.assertEqual(
            self.mod.source_courses(self.root, "deadlines"),
            ["202615_math"],
        )
        self.assertEqual(self.mod.source_courses(self.root, "windows"), [])

    def test_empty_or_missing_export_is_not_a_course(self):
        self.course("202615_empty", files=0)
        (self.root / "202615_nokind").mkdir()

        self.assertEqual(self.mod.source_courses(self.root, "deadlines"), [])

    def test_mirrors_and_hidden_dirs_are_ignored(self):
        (self.root / "unab").mkdir()
        (self.root / "unab-windows").mkdir()
        (self.root / ".caldir").mkdir()
        write(self.root / "unab" / "t.ics", ICS)

        self.assertEqual(self.mod.source_courses(self.root, "deadlines"), [])

    def test_unsafe_names_are_ignored(self):
        self.course("bad name")
        self.course("bad'quote")

        self.assertEqual(self.mod.source_courses(self.root, "deadlines"), [])

    def test_canonical_courses_keep_old_terms_without_source(self):
        (self.root / "202515_old__deadlines").mkdir()
        (self.root / "202615_math__deadlines").mkdir()
        (self.root / "202615_math__windows").mkdir()
        self.course("202625_new")

        self.assertEqual(
            self.mod.canonical_courses(self.root, "deadlines"),
            ["202515_old", "202615_math"],
        )
        self.assertEqual(
            self.mod.canonical_dirs(self.root, "windows"),
            [self.root / "202615_math__windows"],
        )

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            self.mod.source_courses(self.root, "events")

    def test_cli_lists_one_course_per_line(self):
        self.course("202615_math")
        self.course("202625_physics")

        result = subprocess.run(
            ["python3", str(CALDIR_DIR / "courses.py"), "sources", "deadlines"],
            env={**os.environ, "CALENDAR_ROOT": str(self.root)},
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertEqual(result.stdout, "202615_math\n202625_physics\n")


class ProvisionRadicaleTest(unittest.TestCase):
    """Runs provision-radicale.sh against a fake curl on PATH."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "calendars"
        self.root.mkdir()
        self.bin = base / "bin"
        self.bin.mkdir()
        self.log = base / "curl.log"

        curl = self.bin / "curl"
        curl.write_text(
            "#!/bin/sh\n"
            f'for arg in "$@"; do last="$arg"; done\n'
            f'echo "$last" >> "{self.log}"\n'
            "printf 201\n"
        )
        curl.chmod(curl.stat().st_mode | stat.S_IEXEC)

    def tearDown(self):
        self.tmp.cleanup()

    def run_provision(self):
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "CALENDAR_ROOT": str(self.root),
            "RADICALE_URL": "http://radicale:5232",
            "RADICALE_USER": "user",
            "RADICALE_PASSWORD": "secret",
        }
        return subprocess.run(
            ["sh", str(CALDIR_DIR / "provision-radicale.sh")],
            env=env,
            capture_output=True,
            text=True,
        )

    def test_provisions_every_term_and_skips_empty_exports(self):
        write(self.root / "202615_math" / "deadlines" / "a.ics", ICS)
        write(self.root / "202625_physics" / "windows" / "a.ics", ICS)
        write(self.root / "202715_future" / "deadlines" / "a.ics", ICS)
        (self.root / "202625_empty" / "deadlines").mkdir(parents=True)
        (self.root / "202515_old__deadlines" / ".caldir").mkdir(parents=True)

        result = self.run_provision()

        self.assertEqual(result.returncode, 0, result.stderr)
        configured = sorted(
            path.parent.parent.name
            for path in self.root.glob("*/.caldir/config.toml")
        )
        self.assertEqual(
            configured,
            [
                "202615_math__deadlines",
                "202625_physics__windows",
                "202715_future__deadlines",
            ],
        )
        self.assertFalse((self.root / "202625_empty__deadlines").exists())
        self.assertNotIn("secret", result.stdout + result.stderr)

    def test_second_run_creates_nothing(self):
        write(self.root / "202625_physics" / "deadlines" / "a.ics", ICS)
        self.run_provision()
        calls = self.log.read_text().count("\n")

        result = self.run_provision()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text().count("\n"), calls)
        self.assertNotIn("creating", result.stdout)

    def test_missing_credentials_fail(self):
        result = subprocess.run(
            ["sh", str(CALDIR_DIR / "provision-radicale.sh")],
            env={"PATH": "/usr/bin:/bin", "CALENDAR_ROOT": str(self.root)},
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("RADICALE_USER required", result.stderr)


if __name__ == "__main__":
    unittest.main()
