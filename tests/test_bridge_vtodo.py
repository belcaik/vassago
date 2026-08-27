"""Pin the current behaviour of caldir/bridge-vtodo.py."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _load import load_script, write

UID = "u_crawler-todo-1@u-crawler.local"


def vtodo(uid=UID, summary="Assignment 1", due="DUE:20260830T235900Z",
          extra=""):
    body = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//u_crawler//EN",
        "BEGIN:VTODO",
        f"UID:{uid}",
        "DTSTAMP:20260101T000000Z",
        f"SUMMARY:{summary}",
    ]
    if due:
        body.append(due)
    if extra:
        body.extend(extra.strip("\n").split("\n"))
    body += ["END:VTODO", "END:VCALENDAR", ""]
    return "\n".join(body)


class HelperTest(unittest.TestCase):
    """Pure helpers: no calendar tree needed, but ROOT must still be safe."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "unab").mkdir()
        cls.mod = load_script(
            "bridge-vtodo.py",
            {
                "CALENDAR_ROOT": str(root),
                "GOOGLE_TASKS_DIR": str(root / "unab"),
                "BRIDGE_STATE_FILE": str(root / "state.json"),
            },
        )
        cls.root = root

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def item(self, text, name="item.ics"):
        path = write(self.root / "fixtures" / name, text)
        return self.mod.parse_vtodo(path)

    def test_normalize_due_date_strips_time(self):
        self.assertEqual(
            self.mod.normalize_due_date("DUE:20260830T235900Z"),
            "20260830",
        )
        self.assertEqual(
            self.mod.normalize_due_date("DUE;VALUE=DATE:20260830"),
            "20260830",
        )

    def test_normalize_due_date_returns_none_for_unparsable(self):
        self.assertIsNone(self.mod.normalize_due_date("DUE:not-a-date"))

    def test_google_due_from_canonical_is_date_only(self):
        self.assertEqual(
            self.mod.google_due_from_canonical("DUE:20260830T235900Z"),
            "DUE;VALUE=DATE:20260830",
        )

    def test_google_due_from_canonical_passes_unparsable_through(self):
        self.assertEqual(
            self.mod.google_due_from_canonical("DUE:garbage"),
            "DUE:garbage",
        )

    def test_canonical_due_from_google_replaces_date_only_value(self):
        self.assertEqual(
            self.mod.canonical_due_from_google(
                "DUE;VALUE=DATE:20260901",
                "DUE;VALUE=DATE:20260830",
            ),
            "DUE;VALUE=DATE:20260901",
        )

    def test_canonical_due_from_google_keeps_canonical_time_of_day(self):
        self.assertEqual(
            self.mod.canonical_due_from_google(
                "DUE;VALUE=DATE:20260901",
                "DUE:20260830T235900Z",
            ),
            "DUE:20260901T235900Z",
        )

    def test_canonical_due_from_google_without_existing_is_date_only(self):
        self.assertEqual(
            self.mod.canonical_due_from_google(
                "DUE;VALUE=DATE:20260901",
                None,
            ),
            "DUE;VALUE=DATE:20260901",
        )

    def test_shared_signature_equal_for_date_and_datetime_same_day(self):
        date_only = self.item(
            vtodo(due="DUE;VALUE=DATE:20260830"), "a.ics"
        )
        datetime_due = self.item(
            vtodo(due="DUE:20260830T235900Z"), "b.ics"
        )

        self.assertEqual(
            self.mod.shared_signature(date_only),
            self.mod.shared_signature(datetime_due),
        )

    def test_shared_signature_differs_on_different_day(self):
        one = self.item(vtodo(due="DUE:20260830T235900Z"), "c.ics")
        two = self.item(vtodo(due="DUE:20260901T235900Z"), "d.ics")

        self.assertNotEqual(
            self.mod.shared_signature(one),
            self.mod.shared_signature(two),
        )

    def test_semantic_hash_ignores_volatile_and_google_private_props(self):
        plain = self.item(vtodo(), "e.ics")
        noisy = self.item(
            vtodo(
                extra=(
                    "LAST-MODIFIED:20260405T101010Z\n"
                    "SEQUENCE:7\n"
                    "X-GOOGLE-ID:abc123"
                )
            ),
            "f.ics",
        )
        # DTSTAMP is part of the base fixture; change it too.
        noisy_stamp = self.item(
            vtodo().replace("DTSTAMP:20260101", "DTSTAMP:20270101"),
            "g.ics",
        )

        self.assertEqual(
            self.mod.semantic_hash(plain),
            self.mod.semantic_hash(noisy),
        )
        self.assertEqual(
            self.mod.semantic_hash(plain),
            self.mod.semantic_hash(noisy_stamp),
        )

    def test_semantic_hash_reacts_to_summary(self):
        plain = self.item(vtodo(), "h.ics")
        renamed = self.item(vtodo(summary="Other"), "i.ics")

        self.assertNotEqual(
            self.mod.semantic_hash(plain),
            self.mod.semantic_hash(renamed),
        )


class ReconcileTest(unittest.TestCase):
    """Each test gets a fresh calendar tree and a fresh module instance."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.canonical_dir = self.root / "202615_course__deadlines"
        self.google_dir = self.root / "unab"
        self.canonical_dir.mkdir(parents=True)
        self.google_dir.mkdir(parents=True)
        self.state_file = self.root / "state" / "vtodo-bridge-state.json"

        self.mod = load_script(
            "bridge-vtodo.py",
            {
                "CALENDAR_ROOT": str(self.root),
                "GOOGLE_TASKS_DIR": str(self.google_dir),
                "BRIDGE_STATE_FILE": str(self.state_file),
            },
        )

    def tearDown(self):
        self.tmp.cleanup()

    def canonical(self, text=None, name="assignment-1.ics"):
        return write(self.canonical_dir / name, text or vtodo())

    def google(self, name="assignment-1.ics"):
        return self.google_dir / name

    def seed(self):
        """Run one reconcile that seeds Google, and return the fresh state."""
        state = {"version": 2, "items": {}}
        self.assertEqual(self.mod.reconcile(state), (1, 0, 0))
        return state

    def edit(self, path, old, new):
        raw = path.read_text(encoding="utf-8")
        self.assertIn(old, raw)
        write(path, raw.replace(old, new))

    def test_first_observation_canonical_only_seeds_google(self):
        self.canonical()
        state = {"version": 2, "items": {}}

        self.assertEqual(self.mod.reconcile(state), (1, 0, 0))

        seeded = self.google().read_text(encoding="utf-8")
        self.assertIn("DUE;VALUE=DATE:20260830", seeded)
        self.assertNotIn("DUE:20260830T235900Z", seeded)
        self.assertIn(UID, state["items"])
        self.assertIsNotNone(state["items"][UID]["canonical_hash"])
        self.assertIsNotNone(state["items"][UID]["google_hash"])

    def test_first_observation_google_only_is_a_conflict(self):
        write(self.google(), vtodo())
        state = {"version": 2, "items": {}}

        self.assertEqual(self.mod.reconcile(state), (0, 0, 1))
        self.assertTrue(self.google().exists())
        self.assertNotIn(UID, state["items"])

    def test_unmanaged_google_task_is_untouched(self):
        personal = write(
            self.google_dir / "personal.ics",
            vtodo(uid="personal@google-tasks.com"),
        )
        before = personal.read_bytes()
        state = {"version": 2, "items": {}}

        self.assertEqual(self.mod.reconcile(state), (0, 0, 0))
        self.assertEqual(before, personal.read_bytes())

    def test_canonical_deleted_and_google_unchanged_deletes_google(self):
        path = self.canonical()
        state = self.seed()
        path.unlink()

        self.assertEqual(self.mod.reconcile(state), (0, 1, 0))
        self.assertFalse(self.google().exists())
        self.assertIsNone(state["items"][UID]["canonical_hash"])

    def test_canonical_deleted_and_google_changed_is_a_conflict(self):
        path = self.canonical()
        state = self.seed()
        self.edit(self.google(), "SUMMARY:Assignment 1", "SUMMARY:Touched")
        path.unlink()

        self.assertEqual(self.mod.reconcile(state), (0, 0, 1))
        self.assertTrue(self.google().exists())

    def test_google_deleted_reseeds_and_leaves_canonical_alone(self):
        path = self.canonical()
        state = self.seed()
        before = path.read_bytes()
        self.google().unlink()

        self.assertEqual(self.mod.reconcile(state), (1, 0, 0))
        self.assertTrue(self.google().exists())
        self.assertEqual(before, path.read_bytes())

    def test_only_google_changed_updates_canonical(self):
        path = self.canonical()
        state = self.seed()
        self.edit(self.google(), "SUMMARY:Assignment 1", "SUMMARY:From Google")

        self.assertEqual(self.mod.reconcile(state), (1, 0, 0))
        self.assertIn(
            "SUMMARY:From Google",
            path.read_text(encoding="utf-8"),
        )

    def test_both_changed_shared_fields_differently_is_a_conflict(self):
        path = self.canonical()
        state = self.seed()
        self.edit(path, "SUMMARY:Assignment 1", "SUMMARY:From Canvas")
        self.edit(self.google(), "SUMMARY:Assignment 1", "SUMMARY:From Google")

        self.assertEqual(self.mod.reconcile(state), (0, 0, 1))
        self.assertIn("SUMMARY:From Canvas", path.read_text(encoding="utf-8"))
        self.assertIn(
            "SUMMARY:From Google",
            self.google().read_text(encoding="utf-8"),
        )

    def test_only_canonical_changed_projects_to_google(self):
        path = self.canonical()
        state = self.seed()
        self.edit(path, "SUMMARY:Assignment 1", "SUMMARY:From Canvas")

        self.assertEqual(self.mod.reconcile(state), (1, 0, 0))
        self.assertIn(
            "SUMMARY:From Canvas",
            self.google().read_text(encoding="utf-8"),
        )

    def test_duplicate_managed_uid_in_canonical_raises(self):
        self.canonical()
        self.canonical(name="assignment-1-copy.ics")

        with self.assertRaises(RuntimeError):
            self.mod.reconcile({"version": 2, "items": {}})

    def test_duplicate_managed_uid_in_google_raises(self):
        write(self.google("a.ics"), vtodo())
        write(self.google("b.ics"), vtodo())

        with self.assertRaises(RuntimeError):
            self.mod.reconcile({"version": 2, "items": {}})

    def test_missing_google_directory_raises(self):
        self.canonical()
        self.google_dir.rmdir()

        with self.assertRaises(RuntimeError):
            self.mod.reconcile({"version": 2, "items": {}})

    def test_main_returns_2_when_conflicts_exist(self):
        write(self.google(), vtodo())

        self.assertEqual(self.mod.main(), 2)
        self.assertTrue(self.state_file.exists())

    def test_main_returns_0_without_conflicts(self):
        self.canonical()

        self.assertEqual(self.mod.main(), 0)

    def test_state_of_another_version_is_discarded(self):
        write(self.state_file, '{"version": 1, "items": {"x": {}}}')

        self.assertEqual(self.mod.load_state(), {"version": 2, "items": {}})


if __name__ == "__main__":
    unittest.main()
