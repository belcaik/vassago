#!/bin/sh
set -eu

CRON_SCHEDULE="${CRON_SCHEDULE:-*/15 * * * *}"

# The CALDIR_CRON_ENV_FILE, CALDIR_CRON_D_FILE and CALDIR_SKIP_EXEC overrides
# exist only for tests/test_cron_env.py.
CRON_ENV_FILE="${CALDIR_CRON_ENV_FILE:-/run/caldir/cron.env}"
CRON_D_FILE="${CALDIR_CRON_D_FILE:-/etc/cron.d/caldir}"
CRON_RUN=/usr/local/bin/cron-run.sh

# cron starts jobs with an empty environment. Snapshot the variables the
# pipeline reads (grep os.environ and ${...} under caldir/) so cron-run.sh can
# restore them. Values are never printed.
write_env_snapshot() {
    env_dir="$(dirname "$CRON_ENV_FILE")"

    (
        umask 077
        mkdir -p "$env_dir"
        chmod 0700 "$env_dir"

        tmp="$(mktemp "$env_dir/.cron.env.XXXXXX")"
        trap 'rm -f "$tmp"' EXIT

        (
            for name in $(env | sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p'); do
                case "$name" in
                    CALDIR_CRON_*|CALDIR_SKIP_EXEC|CALDIR_RUN_SYNC)
                        unset "$name" 2>/dev/null || true ;;
                    TZ|HOME|PATH|LANG|LANGUAGE|LC_*|XDG_*|RUST_LOG|RUST_BACKTRACE) ;;
                    RADICALE_*|GOOGLE_*|CALENDAR_ROOT|CALDIR_*) ;;
                    BRIDGE_*|WINDOWS_BRIDGE_*|CRON_SCHEDULE|RUN_ON_START) ;;
                    *)
                        unset "$name" 2>/dev/null || true ;;
                esac
            done
            export -p
        ) >"$tmp"

        chmod 0600 "$tmp"
        mv -f "$tmp" "$CRON_ENV_FILE"
        trap - EXIT
    )
}

write_env_snapshot

# TZ in the header gives cron jobs local time; the cron daemon itself
# schedules in the TZ it inherits from this process at `exec cron -f`.
tz_line=""
case "${TZ:-}" in
    ""|*[!A-Za-z0-9_/+:.-]*) ;;
    *) tz_line="TZ=${TZ}" ;;
esac

cat >"$CRON_D_FILE" <<EOF
SHELL=/bin/sh
PATH=/usr/local/bin:/usr/bin:/bin
HOME=/data
${tz_line}
${CRON_SCHEDULE} root ${CRON_RUN} >>/proc/1/fd/1 2>>/proc/1/fd/2
EOF

chmod 0644 "$CRON_D_FILE"

echo "[caldir] schedule=${CRON_SCHEDULE}"

if [ "${RUN_ON_START:-true}" = "true" ]; then
    echo "[caldir] initial provisioning + sync"
    "$CRON_RUN" || true
fi

if [ "${CALDIR_SKIP_EXEC:-}" = "1" ]; then
    exit 0
fi

exec cron -f
