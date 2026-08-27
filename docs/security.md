# Security

What secrets exist, where they live, who reads them, and what is deliberately
kept out of git. Document reflects tracked files only; see
`docs/operations.md` for setup steps.

## Secret inventory

| Path (gitignored) | Contents | Read by |
| --- | --- | --- |
| `.env` | `RADICALE_USER`, `RADICALE_PASSWORD` | `docker compose` / `podman compose`, interpolated into the `caldir` service environment |
| `radicale/config/users` | htpasswd credentials for Radicale accounts | `radicale` (via `[auth] htpasswd_filename` in `radicale/config/config`) |
| `caldir/config/providers/google/app_config.toml` | Google OAuth `client_id`, `client_secret` | `provision-google.py` |
| `caldir/config/providers/google/session/<account>.toml` | `access_token`, `refresh_token`, `expires_at`, `auth_mode`, `granted_scopes` | `provision-google.py`; also the `caldir`/`caldir-provider-google` binary during `caldir sync` |
| `caldir/state/` | Bridge state JSON (`vtodo-bridge-state.json`, `windows-bridge-state.json`) and quarantine data | `bridge-vtodo.py`, `bridge-windows.py`, `dedupe-vtodo.py` |
| `caldir/calendars/` | All ICS data (u_crawler output, canonical collections, Google-mirror working copies) plus each collection's `.caldir/config.toml`, which embeds `caldav_account`/`caldav_calendar_url` or `google_account`/`google_resource_id` | `caldir`, `merge-ucrawler.py`, `provision-radicale.sh`, `provision-google.py`, both bridges |
| `u-crawler/config/` | u_crawler's own credentials/config for the LMS it crawls | `u-crawler` container only (not read by `caldir`) |

## Access model

Radicale authenticates with htpasswd (`radicale/config/config`:
`[auth] type = htpasswd`, `htpasswd_encryption = autodetect`) and authorizes
with `[rights] type = owner_only` — each user can only access collections
under their own username. `caldir` authenticates to Radicale as a CalDAV
client using `RADICALE_USER`/`RADICALE_PASSWORD` (from `.env`, passed through
`compose.yaml`); `provision-radicale.sh` creates collections at
`<RADICALE_URL>/<RADICALE_USER>/<flat_name>/`, so `caldir` only ever
provisions and syncs under its own Radicale user. Google access is via an
OAuth session (`caldir/config/providers/google/session/<account>.toml`);
`caldir` never handles a Google username/password, only the delegated tokens
in that session file.

## Network exposure

`radicale` is the only service in `compose.yaml` with a `ports:` mapping —
published on the host address set in `compose.yaml`, port `5232`. `caldir`
and `u-crawler` publish no ports. All three services share the internal
`calendar` bridge network (`compose.yaml`, top-level `networks:`), and
`caldir` reaches Radicale over that network at `http://radicale:5232`
(`RADICALE_URL` in `compose.yaml`, and the default in
`provision-radicale.sh`) rather than through the published host port.

## Token handling

`provision-google.py` refreshes the Google access token when it is within 5
minutes of `expires_at` (`get_access_token`, `caldir/provision-google.py`).
On refresh, `update_session_token` rewrites only the `access_token` and
`expires_at` lines of the existing session TOML file via regex substitution
— it writes to a `.toml.tmp` temp file, `chmod`s it `0600`, then
`os.replace`s it over the session file (atomic on POSIX). `refresh_token`,
`auth_mode`, and `granted_scopes` are never modified by this rewrite; the
initial OAuth authorization (which produces `refresh_token` and
`granted_scopes`) is out of scope for this script, see
`docs/operations.md#google-oauth-prerequisites`.

## What is gitignored

Mirrors `.gitignore`:

| Pattern | Why |
| --- | --- |
| `.env`, `.env.*` (except `!.env.example`) | Holds `RADICALE_USER`/`RADICALE_PASSWORD`; `.env.example` is the only variant meant to be tracked, as a template with placeholder values |
| `radicale/data/` | Radicale's persistent collection storage and its own runtime state |
| `radicale/config/users` | htpasswd credentials, created by hand, not by any script in this repo |
| `caldir/config/` | Google OAuth app credentials and OAuth session tokens |
| `caldir/state/` | Bridge state JSON and quarantine data — machine-specific sync history, not portable across environments |
| `caldir/calendars/` | Generated/synced calendar data (u_crawler output, canonical collections, provider configs derived from live Radicale/Google resource IDs) — runtime state, not source |
| `u-crawler/config/` | u_crawler's own credentials/runtime config |
| `**/quarantine-*/` | Duplicate ICS files moved aside by `dedupe-vtodo.py`; local remediation artifacts, not source |
| `*.bak`, `*.tar`, `*.tar.gz` | Ad hoc backups/archives, not meant to be versioned |
| `__pycache__/`, `*.pyc` | Python bytecode caches |
| `.vscode/`, `.idea/`, `*.swp` | Editor-local state |
