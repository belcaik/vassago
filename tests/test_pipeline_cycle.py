"""End-to-end cycles of the Vassago scripts against a simulated caldir.

`FakeCaldir` stands in for `caldir sync` on the two Google mirrors. It models
the three-way rule that matters here: an item present locally, absent
remotely and remembered in the sync bases is a *remote deletion* and is
deleted locally. `forget_on_delete=True` is the fixed caldir (the base is
dropped once a delete is applied); `False` reproduces the append-only bases
behind the 57-restored / 57-deleted incident.

No network, no credentials: every "remote" lives in a dict.
"""

from __future__ import annotations

import contextlib
import io
import itertools
import shutil
import tempfile
import unittest
from pathlib import Path

from _load import load_script, write


def vtodo(number, summary=None, due="20260830T235900Z"):
    return (
        "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//u_crawler//EN\n"
        "BEGIN:VTODO\n"
        f"UID:u_crawler-todo-{number}@u-crawler.local\n"
        "DTSTAMP:20260101T000000Z\n"
        f"SUMMARY:{summary or f'Assignment {number}'}\n"
        f"DUE:{due}\n"
        "END:VTODO\nEND:VCALENDAR\n"
    )


def vevent(number, start="20260825T120000Z", end="20260830T235900Z"):
    return (
        "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//u_crawler//EN\n"
        "BEGIN:VEVENT\n"
        f"UID:u_crawler-window-{number}@u-crawler.local\n"
        "DTSTAMP:20260101T000000Z\n"
        f"SUMMARY:Window {number}\n"
        f"DTSTART:{start}\nDTEND:{end}\n"
        "END:VEVENT\nEND:VCALENDAR\n"
    )


def uid_of(raw):
    for line in raw.replace("\r\n", "\n").split("\n"):
        if line.startswith("UID:"):
            return line[4:]
    raise AssertionError("no UID")


def without_id(raw, id_prop):
    lines = raw.replace("\r\n", "\n").split("\n")
    return "\n".join(line for line in lines if not line.startswith(id_prop))


class FakeCaldir:
    def __init__(self, mirror: Path, id_prop: str, forget_on_delete=True):
        self.mirror = mirror
        self.id_prop = id_prop
        self.forget_on_delete = forget_on_delete
        self.remote: dict[str, dict] = {}  # uid -> {"id", "raw"}
        self.bases: set[str] = set()
        self.pending_remote_edits: set[str] = set()
        self.ids = itertools.count(1)
        self.changes = 0

    # Google-side actions --------------------------------------------------
    def user_edits(self, uid, old, new):
        entry = self.remote[uid]
        entry["raw"] = entry["raw"].replace(old, new)
        self.pending_remote_edits.add(uid)

    def user_deletes(self, uid):
        del self.remote[uid]

    # caldir sync ----------------------------------------------------------
    def local(self):
        return {
            uid_of(path.read_text()): path
            for path in sorted(self.mirror.glob("*.ics"))
        }

    def with_id(self, raw, remote_id):
        body = without_id(raw, self.id_prop).replace("\r\n", "\n")
        marker = "END:VTODO" if "BEGIN:VTODO" in body else "END:VEVENT"
        body = body.replace(marker, f"{self.id_prop}:{remote_id}\n{marker}")
        return body.replace("\n", "\r\n")

    def sync(self):
        local = self.local()

        for uid, path in local.items():
            if uid in self.remote:
                remote = self.remote[uid]
                current = path.read_bytes().decode()
                if uid in self.pending_remote_edits:
                    path.write_bytes(remote["raw"].encode())
                    self.changes += 1
                elif without_id(current, self.id_prop) != without_id(
                    remote["raw"], self.id_prop
                ):
                    remote["raw"] = self.with_id(current, remote["id"])
                    path.write_bytes(remote["raw"].encode())
                    self.changes += 1
            elif uid in self.bases:
                path.unlink()  # "deleted remotely"
                if self.forget_on_delete:
                    self.bases.discard(uid)
                self.changes += 1
            else:
                remote_id = f"id{next(self.ids)}"
                raw = self.with_id(path.read_bytes().decode(), remote_id)
                self.remote[uid] = {"id": remote_id, "raw": raw}
                path.write_bytes(raw.encode())
                self.bases.add(uid)
                self.changes += 1

        for uid in list(self.remote):
            if uid not in local and uid not in self.bases:
                raw = self.remote[uid]["raw"]
                write(self.mirror / f"{uid}.ics", raw.replace("\r\n", "\n"))
                self.bases.add(uid)
                self.changes += 1

        self.pending_remote_edits.clear()


class PipelineCycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "calendars"
        self.tasks = self.root / "unab"
        self.windows = self.root / "unab-windows"
        self.tasks.mkdir(parents=True)
        self.windows.mkdir()

        env = {
            "CALENDAR_ROOT": str(self.root),
            "GOOGLE_TASKS_DIR": str(self.tasks),
            "GOOGLE_WINDOWS_DIR": str(self.windows),
            "BRIDGE_STATE_FILE": str(base / "vtodo-state.json"),
            "WINDOWS_BRIDGE_STATE_FILE": str(base / "windows-state.json"),
        }
        self.vtodo_state = base / "vtodo-state.json"
        self.merge = load_script("merge-ucrawler.py", env)
        self.bridge = load_script("bridge-vtodo.py", env)
        self.windows_bridge = load_script("bridge-windows.py", env)
        self.verify = load_script("verify-sync.py", env)

        self.google_tasks = FakeCaldir(self.tasks, "X-GOOGLE-TASK-ID")
        self.google_cal = FakeCaldir(self.windows, "X-GOOGLE-EVENT-ID")

    def tearDown(self):
        self.tmp.cleanup()

    def export(self, course, number, text=None, kind="deadlines"):
        maker = vtodo if kind == "deadlines" else vevent
        write(
            self.root / course / kind / f"assignment-{number}.ics",
            text or maker(number),
        )

    def caldir_sync(self):
        self.google_tasks.sync()
        self.google_cal.sync()

    def cycle(self, *, stop_before_phase2=False):
        """One run-sync.sh pass; returns (bridge rcs, verify rc, stdout)."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            self.merge.main()
            self.caldir_sync()
            rc_tasks = self.bridge.main()
            rc_windows = self.windows_bridge.main()
            if stop_before_phase2:
                return (rc_tasks, rc_windows), None, out.getvalue()
            self.caldir_sync()
            rc_verify = self.verify.main()
        return (rc_tasks, rc_windows), rc_verify, out.getvalue()

    def snapshot(self):
        files = {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in sorted(self.root.rglob("*.ics"))
        }
        remote = {
            uid: dict(entry) for uid, entry in self.google_tasks.remote.items()
        }
        return files, remote

    def test_two_terms_publish_and_second_cycle_is_a_no_op(self):
        self.export("202615_math", 1)
        self.export("202625_physics", 2)
        self.export("202625_physics", 3, kind="windows")

        rcs, verify, _ = self.cycle()
        self.assertEqual((rcs, verify), ((0, 0), 0))
        self.assertEqual(len(self.google_tasks.remote), 2)
        self.assertEqual(len(self.google_cal.remote), 1)

        before = self.snapshot()
        self.google_tasks.changes = self.google_cal.changes = 0

        rcs, verify, out = self.cycle()

        self.assertEqual((rcs, verify), ((0, 0), 0))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.google_tasks.changes, 0)
        self.assertEqual(self.google_cal.changes, 0)
        self.assertIn("[bridge] done: 0 changed, 0 deleted, 0 conflicts", out)
        self.assertIn("deadlines: 0 created, 0 updated, 0 removed", out)

    def test_new_course_in_a_future_term_is_added_automatically(self):
        self.export("202615_math", 1)
        self.cycle()

        self.export("202715_future", 9)
        rcs, verify, _ = self.cycle()

        self.assertEqual((rcs, verify), ((0, 0), 0))
        self.assertIn(
            "u_crawler-todo-9@u-crawler.local", self.google_tasks.remote
        )
        self.assertTrue((self.root / "202715_future__deadlines").is_dir())

    def test_old_term_survives_when_its_export_disappears(self):
        self.export("202615_math", 1)
        self.export("202625_physics", 2)
        self.cycle()
        remote_id = self.google_tasks.remote[
            "u_crawler-todo-1@u-crawler.local"
        ]["id"]

        shutil.rmtree(self.root / "202615_math")
        rcs, verify, out = self.cycle()

        self.assertEqual((rcs, verify), ((0, 0), 0))
        self.assertNotIn("delete Google", out)
        self.assertTrue(
            (self.root / "202615_math__deadlines" / "assignment-1.ics").exists()
        )
        self.assertEqual(
            self.google_tasks.remote["u_crawler-todo-1@u-crawler.local"]["id"],
            remote_id,
        )

    def test_canvas_edit_keeps_uid_and_remote_id(self):
        self.export("202625_physics", 1)
        self.export("202625_physics", 2)
        self.cycle()
        uid = "u_crawler-todo-1@u-crawler.local"
        remote_id = self.google_tasks.remote[uid]["id"]

        self.export("202625_physics", 1, vtodo(1, summary="Renamed"))
        rcs, verify, _ = self.cycle()

        self.assertEqual((rcs, verify), ((0, 0), 0))
        self.assertEqual(self.google_tasks.remote[uid]["id"], remote_id)
        self.assertIn("SUMMARY:Renamed", self.google_tasks.remote[uid]["raw"])
        self.assertEqual(len(self.google_tasks.remote), 2)

    def test_completed_in_google_does_not_come_back_pending(self):
        self.export("202625_physics", 1)
        self.cycle()
        uid = "u_crawler-todo-1@u-crawler.local"

        self.google_tasks.user_edits(
            uid, "END:VTODO", "STATUS:COMPLETED\r\nEND:VTODO"
        )
        self.cycle()
        # u_crawler re-exports the assignment without any status.
        self.export("202625_physics", 1, vtodo(1))
        rcs, verify, _ = self.cycle()

        self.assertEqual((rcs, verify), ((0, 0), 0))
        canonical = (
            self.root / "202625_physics__deadlines" / "assignment-1.ics"
        ).read_text()
        self.assertIn("STATUS:COMPLETED", canonical)
        self.assertIn("STATUS:COMPLETED", self.google_tasks.remote[uid]["raw"])

        before = self.snapshot()
        self.cycle()
        self.assertEqual(self.snapshot(), before)

    def test_google_deletion_is_restored_and_converges(self):
        self.export("202625_physics", 1)
        self.cycle()
        uid = "u_crawler-todo-1@u-crawler.local"
        old_id = self.google_tasks.remote[uid]["id"]

        self.google_tasks.user_deletes(uid)
        rcs, verify, out = self.cycle()

        self.assertIn("restore Google task", out)
        self.assertEqual((rcs, verify), ((0, 0), 0))
        self.assertIn(uid, self.google_tasks.remote)
        self.assertNotEqual(self.google_tasks.remote[uid]["id"], old_id)

        before = self.snapshot()
        rcs, verify, out = self.cycle()
        self.assertNotIn("restore", out)
        self.assertEqual(self.snapshot(), before)

    def test_append_only_bases_loop_is_reported_not_done(self):
        # The incident: caldir never forgets a base, so the bridge restore is
        # undone in phase 2 on every run. verify-sync must catch it.
        self.google_tasks.forget_on_delete = False
        self.export("202625_physics", 1)
        self.cycle()

        self.google_tasks.user_deletes("u_crawler-todo-1@u-crawler.local")
        for _ in range(2):
            rcs, verify, out = self.cycle()
            self.assertIn("restore Google task", out)
            self.assertEqual(verify, 3)
            self.assertIn("missing from mirror", out)

    def test_interrupted_run_recovers_without_duplicates(self):
        self.export("202615_math", 1)
        self.export("202625_physics", 2)

        # Crash after the bridges, before phase 2 and with the bridge state
        # lost (e.g. container killed before save_state).
        self.cycle(stop_before_phase2=True)
        self.vtodo_state.unlink()

        rcs, verify, _ = self.cycle()

        self.assertEqual((rcs, verify), ((0, 0), 0))
        self.assertEqual(len(self.google_tasks.remote), 2)
        self.assertEqual(len(list(self.tasks.glob("*.ics"))), 2)

    def test_windows_are_published_with_event_ids(self):
        self.export("202625_physics", 1, kind="windows")
        self.export("202715_future", 2, kind="windows")

        rcs, verify, _ = self.cycle()

        self.assertEqual((rcs, verify), ((0, 0), 0))
        self.assertEqual(len(self.google_cal.remote), 2)
        for path in self.windows.glob("*.ics"):
            self.assertIn("X-GOOGLE-EVENT-ID:", path.read_text())

    def test_unpublished_window_fails_verification(self):
        self.export("202625_physics", 1, kind="windows")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            self.merge.main()
            self.windows_bridge.main()
            # phase 2 never pushes (e.g. provider error swallowed upstream)
            rc = self.verify.main()

        self.assertEqual(rc, 3)
        self.assertIn("windows unpublished", out.getvalue())

    def test_items_outside_the_sync_window_are_not_required(self):
        self.export("202015_ancient", 1, vtodo(1, due="20200101T000000Z"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            self.merge.main()
            rc = self.verify.main()

        self.assertEqual(rc, 0)
        self.assertIn("deadlines: 0 expected", out.getvalue())

    def test_duplicate_uid_across_courses_aborts_before_writing(self):
        self.export("202615_math", 1)
        self.export("202625_physics", 1)

        with self.assertRaises(RuntimeError) as ctx:
            with contextlib.redirect_stdout(io.StringIO()):
                self.merge.main()

        self.assertIn("duplicate u_crawler UID", str(ctx.exception))
        self.assertEqual(list(self.root.glob("*__deadlines")), [])

    def test_mass_removal_is_held_back(self):
        for number in range(1, 7):
            self.export("202625_physics", number)
        self.cycle()

        for number in range(2, 7):
            (self.root / "202625_physics" / "deadlines"
             / f"assignment-{number}.ics").unlink()
        rcs, verify, out = self.cycle()

        self.assertIn("WARNING holding 5/6 removals", out)
        self.assertEqual(len(self.google_tasks.remote), 6)
        self.assertEqual((rcs, verify), ((0, 0), 0))


if __name__ == "__main__":
    unittest.main()
