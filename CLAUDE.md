@AGENTS.md

- Validation entry point: `sh scripts/check.sh`.
- Project skills live in `.claude/skills/`; invoke them by name.
- The runtime host is a separate machine. Treat this checkout as source only:
  run `docker compose` / `podman compose` here only when the user asks.
- The runtime directories `caldir/config`, `caldir/state`, `caldir/calendars`,
  `radicale/data`, and `u-crawler/config` hold live credentials and personal
  calendar data. If they exist locally, leave them unread and unprinted.
