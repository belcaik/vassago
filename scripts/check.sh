#!/bin/sh
# Single validation entry point for the Vassago repository.
#
# Runs seven checks in order and stops at the first failure. Nothing here
# builds an image, starts a container or touches runtime state; every check is
# static except the unit tests, which work in temporary directories.
#
# Usage: sh scripts/check.sh
set -eu

cd "$(dirname "$0")/.."

TOTAL=7

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


# 4. Term prefix -------------------------------------------------------------
step 4 "term prefix lockstep"

term_files="caldir/provision-radicale.sh caldir/merge-ucrawler.py \
caldir/dedupe-vtodo.py caldir/bridge-vtodo.py caldir/bridge-windows.py"

: > "$TMPDIR_CHECK/prefixes"

for file in $term_files; do
    found="$(grep -oE '[0-9]{6}_' "$file" | sort -u | tr '\n' ' ')"

    [ -n "$found" ] || fail "$file declares no NNNNNN_ term prefix"

    set -- $found
    [ "$#" -eq 1 ] || fail "$file mixes several term prefixes: $found"

    echo "$1" >> "$TMPDIR_CHECK/prefixes"
done

term_prefix="$(sort -u < "$TMPDIR_CHECK/prefixes" | tr '\n' ' ')"

set -- $term_prefix
[ "$#" -eq 1 ] \
    || fail "term prefixes are out of lockstep across the five scripts: $term_prefix"


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

for script in $(cat "$TMPDIR_CHECK/copied"); do
    [ -f "caldir/$script" ] \
        || fail "the Dockerfile COPYs caldir/$script, which does not exist"
done


# 6. Unit tests --------------------------------------------------------------
step 6 "unit tests"

python3 -m unittest discover -s tests -v || fail "unit tests failed"


# 7. Documentation paths -----------------------------------------------------
step 7 "documentation paths"

python3 scripts/check_docs_paths.py || fail "documentation references a missing path"


echo "[check] all passed"
