#!/bin/sh
# Run the sync pipeline with the container environment restored.
#
# cron starts jobs with an empty environment, so entrypoint.sh snapshots the
# allowlisted container variables into $ENV_FILE at start-up. Both the cron
# job and the RUN_ON_START run go through this wrapper: it refuses a snapshot
# anyone but the invoking user (root in the container) could have written or
# read, sources it, takes a non-blocking lock so two runs never overlap, and
# execs run-sync.sh.
#
# The CALDIR_CRON_ENV_FILE, CALDIR_CRON_LOCK_FILE and CALDIR_RUN_SYNC overrides
# exist only for tests/test_cron_env.py.
set -eu

umask 077

ENV_FILE="${CALDIR_CRON_ENV_FILE:-/run/caldir/cron.env}"
LOCK_FILE="${CALDIR_CRON_LOCK_FILE:-/run/caldir/sync.lock}"
RUN_SYNC="${CALDIR_RUN_SYNC:-/usr/local/bin/run-sync.sh}"

die() {
    echo "[caldir] cron-run: fatal: $*" >&2
    exit 78
}

[ ! -L "$ENV_FILE" ] || die "env snapshot $ENV_FILE is a symlink"
[ -f "$ENV_FILE" ] || die "env snapshot $ENV_FILE is missing (did entrypoint.sh run?)"

meta="$(stat -c '%u %a' "$ENV_FILE")" || die "cannot stat $ENV_FILE"
owner="${meta% *}"
mode="${meta#* }"
self="$(id -u)"

[ "$mode" = "600" ] || die "env snapshot $ENV_FILE has mode $mode, expected 600"
[ "$owner" = "$self" ] \
    || die "env snapshot $ENV_FILE is owned by uid $owner, expected uid $self"

# shellcheck disable=SC1090
. "$ENV_FILE"

if command -v flock >/dev/null 2>&1; then
    # fd 9 stays open across exec, so run-sync.sh and its children hold the
    # lock until the whole pipeline exits.
    exec 9>>"$LOCK_FILE"

    if ! flock -n 9; then
        echo "[caldir] $(date -Is) previous sync still running; skipping this run" >&2
        exit 0
    fi
else
    echo "[caldir] cron-run: warning: flock not found; running without a lock" >&2
fi

exec "$RUN_SYNC"
