# caldir is built from one exact commit of the fork. Changing CALDIR_COMMIT
# busts the fetch/compile layers; leaving it unchanged reuses them.
ARG CALDIR_REPO=https://github.com/belcaik/caldir.git
ARG CALDIR_COMMIT=3ca66aa3ba1fc1615cba0f54b86f22326fca023a

FROM rust:1.97.0-bookworm AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       git \
       pkg-config \
       libssl-dev \
    && rm -rf /var/lib/apt/lists/*

ARG CALDIR_REPO
ARG CALDIR_COMMIT

RUN set -eu; \
    case "$CALDIR_COMMIT" in \
        *[!0-9a-f]*|"") echo "CALDIR_COMMIT must be a full 40-hex SHA" >&2; exit 1 ;; \
    esac; \
    [ "${#CALDIR_COMMIT}" -eq 40 ] \
        || { echo "CALDIR_COMMIT must be a full 40-hex SHA" >&2; exit 1; }; \
    git init -q /src/caldir; \
    cd /src/caldir; \
    git remote add origin "$CALDIR_REPO"; \
    git fetch --depth 1 origin "$CALDIR_COMMIT"; \
    git checkout -q --detach FETCH_HEAD; \
    head="$(git rev-parse HEAD)"; \
    if [ "$head" != "$CALDIR_COMMIT" ]; then \
        echo "checked out $head, expected $CALDIR_COMMIT" >&2; \
        exit 1; \
    fi

WORKDIR /src/caldir

RUN rm -f rust-toolchain.toml \
    && rustc --version \
    && cargo --version

# Build, check, copy and clean in one layer so target/ never lands in the
# build cache. caldir-provider-google must carry every OAuth scope the
# pipeline relies on; a stale or wrong commit fails here, not at runtime.
RUN set -eu; \
    bins="caldir caldir-provider-google caldir-provider-outlook \
          caldir-provider-icloud caldir-provider-caldav caldir-provider-webcal"; \
    cargo build --release --workspace; \
    for scope in \
        https://www.googleapis.com/auth/calendar.calendarlist.readonly \
        https://www.googleapis.com/auth/calendar.events \
        https://www.googleapis.com/auth/calendar.calendars \
        https://www.googleapis.com/auth/tasks; \
    do \
        grep -aqF "$scope" target/release/caldir-provider-google \
            || { echo "caldir-provider-google lacks OAuth scope $scope" >&2; exit 1; }; \
    done; \
    mkdir -p /build/bin /build/share; \
    for bin in $bins; do cp "target/release/$bin" /build/bin/; done; \
    { \
        echo "caldir_repo=$CALDIR_REPO"; \
        echo "caldir_commit=$CALDIR_COMMIT"; \
        echo "rustc=$(rustc --version)"; \
        cd /build/bin; \
        for bin in $bins; do echo "sha256 $(sha256sum "$bin")"; done; \
    } > /build/share/BUILD_INFO; \
    cd /src/caldir; \
    cargo clean


FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates \
       cron \
       curl \
       python3 \
       tzdata \
    && rm -rf /var/lib/apt/lists/*

# IMPORTANTE: binarios compilados en la primera stage.
COPY --from=builder /build/bin/ /usr/local/bin/
COPY --from=builder /build/share/BUILD_INFO /usr/local/share/caldir/BUILD_INFO

# Scripts after the binaries: editing one never recompiles Rust.
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY cron-run.sh /usr/local/bin/cron-run.sh
COPY provision-radicale.sh /usr/local/bin/provision-radicale.sh
COPY merge-ucrawler.py /usr/local/bin/merge-ucrawler.py
COPY bridge-vtodo.py /usr/local/bin/bridge-vtodo.py
COPY bridge-windows.py /usr/local/bin/bridge-windows.py
COPY run-sync.sh /usr/local/bin/run-sync.sh
COPY provision-google.py /usr/local/bin/provision-google.py
COPY courses.py /usr/local/bin/courses.py
COPY verify-sync.py /usr/local/bin/verify-sync.py
COPY diagnose-google.py /usr/local/bin/diagnose-google.py


RUN chmod +x \
    /usr/local/bin/caldir \
    /usr/local/bin/caldir-provider-google \
    /usr/local/bin/caldir-provider-outlook \
    /usr/local/bin/caldir-provider-icloud \
    /usr/local/bin/caldir-provider-caldav \
    /usr/local/bin/caldir-provider-webcal \
    /usr/local/bin/entrypoint.sh \
    /usr/local/bin/cron-run.sh \
    /usr/local/bin/provision-radicale.sh \
    /usr/local/bin/merge-ucrawler.py \
    /usr/local/bin/bridge-vtodo.py \
    /usr/local/bin/provision-google.py \
    /usr/local/bin/bridge-windows.py \
    /usr/local/bin/verify-sync.py \
    /usr/local/bin/diagnose-google.py \
    /usr/local/bin/run-sync.sh

ARG CALDIR_COMMIT
ARG VASSAGO_REVISION=unknown

RUN echo "vassago_revision=${VASSAGO_REVISION}" >> /usr/local/share/caldir/BUILD_INFO

LABEL org.opencontainers.image.revision="${VASSAGO_REVISION}" \
      io.vassago.caldir.commit="${CALDIR_COMMIT}" \
      io.vassago.revision="${VASSAGO_REVISION}"

ENV HOME=/data
WORKDIR /data

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
