#!/bin/sh
set -eu

CRON_SCHEDULE="${CRON_SCHEDULE:-*/15 * * * *}"

cat >/etc/cron.d/caldir <<EOF
SHELL=/bin/sh
PATH=/usr/local/bin:/usr/bin:/bin
HOME=/data
${CRON_SCHEDULE} root /usr/local/bin/run-sync.sh >>/proc/1/fd/1 2>>/proc/1/fd/2
EOF

chmod 0644 /etc/cron.d/caldir

echo "[caldir] schedule=${CRON_SCHEDULE}"

if [ "${RUN_ON_START:-true}" = "true" ]; then
    echo "[caldir] initial provisioning + sync"
    /usr/local/bin/run-sync.sh || true
fi

exec cron -f
