"""Pin the current behaviour of caldir/bridge-windows.py."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from _load import load_script, write

UID = "u_crawler-window-1@u-crawler.local"


def vevent(uid=UID, summary="Delivery window 1", extra=""):
    body = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//u_crawler//EN",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        "DTSTAMP:20260101T000000Z",
        f"SUMMARY:{summary}",
        "DTSTART:20260830T120000Z",
        "DTEND:20260830T235900Z",
    ]
    if extra:
        body.extend(extra.strip("\n").split("\n"))
    body += ["END:VEVENT", "END:VCALENDAR", ""]
    return "\n".join(body)


class MergeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "unab-windows").mkdir()
        cls.root = root
        cls.mod = load_script(
            "bridge-windows.py",
            {
                "CALENDAR_ROOT": str(root),
                "GOOGLE_WINDOWS_DIR": str(root / "unab-windows"),
                "WINDOWS_BRIDGE_STATE_FILE": str(root / "state.json"),
            },
        )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def item(self, text, name):
        return self.mod.parse_vevent(
            write(self.root / "fixtures" / name, text)
        )

    def test_merge_replaces_body_and_retains_google_private_props(self):
        google = self.item(
            vevent(
                summary="Edited in Google",
                extra="X-GOOGLE-EVENT-ID:gid-123\nX-GOOGLE-HANGOUT:none",
            ),
            "google.ics",
        )
        canonical = self.item(
            vevent(summary="From Canvas", extra="X-GOOGLE-EVENT-ID:stale"),
            "canonical.ics",
        )

        merged = self.mod.merge_canonical_into_google(google, canonical)

        self.assertIn("SUMMARY:From Canvas", merged)
        self.assertNotIn("Edited in Google", merged)
        self.assertIn("X-GOOGLE-EVENT-ID:gid-123", merged)
        self.assertIn("X-GOOGLE-HANGOUT:none", merged)
        self.assertNotIn("X-GOOGLE-EVENT-ID:stale", merged)
        self.assertTrue(merged.endswith("END:VCALENDAR\r\n"))

    def test_semantic_hash_ignores_google_private_props(self):
        plain = self.item(vevent(), "plain.ics")
        tagged = self.item(
            vevent(extra="X-GOOGLE-EVENT-ID:gid-123\nSEQUENCE:4"),
            "tagged.ics",
        )

        self.assertEqual(
            self.mod.semantic_hash(plain),
            self.mod.semantic_hash(tagged),
        )

    def test_google_equivalent_accepts_summer_tzid_and_utc_same_instant(self):
        canonical = self.item(
            vevent().replace("20260830", "20261230"),
            "canonical-time.ics",
        )
        google = self.item(
            vevent().replace("20260830", "20261230").replace(
                "DTSTART:20261230T120000Z",
                "DTSTART;TZID=America/Santiago:20261230T090000",
            ).replace(
                "DTEND:20261230T235900Z",
                "DTEND;TZID=America/Santiago:20261230T205900",
            ),
            "google-time.ics",
        )

        self.assertTrue(self.mod.google_equivalent(canonical, google))

    def test_google_equivalent_accepts_both_folds_of_ambiguous_time(self):
        google = self.item(
            vevent().replace(
                "DTEND:20260830T235900Z",
                "DTEND;TZID=America/Santiago:20270403T235900",
            ),
            "google-fold.ics",
        )

        for instant in ("20270404T025900Z", "20270404T035900Z"):
            with self.subTest(instant=instant):
                canonical = self.item(
                    vevent().replace("20260830T235900Z", instant),
                    f"canonical-{instant}.ics",
                )
                self.assertTrue(
                    self.mod.google_equivalent(canonical, google)
                )

    def test_google_equivalent_rejects_utc_value_with_tzid(self):
        canonical = self.item(vevent(), "canonical-z-tzid.ics")
        google = self.item(
            vevent().replace(
                "DTSTART:20260830T120000Z",
                "DTSTART;TZID=America/Santiago:20260830T120000Z",
            ),
            "google-z-tzid.ics",
        )

        self.assertFalse(self.mod.google_equivalent(canonical, google))

    def test_google_equivalent_rejects_different_instant(self):
        canonical = self.item(vevent(), "canonical-different.ics")
        google = self.item(
            vevent().replace(
                "DTSTART:20260830T120000Z",
                "DTSTART;TZID=America/Santiago:20260830T090000",
            ),
            "google-different.ics",
        )

        self.assertFalse(self.mod.google_equivalent(canonical, google))

    def test_google_equivalent_accepts_missing_url(self):
        canonical = self.item(
            vevent(extra="URL:https://example.edu/window"),
            "canonical-url.ics",
        )
        google = self.item(vevent(), "google-url.ics")

        self.assertTrue(self.mod.google_equivalent(canonical, google))

    def test_google_equivalent_rejects_changed_summary(self):
        canonical = self.item(vevent(), "canonical-summary.ics")
        google = self.item(
            vevent(summary="Edited in Google"),
            "google-summary.ics",
        )

        self.assertFalse(self.mod.google_equivalent(canonical, google))

    def test_google_equivalent_compares_summary_with_t_and_commas_as_text(self):
        canonical = self.item(
            vevent(summary="Take T, then submit"),
            "canonical-text-summary.ics",
        )
        same = self.item(
            vevent(summary="Take T, then submit"),
            "google-text-summary-same.ics",
        )
        changed = self.item(
            vevent(summary="Take T, then wait"),
            "google-text-summary-changed.ics",
        )

        self.assertTrue(self.mod.google_equivalent(canonical, same))
        self.assertFalse(self.mod.google_equivalent(canonical, changed))

    def test_google_equivalent_compares_floating_time_as_is(self):
        canonical = self.item(
            vevent().replace(
                "DTSTART:20260830T120000Z",
                "DTSTART:20260830T120000",
            ),
            "canonical-floating.ics",
        )
        google = self.item(
            vevent().replace(
                "DTSTART:20260830T120000Z",
                "DTSTART;TZID=America/Santiago:20260830T080000",
            ),
            "google-floating.ics",
        )

        self.assertFalse(self.mod.google_equivalent(canonical, google))

    def test_google_equivalent_compares_all_day_date_as_is(self):
        canonical = self.item(
            vevent().replace(
                "DTSTART:20260830T120000Z",
                "DTSTART;VALUE=DATE:20260830",
            ),
            "canonical-date.ics",
        )
        google = self.item(
            vevent().replace(
                "DTSTART:20260830T120000Z",
                "DTSTART;VALUE=DATE:20260831",
            ),
            "google-date.ics",
        )

        self.assertFalse(self.mod.google_equivalent(canonical, google))

    def test_google_equivalent_rejects_unknown_timezone(self):
        canonical = self.item(vevent(), "canonical-zone.ics")
        google = self.item(
            vevent().replace(
                "DTSTART:20260830T120000Z",
                "DTSTART;TZID=Invalid/Zone:20260830T080000",
            ),
            "google-zone.ics",
        )

        self.assertFalse(self.mod.google_equivalent(canonical, google))


class ReconcileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.canonical_dir = self.root / "202615_course__windows"
        self.google_dir = self.root / "unab-windows"
        self.canonical_dir.mkdir(parents=True)
        self.google_dir.mkdir(parents=True)
        self.state_file = self.root / "state" / "windows-bridge-state.json"

        self.mod = load_script(
            "bridge-windows.py",
            {
                "CALENDAR_ROOT": str(self.root),
                "GOOGLE_WINDOWS_DIR": str(self.google_dir),
                "WINDOWS_BRIDGE_STATE_FILE": str(self.state_file),
            },
        )

    def tearDown(self):
        self.tmp.cleanup()

    def canonical(self, text=None, name="assignment-1.ics"):
        return write(self.canonical_dir / name, text or vevent())

    def google(self, name="assignment-1.ics"):
        return self.google_dir / name

    def seed(self):
        state = {"version": 1, "items": {}}
        self.assertEqual(self.mod.reconcile(state), (1, 0, 0))
        return state

    def edit(self, path, old, new):
        raw = path.read_text(encoding="utf-8")
        self.assertIn(old, raw)
        write(path, raw.replace(old, new))

    def test_first_observation_canonical_only_seeds_google(self):
        self.canonical()
        state = {"version": 1, "items": {}}

        self.assertEqual(self.mod.reconcile(state), (1, 0, 0))
        self.assertIn(
            "SUMMARY:Delivery window 1",
            self.google().read_text(encoding="utf-8"),
        )
        self.assertIn(UID, state["items"])

    def test_first_observation_google_only_is_a_conflict(self):
        write(self.google(), vevent())

        self.assertEqual(
            self.mod.reconcile({"version": 1, "items": {}}),
            (0, 0, 1),
        )
        self.assertTrue(self.google().exists())

    def test_google_edit_is_overwritten_by_canonical(self):
        self.canonical()
        state = self.seed()
        self.edit(
            self.google(),
            "SUMMARY:Delivery window 1",
            "SUMMARY:Edited in Google",
        )

        self.assertEqual(self.mod.reconcile(state), (1, 0, 0))
        self.assertIn(
            "SUMMARY:Delivery window 1",
            self.google().read_text(encoding="utf-8"),
        )

    def test_google_normalization_is_recorded_without_rewrite(self):
        self.canonical(vevent(extra="URL:https://example.edu/window"))
        state = self.seed()
        path = self.google()
        raw = path.read_text(encoding="utf-8")
        normalized = raw.replace(
            "DTSTART:20260830T120000Z",
            "DTSTART;TZID=America/Santiago:20260830T080000",
        ).replace(
            "DTEND:20260830T235900Z",
            "DTEND;TZID=America/Santiago:20260830T195900",
        ).replace(
            "URL:https://example.edu/window\n",
            "",
        )
        write(path, normalized)
        before = path.read_bytes()

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = self.mod.reconcile(state)

        self.assertEqual(result, (0, 0, 0))
        self.assertIn(
            f"[windows-bridge] google normalized {UID}",
            out.getvalue(),
        )
        self.assertEqual(path.read_bytes(), before)

        self.edit(
            path,
            "DTSTART;TZID=America/Santiago:20260830T080000",
            "DTSTART;TZID=America/Santiago:20260830T090000",
        )

        self.assertEqual(self.mod.reconcile(state), (1, 0, 0))
        self.assertIn(
            "DTSTART:20260830T120000Z",
            path.read_text(encoding="utf-8"),
        )

    def test_first_observation_of_google_normalization_is_unchanged(self):
        self.canonical(vevent(extra="URL:https://example.edu/window"))
        path = write(
            self.google(),
            vevent().replace(
                "DTSTART:20260830T120000Z",
                "DTSTART;TZID=America/Santiago:20260830T080000",
            ).replace(
                "DTEND:20260830T235900Z",
                "DTEND;TZID=America/Santiago:20260830T195900",
            ),
        )
        before = path.read_bytes()
        state = {"version": 1, "items": {}}

        self.assertEqual(self.mod.reconcile(state), (0, 0, 0))
        self.assertEqual(path.read_bytes(), before)
        self.assertIn(UID, state["items"])
        self.assertEqual(self.mod.reconcile(state), (0, 0, 0))
        self.assertEqual(path.read_bytes(), before)

    def test_canonical_change_is_pushed_to_google(self):
        path = self.canonical()
        state = self.seed()
        self.edit(path, "SUMMARY:Delivery window 1", "SUMMARY:Moved window")

        self.assertEqual(self.mod.reconcile(state), (1, 0, 0))
        self.assertIn(
            "SUMMARY:Moved window",
            self.google().read_text(encoding="utf-8"),
        )

    def test_canonical_deleted_and_google_unchanged_deletes_google(self):
        path = self.canonical()
        state = self.seed()
        path.unlink()

        self.assertEqual(self.mod.reconcile(state), (0, 1, 0))
        self.assertFalse(self.google().exists())

    def test_canonical_deleted_and_google_changed_is_a_conflict(self):
        path = self.canonical()
        state = self.seed()
        self.edit(
            self.google(),
            "SUMMARY:Delivery window 1",
            "SUMMARY:Edited in Google",
        )
        path.unlink()

        self.assertEqual(self.mod.reconcile(state), (0, 0, 1))
        self.assertTrue(self.google().exists())

    def test_google_deleted_is_reseeded(self):
        path = self.canonical()
        state = self.seed()
        before = path.read_bytes()
        self.google().unlink()

        self.assertEqual(self.mod.reconcile(state), (1, 0, 0))
        self.assertTrue(self.google().exists())
        self.assertEqual(before, path.read_bytes())

    def test_no_change_on_either_side_is_a_no_op(self):
        self.canonical()
        state = self.seed()

        self.assertEqual(self.mod.reconcile(state), (0, 0, 0))

    def test_unmanaged_google_event_is_untouched(self):
        personal = write(
            self.google_dir / "personal.ics",
            vevent(uid="personal@google.com"),
        )
        before = personal.read_bytes()

        self.assertEqual(
            self.mod.reconcile({"version": 1, "items": {}}),
            (0, 0, 0),
        )
        self.assertEqual(before, personal.read_bytes())

    def test_duplicate_managed_uid_in_canonical_raises(self):
        self.canonical()
        self.canonical(name="assignment-1-copy.ics")

        with self.assertRaises(RuntimeError):
            self.mod.reconcile({"version": 1, "items": {}})

    def test_missing_google_directory_raises(self):
        self.canonical()
        self.google_dir.rmdir()

        with self.assertRaises(RuntimeError):
            self.mod.reconcile({"version": 1, "items": {}})

    def test_main_returns_2_when_conflicts_exist(self):
        write(self.google(), vevent())

        self.assertEqual(self.mod.main(), 2)

    def test_main_returns_0_without_conflicts(self):
        self.canonical()

        self.assertEqual(self.mod.main(), 0)


if __name__ == "__main__":
    unittest.main()
