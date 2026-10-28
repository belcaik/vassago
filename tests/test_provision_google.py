"""Scope handling of caldir/provision-google.py and the read-only diagnosis.

Every Google call is replaced by a fake `request_json`; no network and no real
credential is ever involved. The session files are synthetic.
"""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import tomllib
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _load import load_script, write

ACCOUNT = "student@example.com"

ALL_SCOPES = [
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.calendars",
    "https://www.googleapis.com/auth/tasks",
]


def session_toml(scopes, expires_at):
    quoted = ", ".join(f'"{scope}"' for scope in scopes)
    return (
        'access_token = "old-access"\n'
        'refresh_token = "refresh-1"\n'
        f'expires_at = "{expires_at}"\n'
        'auth_mode = "local"\n'
        f"granted_scopes = [{quoted}]\n"
    )


class ProvisionGoogleTest(unittest.TestCase):
    def setUp(self):
        # load_script writes into os.environ; keep HOME from leaking.
        self.saved_env = dict(os.environ)
        self.addCleanup(os.environ.update, self.saved_env)
        self.tmp = tempfile.TemporaryDirectory()
        home = Path(self.tmp.name)
        google = home / ".config" / "caldir" / "providers" / "google"
        self.session = google / "session" / f"{ACCOUNT}.toml"
        write(
            google / "app_config.toml",
            'client_id = "cid"\nclient_secret = "csecret"\n',
        )
        self.mod = load_script(
            "provision-google.py",
            {
                "HOME": str(home),
                "GOOGLE_ACCOUNT": ACCOUNT,
                "CALENDAR_ROOT": str(home / "calendars"),
            },
        )
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def write_session(self, scopes, expired):
        delta = timedelta(hours=-1 if expired else 1)
        expiry = (datetime.now(timezone.utc) + delta).isoformat()
        self.session.parent.mkdir(parents=True, exist_ok=True)
        self.session.write_text(
            session_toml(scopes, expiry.replace("+00:00", "Z"))
        )

    def fake_refresh(self, response):
        def request_json(url, **kwargs):
            self.calls.append((kwargs.get("method", "GET"), url))
            return dict(response)

        self.mod.request_json = request_json

    def run_quiet(self, func):
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            try:
                return func(), err.getvalue()
            except SystemExit as exc:
                return exc, err.getvalue()

    def test_session_missing_calendars_scope_fails_closed(self):
        self.write_session(ALL_SCOPES[:2] + ALL_SCOPES[3:], expired=False)

        result, err = self.run_quiet(self.mod.get_access_token)

        self.assertIsInstance(result, SystemExit)
        self.assertIn("calendar.calendars", err)

    def test_refresh_without_scope_keeps_recorded_scopes(self):
        self.write_session(ALL_SCOPES, expired=True)
        self.fake_refresh({"access_token": "new-access", "expires_in": 3600})

        (token, _), _ = self.run_quiet(self.mod.get_access_token)

        self.assertEqual(token, "new-access")
        saved = tomllib.loads(self.session.read_text())
        self.assertEqual(saved["granted_scopes"], ALL_SCOPES)
        self.assertEqual(saved["refresh_token"], "refresh-1")
        self.assertEqual(self.session.stat().st_mode & 0o777, 0o600)

    def test_refresh_reporting_all_scopes_passes(self):
        self.write_session(ALL_SCOPES, expired=True)
        self.fake_refresh({
            "access_token": "new-access",
            "expires_in": 3600,
            "scope": " ".join(reversed(ALL_SCOPES)),
        })

        (token, _), _ = self.run_quiet(self.mod.get_access_token)

        self.assertEqual(token, "new-access")

    def test_refresh_reporting_a_narrower_grant_fails_and_writes_nothing(self):
        self.write_session(ALL_SCOPES, expired=True)
        before = self.session.read_text()
        self.fake_refresh({
            "access_token": "new-access",
            "expires_in": 3600,
            "scope": " ".join(ALL_SCOPES[:3]),
        })

        result, err = self.run_quiet(self.mod.get_access_token)

        self.assertIsInstance(result, SystemExit)
        self.assertIn("auth/tasks", err)
        self.assertEqual(self.session.read_text(), before)
        self.assertNotIn("new-access", err)

    def test_hidden_or_unselected_calendar_is_reported(self):
        _, err = self.run_quiet(
            lambda: self.mod.warn_if_not_visible({"hidden": True})
        )

        self.assertIn("is hidden", err)
        self.assertIn("is not selected", err)

        _, err = self.run_quiet(
            lambda: self.mod.warn_if_not_visible({"selected": True})
        )
        self.assertEqual(err, "")


class DiagnoseGoogleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write(
            self.root / "unab-windows" / ".caldir" / "config.toml",
            '[remote]\ngoogle_calendar_id = "cal@group"\n',
        )
        write(
            self.root / "unab" / ".caldir" / "config.toml",
            '[remote]\ngoogle_resource_id = "list1"\n',
        )
        write(
            self.root / "unab-windows" / "a.ics",
            "BEGIN:VEVENT\nUID:w1\nX-GOOGLE-EVENT-ID:ev1\nEND:VEVENT\n",
        )
        write(
            self.root / "unab-windows" / "b.ics",
            "BEGIN:VEVENT\nUID:w2\nX-GOOGLE-EVENT-ID:ev2\nEND:VEVENT\n",
        )
        write(
            self.root / "unab" / "t.ics",
            "BEGIN:VTODO\nUID:t1\nX-GOOGLE-TASK-ID:task1\nEND:VTODO\n",
        )
        self.mod = load_script(
            "diagnose-google.py",
            {"CALENDAR_ROOT": str(self.root),
             "GOOGLE_TASKS_DIR": str(self.root / "unab"),
             "GOOGLE_WINDOWS_DIR": str(self.root / "unab-windows")},
        )
        self.methods = []

    def tearDown(self):
        self.tmp.cleanup()

    def test_reports_cancelled_events_and_uses_only_get(self):
        responses = {
            "calendarList": {"summary": "UNAB", "accessRole": "owner",
                             "selected": False},
            "/events": {"items": [
                {"id": "ev1", "status": "cancelled"},
                {"id": "ev2", "status": "confirmed",
                 "start": {"dateTime": "2026-11-02T10:00:00-03:00"}},
            ]},
            "/tasks": {"items": [{"id": "task1", "status": "completed"}]},
        }

        class Fake:
            @staticmethod
            def request_json(url, **kwargs):
                self.methods.append(kwargs.get("method", "GET"))
                for key, value in responses.items():
                    if key in url:
                        return value
                raise AssertionError(url)

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.mod.diagnose_windows(Fake, "token")
            self.mod.diagnose_tasks(Fake, "token")

        text = out.getvalue()
        self.assertEqual(set(self.methods), {"GET"})
        self.assertIn("selected=False", text)
        self.assertIn("{'cancelled': 1, 'active': 1}", text)
        self.assertIn("cancelled: w1", text)
        self.assertIn("{'completed': 1}", text)
        self.assertNotIn("token", text)


if __name__ == "__main__":
    unittest.main()
