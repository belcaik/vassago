#!/bin/sh
# Single validation entry point for the Vassago repository.
#
# Runs eight checks in order and stops at the first failure. Nothing here
# builds an image, starts a container or touches runtime state; every check is
# static except the unit tests, which work in temporary directories.
#
# Usage: sh scripts/check.sh
set -eu

cd "$(dirname "$0")/.."

TOTAL=8

TMPDIR_CHECK="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_CHECK"' EXIT INT TERM

# Keep the tree clean: no __pycache__ next to the scripts under test.
PYTHONDONTWRITEBYTECODE=1
PYTHONPYCACHEPREFIX="$TMPDIR_CHECK/pycache"
export PYTHONDONTWRITEBYTECODE PYTHONPYCACHEPREFIX

step() {
    echo "[check] $1/$TOTAL $2"
}

fail() {
    echo "[check] FAILED: $*" >&2
    exit 1
}


# 1. Syntax ------------------------------------------------------------------
step 1 "syntax: sh -n and python3 -m py_compile"

for file in $(git ls-files '*.sh'); do
    sh -n "$file" || fail "shell syntax error in $file"
done

py_files="$(git ls-files '*.py')"

if [ -n "$py_files" ]; then
    # shellcheck disable=SC2086
    python3 -m py_compile $py_files || fail "python syntax error"
fi


# 2. Compose file ------------------------------------------------------------
step 2 "compose config"

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1
then
    env_file=".env.example"

    if [ ! -f "$env_file" ]; then
        env_file="$TMPDIR_CHECK/env.example"
        printf 'RADICALE_USER=x\nRADICALE_PASSWORD=x\n' > "$env_file"
    fi

    docker compose --env-file "$env_file" config -q \
        || fail "compose.yaml is not valid"
else
    echo "[check] skip: docker not available"
fi


# 3. Secret guard ------------------------------------------------------------
step 3 "secret guard: no runtime state or credentials tracked"

tracked_secrets="$(
    git ls-files \
        | grep -v '^\.env\.example$' \
        | grep -E '^\.env$|^\.env\.|^radicale/config/users$|^radicale/data/|^caldir/config/|^caldir/state/|^caldir/calendars/|^u-crawler/config/|quarantine-|\.bak$|\.tar(\.gz)?$' \
        || true
)"

if [ -n "$tracked_secrets" ]; then
    echo "$tracked_secrets" >&2
    fail "the paths above are runtime state or secrets and must not be tracked"
fi


# 4. No hardcoded term ------------------------------------------------------
step 4 "no academic term code in pipeline scripts"

# Courses are discovered structurally by caldir/courses.py (ADR-0009). A
# NNNNNN_ literal in a pipeline script means a term got hardcoded again.
term_hits="$(grep -nE '\b[0-9]{6}_' caldir/*.py caldir/*.sh || true)"

if [ -n "$term_hits" ]; then
    echo "$term_hits" >&2
    fail "pipeline scripts must not hardcode an academic term code"
fi


# 5. Dockerfile coverage -----------------------------------------------------
step 5 "run-sync.sh scripts are COPY'd in the Dockerfile"

grep -oE '/usr/local/bin/[A-Za-z0-9_.-]+' caldir/run-sync.sh \
    | sed 's#.*/##' \
    | sort -u > "$TMPDIR_CHECK/invoked"

for script in $(cat "$TMPDIR_CHECK/invoked"); do
    grep -qE "^COPY[[:space:]]+$script[[:space:]]" caldir/Dockerfile \
        || fail "run-sync.sh invokes $script, which the Dockerfile never COPYs"
done

grep -E '^COPY[[:space:]]+[^-]' caldir/Dockerfile \
    | awk '{print $2}' \
    | sort -u > "$TMPDIR_CHECK/copied"

# Helpers imported by the copied scripts must ship with them.
if grep -lq '^import courses$' caldir/*.py; then
    grep -qE '^COPY[[:space:]]+courses\.py[[:space:]]' caldir/Dockerfile \
        || fail "scripts import courses.py, which the Dockerfile never COPYs"
fi

for script in $(cat "$TMPDIR_CHECK/copied"); do
    [ -f "caldir/$script" ] \
        || fail "the Dockerfile COPYs caldir/$script, which does not exist"
done


# 6. Reproducible caldir build -----------------------------------------------
step 6 "caldir Dockerfile pins an exact commit"

grep -qE '^ARG[[:space:]]+CALDIR_COMMIT=[0-9a-f]{40}[[:space:]]*$' caldir/Dockerfile \
    || fail "caldir/Dockerfile must declare ARG CALDIR_COMMIT=<40-hex SHA>"

if grep -n -e '--branch' caldir/Dockerfile >&2; then
    fail "caldir/Dockerfile builds from a mutable --branch ref"
fi

if grep -nE '\\[[:space:]]+$' caldir/Dockerfile >&2; then
    fail "caldir/Dockerfile has a line continuation followed by trailing whitespace"
fi


# 7. Unit tests --------------------------------------------------------------
step 7 "unit tests"

python3 -m unittest discover -s tests -v || fail "unit tests failed"


# 8. Documentation paths -----------------------------------------------------
step 8 "documentation paths"

python3 scripts/check_docs_paths.py || fail "documentation references a missing path"


echo "[check] all passed"
