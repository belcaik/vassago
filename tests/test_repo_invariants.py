"""Structural invariants of the repository itself.

These duplicate checks 4 and 5 of `scripts/check.sh` on purpose, so they also
run under a plain `python3 -m unittest discover -s tests`.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from _load import REPO_ROOT

TERM_PREFIX_FILES = [
    "caldir/provision-radicale.sh",
    "caldir/merge-ucrawler.py",
    "caldir/dedupe-vtodo.py",
    "caldir/bridge-vtodo.py",
    "caldir/bridge-windows.py",
]

TERM_PREFIX_RE = re.compile(r"\b(\d{6}_)")


def read(relative):
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


class TermPrefixLockstepTest(unittest.TestCase):
    def prefixes(self):
        found = {}
        for relative in TERM_PREFIX_FILES:
            matches = set(TERM_PREFIX_RE.findall(read(relative)))
            found[relative] = matches
        return found

    def test_every_script_declares_a_term_prefix(self):
        for relative, matches in self.prefixes().items():
            with self.subTest(file=relative):
                self.assertTrue(
                    matches,
                    f"{relative} has no NNNNNN_ term prefix",
                )

    def test_all_scripts_use_the_same_term_prefix(self):
        union = set()
        for matches in self.prefixes().values():
            union |= matches

        self.assertEqual(
            len(union),
            1,
            f"term prefixes are out of lockstep: {sorted(union)}",
        )


class DockerfileCoverageTest(unittest.TestCase):
    def invoked_scripts(self):
        return set(
            re.findall(
                r"/usr/local/bin/([A-Za-z0-9_.-]+)",
                read("caldir/run-sync.sh"),
            )
        )

    def copied_scripts(self):
        copied = set()
        for line in read("caldir/Dockerfile").splitlines():
            match = re.match(r"^COPY\s+(?!--)(\S+)\s+(\S+)", line.strip())
            if match:
                copied.add(match.group(1))
        return copied

    def test_every_invoked_script_is_copied_into_the_image(self):
        missing = self.invoked_scripts() - self.copied_scripts()

        self.assertEqual(
            missing,
            set(),
            f"run-sync.sh invokes scripts absent from the Dockerfile: {missing}",
        )

    def test_every_copied_script_is_a_tracked_file(self):
        for name in sorted(self.copied_scripts()):
            with self.subTest(script=name):
                self.assertTrue(
                    (REPO_ROOT / "caldir" / name).is_file(),
                    f"Dockerfile COPYs {name}, which does not exist",
                )

    def test_dedupe_stays_out_of_the_image(self):
        self.assertNotIn("dedupe-vtodo.py", self.copied_scripts())
        self.assertNotIn("dedupe-vtodo.py", self.invoked_scripts())


class GitignoreTest(unittest.TestCase):
    REQUIRED = [
        ".env",
        ".env.*",
        "!.env.example",
        "radicale/data/",
        "radicale/config/users",
        "caldir/config/",
        "caldir/state/",
        "caldir/calendars/",
        "u-crawler/config/",
        "**/quarantine-*/",
        "*.bak",
        "*.tar",
        "*.tar.gz",
    ]

    def test_runtime_and_secret_paths_are_ignored(self):
        entries = {
            line.strip()
            for line in read(".gitignore").splitlines()
            if line.strip() and not line.startswith("#")
        }

        for pattern in self.REQUIRED:
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, entries)


class ComposeTest(unittest.TestCase):
    """Text-level assertions: PyYAML is not available and must not be added."""

    def caldir_block(self):
        text = read("compose.yaml")
        start = text.index("\n  caldir:")
        end = text.index("\n  u-crawler:", start)
        return text[start:end]

    def test_caldir_image_is_the_locally_built_tag(self):
        images = re.findall(
            r"^\s*image:\s*(\S+)\s*$",
            self.caldir_block(),
            re.MULTILINE,
        )

        self.assertEqual(images, ["localhost/calendar-caldir:vtodo-support"])

    def test_caldir_has_no_build_key(self):
        for line in self.caldir_block().splitlines():
            self.assertNotRegex(
                line,
                r"^\s*build:",
                "caldir must be built manually, not by compose",
            )


if __name__ == "__main__":
    unittest.main()
