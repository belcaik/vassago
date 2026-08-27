#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import re
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request

from datetime import datetime, timedelta, timezone
from pathlib import Path


HOME = Path(os.environ.get("HOME", "/data"))

GOOGLE_ROOT = (
    HOME
    / ".config"
    / "caldir"
    / "providers"
    / "google"
)

APP_CONFIG = GOOGLE_ROOT / "app_config.toml"

ACCOUNT = os.environ.get(
    "GOOGLE_ACCOUNT",
    "belcaik@gmail.com",
)

SESSION_FILE = (
    GOOGLE_ROOT
    / "session"
    / f"{ACCOUNT}.toml"
)

CALENDAR_ROOT = Path(
    os.environ.get(
        "CALENDAR_ROOT",
        "/data/calendars",
    )
)

TASKLIST_NAME = os.environ.get(
    "GOOGLE_UNAB_TASKLIST_NAME",
    "UNAB",
)

TASKLIST_SLUG = os.environ.get(
    "GOOGLE_UNAB_TASKLIST_SLUG",
    "unab",
)

# Reutilizamos uno existente porque el token actual no puede crear
# calendarios secundarios.
WINDOWS_CALENDAR_NAME = os.environ.get(
    "GOOGLE_UNAB_WINDOWS_CALENDAR",
    "UNAB - Ventanas de entrega",
)

WINDOWS_SLUG = os.environ.get(
    "GOOGLE_UNAB_WINDOWS_SLUG",
    "unab-windows",
)


def fail(message: str) -> None:
    print(
        f"[google-provision] fatal: {message}",
        file=sys.stderr,
    )
    raise SystemExit(1)


def load_toml(path: Path) -> dict:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        fail(f"missing {path}")
    except Exception as exc:
        fail(f"could not parse {path}: {exc}")


def request_json(
    url: str,
    *,
    token: str | None = None,
    method: str = "GET",
    data: dict | None = None,
    form: dict | None = None,
) -> dict:
    headers = {
        "Accept": "application/json",
    }

    body = None

    if token is not None:
        headers["Authorization"] = f"Bearer {token}"

    if data is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(data).encode("utf-8")

    if form is not None:
        headers[
            "Content-Type"
        ] = "application/x-www-form-urlencoded"

        body = urllib.parse.urlencode(
            form
        ).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(
            req,
            timeout=30,
        ) as response:
            raw = response.read()

            if not raw:
                return {}

            return json.loads(raw)

    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(
            "utf-8",
            errors="replace",
        )

        fail(
            f"{method} {url}: "
            f"HTTP {exc.code}: {raw}"
        )

    except Exception as exc:
        fail(
            f"{method} {url}: {exc}"
        )


def parse_expiry(value: str) -> datetime:
    value = value.strip()

    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    dt = datetime.fromisoformat(value)

    if dt.tzinfo is None:
        dt = dt.replace(
            tzinfo=timezone.utc
        )

    return dt.astimezone(timezone.utc)


def toml_string(value: str) -> str:
    # JSON string quoting is valid for TOML basic strings.
    return json.dumps(value)


def update_session_token(
    *,
    access_token: str,
    expires_at: datetime,
) -> None:
    raw = SESSION_FILE.read_text(
        encoding="utf-8",
    )

    access_line = (
        "access_token = "
        + toml_string(access_token)
    )

    expiry = (
        expires_at
        .astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )

    expiry_line = (
        "expires_at = "
        + toml_string(expiry)
    )

    raw, access_count = re.subn(
        r'^access_token\s*=\s*".*"$',
        access_line,
        raw,
        count=1,
        flags=re.MULTILINE,
    )

    raw, expiry_count = re.subn(
        r'^expires_at\s*=\s*".*"$',
        expiry_line,
        raw,
        count=1,
        flags=re.MULTILINE,
    )

    if access_count != 1:
        fail(
            "could not update access_token "
            "in existing session"
        )

    if expiry_count != 1:
        fail(
            "could not update expires_at "
            "in existing session"
        )

    temp = SESSION_FILE.with_suffix(
        ".toml.tmp"
    )

    temp.write_text(
        raw,
        encoding="utf-8",
    )

    os.chmod(temp, 0o600)
    os.replace(temp, SESSION_FILE)


def get_access_token() -> tuple[str, dict]:
    app = load_toml(APP_CONFIG)
    session = load_toml(SESSION_FILE)

    if session.get("auth_mode") != "local":
        fail(
            "Google session is not local OAuth"
        )

    scopes = set(
        session.get(
            "granted_scopes",
            [],
        )
    )

    required = {
        "https://www.googleapis.com/auth/tasks",
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
        "https://www.googleapis.com/auth/calendar.calendars",
    }

    missing = required - scopes

    if missing:
        fail(
            "existing Google OAuth session "
            "is missing scopes: "
            + ", ".join(sorted(missing))
        )

    access_token = session.get(
        "access_token"
    )

    refresh_token = session.get(
        "refresh_token"
    )

    expires_at_raw = session.get(
        "expires_at"
    )

    if (
        not access_token
        or not refresh_token
        or not expires_at_raw
    ):
        fail(
            "OAuth session is incomplete"
        )

    expires_at = parse_expiry(
        expires_at_raw
    )

    # Refresh a little early.
    if (
        expires_at
        > datetime.now(timezone.utc)
        + timedelta(minutes=5)
    ):
        return access_token, session

    print(
        "[google-provision] "
        "refreshing existing OAuth token"
    )

    refreshed = request_json(
        "https://oauth2.googleapis.com/token",
        method="POST",
        form={
            "client_id": app["client_id"],
            "client_secret": app[
                "client_secret"
            ],
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )

    new_access = refreshed.get(
        "access_token"
    )

    expires_in = int(
        refreshed.get(
            "expires_in",
            3600,
        )
    )

    if not new_access:
        fail(
            "Google refresh response "
            "did not contain access_token"
        )

    new_expiry = (
        datetime.now(timezone.utc)
        + timedelta(seconds=expires_in)
    )

    # Modificamos sólo token + expiración.
    # No tocamos refresh_token, auth_mode ni granted_scopes.
    update_session_token(
        access_token=new_access,
        expires_at=new_expiry,
    )

    session["access_token"] = (
        new_access
    )

    session["expires_at"] = (
        new_expiry.isoformat()
    )

    return new_access, session


def list_tasklists(
    token: str,
) -> list[dict]:
    result: list[dict] = []
    page_token = None

    while True:
        params = {}

        if page_token:
            params["pageToken"] = (
                page_token
            )

        url = (
            "https://tasks.googleapis.com/"
            "tasks/v1/users/@me/lists"
        )

        if params:
            url += "?" + urllib.parse.urlencode(
                params
            )

        data = request_json(
            url,
            token=token,
        )

        result.extend(
            data.get("items", [])
        )

        page_token = data.get(
            "nextPageToken"
        )

        if not page_token:
            return result


def ensure_tasklist(
    token: str,
) -> str:
    matches = [
        item
        for item in list_tasklists(token)
        if item.get("title")
        == TASKLIST_NAME
    ]

    if len(matches) > 1:
        fail(
            f"multiple Google Task Lists "
            f"named {TASKLIST_NAME!r}"
        )

    if matches:
        resource_id = matches[0]["id"]

        print(
            "[google-provision] "
            f"tasklist exists: "
            f"{TASKLIST_NAME}"
        )

        return resource_id

    print(
        "[google-provision] "
        f"creating tasklist: "
        f"{TASKLIST_NAME}"
    )

    created = request_json(
        (
            "https://tasks.googleapis.com/"
            "tasks/v1/users/@me/lists"
        ),
        token=token,
        method="POST",
        data={
            "title": TASKLIST_NAME,
        },
    )

    resource_id = created.get("id")

    if not resource_id:
        fail(
            "Google Tasks did not return "
            "task list id"
        )

    return resource_id


def list_calendars(
    token: str,
) -> list[dict]:
    result: list[dict] = []
    page_token = None

    while True:
        params = {
            "maxResults": "250",
        }

        if page_token:
            params["pageToken"] = (
                page_token
            )

        url = (
            "https://www.googleapis.com/"
            "calendar/v3/users/me/calendarList?"
            + urllib.parse.urlencode(
                params
            )
        )

        data = request_json(
            url,
            token=token,
        )

        result.extend(
            data.get("items", [])
        )

        page_token = data.get(
            "nextPageToken"
        )

        if not page_token:
            return result


def ensure_windows_calendar(
    token: str,
) -> str:
    calendars = list_calendars(token)

    matches = [
        item
        for item in calendars
        if item.get("summary")
        == WINDOWS_CALENDAR_NAME
    ]

    if len(matches) > 1:
        fail(
            f"multiple Google Calendars named "
            f"{WINDOWS_CALENDAR_NAME!r}"
        )

    if matches:
        calendar = matches[0]

        role = calendar.get("accessRole")

        if role not in {
            "owner",
            "writer",
        }:
            fail(
                f"calendar {WINDOWS_CALENDAR_NAME!r} "
                f"is not writable "
                f"(accessRole={role!r})"
            )

        print(
            "[google-provision] "
            f"calendar exists: "
            f"{WINDOWS_CALENDAR_NAME}"
        )

        return calendar["id"]

    print(
        "[google-provision] "
        f"creating calendar: "
        f"{WINDOWS_CALENDAR_NAME}"
    )

    created = request_json(
        "https://www.googleapis.com/calendar/v3/calendars",
        token=token,
        method="POST",
        data={
            "summary": WINDOWS_CALENDAR_NAME,
            "timeZone": "America/Santiago",
            "description": (
                "Ventanas de disponibilidad y entrega "
                "sincronizadas automáticamente desde UNAB/Canvas."
            ),
        },
    )

    calendar_id = created.get("id")

    if not calendar_id:
        fail(
            "Google Calendar did not return "
            "a calendar id"
        )

    print(
        "[google-provision] "
        f"calendar created: "
        f"{WINDOWS_CALENDAR_NAME}"
    )

    return calendar_id


def write_tasklist_config(
    resource_id: str,
) -> None:
    target = (
        CALENDAR_ROOT
        / TASKLIST_SLUG
        / ".caldir"
    )

    target.mkdir(
        parents=True,
        exist_ok=True,
    )

    config = target / "config.toml"

    desired = f'''name = "{TASKLIST_NAME}"
read_only = false

[remote]
provider = "google"
google_account = "{ACCOUNT}"
google_resource_id = "{resource_id}"
google_resource_kind = "tasklist"
'''

    if (
        config.exists()
        and config.read_text(
            encoding="utf-8"
        )
        == desired
    ):
        print(
            "[google-provision] "
            f"local config OK: "
            f"{TASKLIST_SLUG}"
        )
        return

    config.write_text(
        desired,
        encoding="utf-8",
    )

    print(
        "[google-provision] "
        f"configured local tasklist: "
        f"{TASKLIST_SLUG}"
    )


def write_calendar_config(
    calendar_id: str,
) -> None:
    target = (
        CALENDAR_ROOT
        / WINDOWS_SLUG
        / ".caldir"
    )

    target.mkdir(
        parents=True,
        exist_ok=True,
    )

    config = target / "config.toml"

    desired = f'''name = "UNAB - Ventanas de entrega"
read_only = false

[remote]
provider = "google"
google_account = "{ACCOUNT}"
google_calendar_id = "{calendar_id}"
'''

    if (
        config.exists()
        and config.read_text(
            encoding="utf-8"
        )
        == desired
    ):
        print(
            "[google-provision] "
            f"local config OK: "
            f"{WINDOWS_SLUG}"
        )
        return

    config.write_text(
        desired,
        encoding="utf-8",
    )

    print(
        "[google-provision] "
        f"configured local calendar: "
        f"{WINDOWS_SLUG}"
    )


def main() -> int:
    print(
        "[google-provision] "
        f"account: {ACCOUNT}"
    )

    token, _ = get_access_token()

    tasklist_id = ensure_tasklist(
        token
    )

    windows_calendar_id = (
        ensure_windows_calendar(
            token
        )
    )

    write_tasklist_config(
        tasklist_id
    )

    write_calendar_config(
        windows_calendar_id
    )

    print(
        "[google-provision] done"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
