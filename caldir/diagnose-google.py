#!/usr/bin/env python3
"""Read-only diagnosis of what Google really holds for the two mirrors.

Compares the local mirrors (unab/, unab-windows/) with the Google task list
and calendar their .caldir/config.toml point at, using only GET requests:

- calendarList entry of the windows calendar: hidden / selected / accessRole;
- events of that calendar including deleted ones (showDeleted=true), by
  status, with the date range covered;
- tasks of the task list including completed, hidden and deleted ones;
- for every local X-GOOGLE-EVENT-ID / X-GOOGLE-TASK-ID: active, cancelled
  or deleted in Google, or unknown to it.

Never creates, updates or deletes anything in Google. The only write it can
cause is the OAuth access-token refresh that every pipeline run already does
(provision-google.get_access_token). Prints counts and ids, never tokens.

Run inside the caldir container:  diagnose-google.py
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tomllib
import urllib.parse
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("CALENDAR_ROOT", "/data/calendars"))
TASKS_DIR = Path(os.environ.get("GOOGLE_TASKS_DIR", str(ROOT / "unab")))
WINDOWS_DIR = Path(
    os.environ.get("GOOGLE_WINDOWS_DIR", str(ROOT / "unab-windows"))
)


def load_provision():
    path = HERE / "provision-google.py"
    spec = importlib.util.spec_from_file_location("provision_google", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def remote_config(mirror: Path) -> dict:
    path = mirror / ".caldir" / "config.toml"

    if not path.exists():
        return {}

    with path.open("rb") as handle:
        return tomllib.load(handle).get("remote", {})


def local_ids(mirror: Path, prop: str) -> dict[str, str]:
    """provider id -> UID for every mirror file carrying `prop`."""
    result: dict[str, str] = {}

    for path in sorted(mirror.glob("*.ics")):
        text = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
        uid = remote_id = None

        # Unfold first: Google event ids are long enough for caldir to fold
        # the X-GOOGLE-EVENT-ID line, and a truncated id matches nothing.
        lines: list[str] = []
        for line in text.split("\n"):
            if line.startswith((" ", "\t")) and lines:
                lines[-1] += line[1:]
            else:
                lines.append(line)

        for line in lines:
            if line.startswith("UID:"):
                uid = line[4:].strip()
            elif line.split(":", 1)[0].split(";", 1)[0] == prop:
                remote_id = line.split(":", 1)[1].strip()

        if remote_id:
            result[remote_id] = uid or path.name

    return result


def paged(provision, token: str, url: str, params: dict) -> list[dict]:
    items: list[dict] = []
    params = dict(params)

    while True:
        data = provision.request_json(
            url + "?" + urllib.parse.urlencode(params),
            token=token,
        )
        items.extend(data.get("items", []))

        if not data.get("nextPageToken"):
            return items

        params["pageToken"] = data["nextPageToken"]


def compare(label: str, local: dict[str, str], remote: dict[str, dict],
            status_of) -> None:
    outcome = Counter()
    examples: dict[str, list[str]] = {}

    for remote_id, uid in local.items():
        item = remote.get(remote_id)
        state = "unknown-to-google" if item is None else status_of(item)
        outcome[state] += 1
        examples.setdefault(state, []).append(uid)

    print(f"[diagnose] {label}: {len(local)} local ids -> {dict(outcome)}")

    for state, uids in sorted(examples.items()):
        if state not in {"active", "needsAction", "completed"}:
            for uid in uids[:5]:
                print(f"[diagnose]   {state}: {uid}")


def diagnose_windows(provision, token: str) -> None:
    config = remote_config(WINDOWS_DIR)
    calendar_id = config.get("google_calendar_id")

    if not calendar_id:
        print(f"[diagnose] windows: no google_calendar_id in {WINDOWS_DIR}")
        return

    quoted = urllib.parse.quote(calendar_id, safe="")
    entry = provision.request_json(
        "https://www.googleapis.com/calendar/v3/users/me/calendarList/"
        + quoted,
        token=token,
    )
    print(
        "[diagnose] windows calendar: "
        f"summary={entry.get('summary')!r} "
        f"accessRole={entry.get('accessRole')} "
        f"hidden={entry.get('hidden', False)} "
        f"selected={entry.get('selected', False)} "
        f"timeZone={entry.get('timeZone')}"
    )

    namesakes = [
        item
        for item in paged(
            provision,
            token,
            "https://www.googleapis.com/calendar/v3/users/me/calendarList",
            {"showHidden": "true", "showDeleted": "true", "maxResults": "250"},
        )
        if item.get("summary") == entry.get("summary")
    ]
    print(
        f"[diagnose] calendars named {entry.get('summary')!r}: "
        + ", ".join(
            f"{item.get('id')}"
            f"{' (configured)' if item.get('id') == calendar_id else ''}"
            f" hidden={item.get('hidden', False)}"
            f" selected={item.get('selected', False)}"
            f" deleted={item.get('deleted', False)}"
            for item in namesakes
        )
    )

    events = paged(
        provision,
        token,
        f"https://www.googleapis.com/calendar/v3/calendars/{quoted}/events",
        {"showDeleted": "true", "maxResults": "2500"},
    )
    by_id = {event["id"]: event for event in events}
    statuses = Counter(event.get("status", "confirmed") for event in events)
    starts = sorted(
        (event.get("start") or {}).get("dateTime")
        or (event.get("start") or {}).get("date")
        or ""
        for event in events
        if event.get("status") != "cancelled"
    )
    print(
        f"[diagnose] windows events in Google: {len(events)} {dict(statuses)}"
        + (f" active range {starts[0]} .. {starts[-1]}" if starts else "")
    )

    compare(
        "windows",
        local_ids(WINDOWS_DIR, "X-GOOGLE-EVENT-ID"),
        by_id,
        lambda event: (
            "cancelled" if event.get("status") == "cancelled" else "active"
        ),
    )


def diagnose_tasks(provision, token: str) -> None:
    config = remote_config(TASKS_DIR)
    tasklist_id = config.get("google_resource_id")

    if not tasklist_id:
        print(f"[diagnose] tasks: no google_resource_id in {TASKS_DIR}")
        return

    quoted = urllib.parse.quote(tasklist_id, safe="")
    tasks = paged(
        provision,
        token,
        f"https://tasks.googleapis.com/tasks/v1/lists/{quoted}/tasks",
        {
            "showCompleted": "true",
            "showHidden": "true",
            "showDeleted": "true",
            "maxResults": "100",
        },
    )
    by_id = {task["id"]: task for task in tasks}

    def status_of(task: dict) -> str:
        return "deleted" if task.get("deleted") else task.get("status", "?")

    print(
        f"[diagnose] tasks in Google list: {len(tasks)} "
        f"{dict(Counter(status_of(task) for task in tasks))}"
    )
    compare(
        "tasks",
        local_ids(TASKS_DIR, "X-GOOGLE-TASK-ID"),
        by_id,
        status_of,
    )


def main() -> int:
    provision = load_provision()
    token, _ = provision.get_access_token()

    diagnose_windows(provision, token)
    diagnose_tasks(provision, token)

    return 0


if __name__ == "__main__":
    sys.exit(main())
