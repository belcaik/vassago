"""Pin how caldir/entrypoint.sh hands the container environment to cron.

cron starts jobs with an empty environment. entrypoint.sh snapshots an
allowlist of variables into a 0600 file and caldir/cron-run.sh restores it
before exec'ing run-sync.sh. These tests run both scripts against a temporary
directory through their test-only overrides (CALDIR_CRON_ENV_FILE,
CALDIR_CRON_D_FILE, CALDIR_CRON_LOCK_FILE, CALDIR_RUN_SYNC, CALDIR_SKIP_EXEC).
"""

from __future__ import annotations

import fcntl
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from _load import CALDIR_DIR

ENTRYPOINT = CALDIR_DIR / "entrypoint.sh"
CRON_RUN = CALDIR_DIR / "cron-run.sh"

HOST_PATH = "/usr/local/bin:/usr/bin:/bin"

PASSWORD = "p4ss w'o\"rd $HOME `id` \\ $(echo x) end"
ACCOUNT = "line one\nline two with 'quotes'"


class CronEnvCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        self.env_file = self.tmp / "run" / "caldir" / "cron.env"
        self.cron_d = self.tmp / "cron.d-caldir"
        self.lock_file = self.tmp / "sync.lock"
        self.marker = self.tmp / "run-sync.out"

        self.fake_run_sync = self.tmp / "fake-run-sync.sh"
        self.fake_run_sync.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\0' \"$RADICALE_USER\" \"$TZ\" > '{self.marker}'\n",
            encoding="utf-8",
        )
        self.fake_run_sync.chmod(0o700)

    def container_env(self, **extra):
        env = {
            "PATH": HOST_PATH,
            "HOME": "/data",
            "TZ": "America/Santiago",
            "CRON_SCHEDULE": "*/15 * * * *",
            "RUN_ON_START": "false",
            "RADICALE_URL": "http://radicale:5232",
            "RADICALE_USER": "calendar-user",
            "RADICALE_PASSWORD": PASSWORD,
            "GOOGLE_ACCOUNT": ACCOUNT,
            "SECRET_UNRELATED": "must-not-leak",
            "CALDIR_CRON_ENV_FILE": str(self.env_file),
            "CALDIR_CRON_D_FILE": str(self.cron_d),
            "CALDIR_SKIP_EXEC": "1",
        }
        env.update(extra)
        return env

    def run_entrypoint(self, **extra):
        return subprocess.run(
            ["sh", str(ENTRYPOINT)],
            env=self.container_env(**extra),
            capture_output=True,
            text=True,
            check=True,
        )

    def source_snapshot(self, *names):
        script = '. "$1"; shift; for n in "$@"; do eval "printf \'%s\\0\' \\"\\${$n-<unset>}\\""; done'
        result = subprocess.run(
            ["env", "-i", "sh", "-c", script, "sh", str(self.env_file), *names],
            capture_output=True,
            text=True,
            check=True,
        )
        values = result.stdout.split("\0")[:-1]
        return dict(zip(names, values))

    def run_cron_run(self):
        return subprocess.run(
            [
                "env", "-i",
                f"PATH={HOST_PATH}",
                f"CALDIR_CRON_ENV_FILE={self.env_file}",
                f"CALDIR_CRON_LOCK_FILE={self.lock_file}",
                f"CALDIR_RUN_SYNC={self.fake_run_sync}",
                "sh", str(CRON_RUN),
            ],
            capture_output=True,
            text=True,
        )


class EntrypointSnapshotTest(CronEnvCase):
    def test_snapshot_is_private(self):
        self.run_entrypoint()

        self.assertEqual(stat.S_IMODE(self.env_file.stat().st_mode), 0o600)
        self.assertEqual(
            stat.S_IMODE(self.env_file.parent.stat().st_mode), 0o700
        )
        leftovers = [
            p.name for p in self.env_file.parent.iterdir()
            if p.name != self.env_file.name
        ]
        self.assertEqual(leftovers, [], "temporary snapshot left behind")

    def test_snapshot_restores_pipeline_variables_in_an_empty_env(self):
        self.run_entrypoint()

        values = self.source_snapshot(
            "RADICALE_USER", "RADICALE_PASSWORD", "TZ", "HOME", "RADICALE_URL"
        )

        self.assertEqual(values["RADICALE_USER"], "calendar-user")
        self.assertEqual(values["RADICALE_PASSWORD"], PASSWORD)
        self.assertEqual(values["TZ"], "America/Santiago")
        self.assertEqual(values["HOME"], "/data")
        self.assertEqual(values["RADICALE_URL"], "http://radicale:5232")

    def test_values_with_quotes_dollars_and_newlines_round_trip(self):
        self.run_entrypoint()

        values = self.source_snapshot("RADICALE_PASSWORD", "GOOGLE_ACCOUNT")

        self.assertEqual(values["RADICALE_PASSWORD"], PASSWORD)
        self.assertEqual(values["GOOGLE_ACCOUNT"], ACCOUNT)

    def test_variables_outside_the_allowlist_are_not_snapshotted(self):
        self.run_entrypoint()

        values = self.source_snapshot(
            "SECRET_UNRELATED", "CALDIR_CRON_ENV_FILE", "CALDIR_SKIP_EXEC"
        )

        for name, value in values.items():
            with self.subTest(name=name):
                self.assertEqual(value, "<unset>")
        self.assertNotIn(
            "must-not-leak", self.env_file.read_text(encoding="utf-8")
        )

    def test_entrypoint_output_never_contains_the_password(self):
        result = self.run_entrypoint()

        self.assertNotIn("p4ss", result.stdout)
        self.assertNotIn("p4ss", result.stderr)

    def test_cron_line_invokes_cron_run(self):
        self.run_entrypoint()

        lines = self.cron_d.read_text(encoding="utf-8").splitlines()

        self.assertIn(
            "*/15 * * * * root /usr/local/bin/cron-run.sh"
            " >>/proc/1/fd/1 2>>/proc/1/fd/2",
            lines,
        )
        self.assertIn("TZ=America/Santiago", lines)
        self.assertNotIn("run-sync.sh", self.cron_d.read_text(encoding="utf-8"))

    def test_run_on_start_goes_through_cron_run(self):
        text = ENTRYPOINT.read_text(encoding="utf-8")

        self.assertIn('"$CRON_RUN" || true', text)
        self.assertNotIn("run-sync.sh", text)


class CronRunTest(CronEnvCase):
    def test_runs_pipeline_with_restored_environment(self):
        self.run_entrypoint()

        result = self.run_cron_run()

        self.assertEqual(result.returncode, 0, result.stderr)
        user, tz = self.marker.read_text(encoding="utf-8").split("\0")[:2]
        self.assertEqual(user, "calendar-user")
        self.assertEqual(tz, "America/Santiago")

    def test_refuses_a_world_readable_snapshot(self):
        self.run_entrypoint()
        self.env_file.chmod(0o644)

        result = self.run_cron_run()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mode 644", result.stderr)
        self.assertFalse(self.marker.exists())

    def test_refuses_a_missing_snapshot(self):
        result = self.run_cron_run()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing", result.stderr)
        self.assertFalse(self.marker.exists())

    @unittest.skipUnless(shutil.which("flock"), "flock(1) not installed")
    def test_skips_while_another_run_holds_the_lock(self):
        self.run_entrypoint()

        with open(self.lock_file, "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_cron_run()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("skipping", result.stderr)
        self.assertFalse(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
