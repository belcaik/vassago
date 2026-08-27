# ADR-0007: Runtime state and secrets live in gitignored bind mounts

- Status: Accepted (retroactive — documents a decision already implemented)
- Date: 2026-08-26

## Context

This stack holds several kinds of long-lived, sensitive state: a Radicale
password database, Google OAuth client credentials and refresh tokens, LMS
credentials for the crawler, the CalDAV collection store, the generated
calendars, and the bridge state files that decide what counts as a change. All
of it is host-specific and none of it belongs in a public repository.

At the same time the deployment is a plain Compose project on a single host, with
no secret manager and no orchestrator to inject anything.

## Decision

Every runtime and credential path is a host directory sitting next to
`compose.yaml`, bind-mounted into the container that needs it, and listed in
`.gitignore`. The repository tracks the code and the configuration templates;
the host owns the state.

The paths, all relative to the repository root:

| Host path | Mounted at | Holds |
| --- | --- | --- |
| `.env` | (Compose variable substitution) | `RADICALE_USER`, `RADICALE_PASSWORD` |
| `radicale/config/users` | `/etc/radicale/users` (via the read-only config mount) | htpasswd database |
| `radicale/data/` | `/var/lib/radicale` | CalDAV collection store |
| `caldir/config/` | `/data/.config/caldir` | Google OAuth app config and session tokens |
| `caldir/state/` | `/data/.local/share/caldir` | bridge state JSON, VTODO quarantine |
| `caldir/calendars/` | `/data/calendars` and `/home/appuser/Caldir` | all generated calendars, the shared bus |
| `u-crawler/config/` | `/home/appuser/.config/u_crawler` | crawler credentials and runtime config |

`.gitignore` additionally covers `**/quarantine-*/`, `*.bak`, `*.tar` and
`*.tar.gz`, so archives and quarantined files cannot be committed by accident,
and `.env.*` is ignored with `!.env.example` re-included so a template can be
tracked.

`.env` carries only the Radicale credentials. Nothing else in the stack reads a
secret from the environment: the Google credentials are files under
`caldir/config/`, and the crawler's credentials are files under
`u-crawler/config/`.

The provisioning scripts create and migrate this state idempotently, so an empty
host directory is a valid starting point for everything except the two things a
human must supply — the htpasswd file and the Google OAuth session.
`provision-radicale.sh` creates each collection and its `.caldir/config.toml`
only when the config is absent, and appends the task-capability key to configs
that predate it. `provision-google.py` reuses an existing task list or calendar
when one matches by name and creates it otherwise, and rewrites a
`.caldir/config.toml` only when its content differs from what it wants. Token
refresh is surgical: it rewrites only the `access_token` and `expires_at` lines
of the session file, leaving `refresh_token`, `auth_mode` and `granted_scopes`
untouched, through a temp file that is `chmod 0600` before `os.replace`.

## Consequences

- The repository can be public. No credential and no personal calendar content
  is tracked; the only place a real value appears is the host filesystem.
- A fresh deployment is not `git clone && up`. The host must be seeded with
  `.env`, an htpasswd `users` file created by hand (nothing in the repository
  generates it), and a Google OAuth session with the four required scopes and
  `auth_mode = "local"` already granted. `provision-google.py` refuses to run
  otherwise rather than starting an interactive flow.
- Backups are a host concern and are not described by anything in the repository.
  Losing `caldir/state/` is not fatal but is not free either: a bridge whose
  state file is missing or carries an unexpected version starts from an empty
  state and re-observes every item.
- Because the state directories are bind mounts rather than named volumes, they
  are inspectable and editable with ordinary tools, which is what makes the
  manual repair paths (quarantine, hand-fixing an ICS) practical.
- The mounts carry SELinux relabel flags — private (`:Z`) for the per-service
  directories, shared (`:z`) for the calendars directory used by two services —
  and the Radicale data mount adds `:U` to fix ownership. This ties the Compose
  file to a runtime that understands those flags.
- Secret rotation is per-file and manual: a new Radicale password means editing
  `.env` and regenerating `users`; a revoked Google grant means replacing the
  session file.

## Evidence

- `.gitignore:1-4` — `.env` and `.env.*` ignored, `!.env.example` re-included.
- `.gitignore:6-8` — `radicale/data/` and `radicale/config/users`.
- `.gitignore:10-12` — `caldir/config/` (OAuth credentials and sessions) and
  `caldir/state/`.
- `.gitignore:14-15` — `caldir/calendars/`.
- `.gitignore:17-18` — `u-crawler/config/`.
- `.gitignore:20-24` — `**/quarantine-*/`, `*.bak`, `*.tar`, `*.tar.gz`.
- `compose.yaml:12-14` — Radicale's read-only config mount and its data mount
  (`:Z,U`).
- `compose.yaml:31-33` — `RADICALE_USER` / `RADICALE_PASSWORD` interpolated from
  `.env`; the only secrets passed as environment variables.
- `compose.yaml:35-38` — the three `caldir` bind mounts: config, state, calendars.
- `compose.yaml:62-66` — the two `u-crawler` bind mounts.
- `radicale/config/config:4-6` — `htpasswd_filename = /etc/radicale/users`, the
  file that is gitignored and hand-created.
- `radicale/config/config:9-10` — `filesystem_folder = /var/lib/radicale/collections`,
  backed by the `radicale/data/` mount.
- `caldir/provision-radicale.sh:20,60-76` — collection config written only when
  absent; otherwise migrated in place by appending the missing key.
- `caldir/provision-google.py:18-46` — credential and session paths derived from
  `HOME` and `CALENDAR_ROOT`, both inside the mounted directories.
- `caldir/provision-google.py:247-273` — the run refuses to proceed unless
  `auth_mode == "local"` and all four required scopes were granted.
- `caldir/provision-google.py:230-240` — session rewrite through a temp file,
  `chmod 0600`, then `os.replace`.
- `caldir/provision-google.py:348-353` — the comment confirming only the token
  and expiry are touched, never `refresh_token`, `auth_mode` or
  `granted_scopes`.
- `caldir/provision-google.py:407-460,504-579` — task list and calendar reused
  when present, created when absent.
- `caldir/provision-google.py:608-631,659-682` — local collection configs
  rewritten only when their content differs.
- `caldir/bridge-vtodo.py:30-35,427-450` — bridge state file path under the state
  mount, and the reset-to-empty behaviour on a version mismatch.
- `caldir/bridge-windows.py:29-34,214-236` — the same for the windows bridge.
- `caldir/dedupe-vtodo.py:11-12` — the quarantine directory under the state mount.
