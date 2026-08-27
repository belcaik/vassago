#!/bin/sh
set -eu

ROOT="/data/calendars"
RADICALE_URL="${RADICALE_URL:-http://radicale:5232}"
RADICALE_USER="${RADICALE_USER:?RADICALE_USER required}"
RADICALE_PASSWORD="${RADICALE_PASSWORD:?RADICALE_PASSWORD required}"

provision_calendar() {
    course="$1"
    kind="$2"

    flat_name="${course}__${kind}"
    target="$ROOT/$flat_name"
    cfg="$target/.caldir/config.toml"
    collection_url="${RADICALE_URL}/${RADICALE_USER}/${flat_name}/"

    mkdir -p "$target/.caldir"

    if [ ! -f "$cfg" ]; then
        echo "[provision] creating $flat_name"

        http_code="$(
            curl -sS \
                -o /tmp/radicale-response \
                -w '%{http_code}' \
                -u "${RADICALE_USER}:${RADICALE_PASSWORD}" \
                -X MKCOL \
                "$collection_url" \
                --data "<?xml version=\"1.0\" encoding=\"UTF-8\" ?>
<create xmlns=\"DAV:\" xmlns:C=\"urn:ietf:params:xml:ns:caldav\">
  <set>
    <prop>
      <resourcetype>
        <collection />
        <C:calendar />
      </resourcetype>

      <C:supported-calendar-component-set>
        <C:comp name=\"VEVENT\" />
        <C:comp name=\"VTODO\" />
      </C:supported-calendar-component-set>

      <displayname>${flat_name}</displayname>
    </prop>
  </set>
</create>"
        )"

        case "$http_code" in
            200|201|204|405)
                ;;
            *)
                echo "[provision] failed $flat_name: HTTP $http_code" >&2
                cat /tmp/radicale-response >&2 || true
                return 1
                ;;
        esac

        cat >"$cfg" <<CFG
name = "$flat_name"
read_only = false

[remote]
provider = "caldav"
caldav_account = "${RADICALE_USER}@radicale"
caldav_calendar_url = "${collection_url}"
caldav_supports_tasks = true
CFG
    else
        # Migration for configs created before VTODO capability metadata.
        if ! grep -q '^caldav_supports_tasks[[:space:]]*=' "$cfg"; then
            echo "[provision] migrating task capability: $flat_name"
            printf '\ncaldav_supports_tasks = true\n' >>"$cfg"
        fi
    fi
}

for course_dir in "$ROOT"/202615_*; do
    [ -d "$course_dir" ] || continue

    course="$(basename "$course_dir")"

    if [ -d "$course_dir/deadlines" ]; then
        provision_calendar "$course" "deadlines"
    fi

    if [ -d "$course_dir/windows" ]; then
        provision_calendar "$course" "windows"
    fi
done
