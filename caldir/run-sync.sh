#!/bin/sh
set -eu

CALDIR_BIN="$(command -v caldir || true)"

if [ -z "$CALDIR_BIN" ]; then
    echo "[caldir] fatal: caldir binary not found in PATH" >&2
    exit 127
fi

echo "[caldir] $(date -Is) using caldir: $CALDIR_BIN"

echo "[caldir] $(date -Is) provisioning Radicale"
/usr/local/bin/provision-radicale.sh

echo "[caldir] $(date -Is) provisioning Google"
/usr/local/bin/provision-google.py

echo "[caldir] $(date -Is) merging u_crawler"
/usr/local/bin/merge-ucrawler.py

echo "[caldir] $(date -Is) phase 1: sync providers"
cd /data/calendars
"$CALDIR_BIN" sync

echo "[caldir] $(date -Is) bridge VTODO"
set +e
/usr/local/bin/bridge-vtodo.py
vtodo_rc=$?
set -e

if [ "$vtodo_rc" -ne 0 ]; then
    echo "[caldir] VTODO bridge failed/conflicted (rc=$vtodo_rc)" >&2
    exit "$vtodo_rc"
fi

echo "[caldir] $(date -Is) bridge VEVENT windows"
set +e
/usr/local/bin/bridge-windows.py
windows_rc=$?
set -e

if [ "$windows_rc" -ne 0 ]; then
    echo "[caldir] windows bridge failed/conflicted (rc=$windows_rc)" >&2
    exit "$windows_rc"
fi

echo "[caldir] $(date -Is) phase 2: publish bridge results"
cd /data/calendars
"$CALDIR_BIN" sync

echo "[caldir] $(date -Is) done"
