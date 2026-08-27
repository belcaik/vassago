FROM rust:1.97.0-bookworm AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       git \
       pkg-config \
       libssl-dev \
    && rm -rf /var/lib/apt/lists/*

RUN git clone \
    --depth 1 \
    --branch vtodo-support \
    https://github.com/belcaik/caldir.git \
    /src/caldir

WORKDIR /src/caldir

RUN rm -f rust-toolchain.toml \
    && rustc --version \
    && cargo --version

RUN cargo build --release --workspace

RUN mkdir -p /build/bin \
    && cp target/release/caldir /build/bin/ \
    && cp target/release/caldir-provider-google /build/bin/ \
    && cp target/release/caldir-provider-outlook /build/bin/ \
    && cp target/release/caldir-provider-icloud /build/bin/ \
    && cp target/release/caldir-provider-caldav /build/bin/ \
    && cp target/release/caldir-provider-webcal /build/bin/


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

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY provision-radicale.sh /usr/local/bin/provision-radicale.sh
COPY merge-ucrawler.py /usr/local/bin/merge-ucrawler.py
COPY bridge-vtodo.py /usr/local/bin/bridge-vtodo.py
COPY bridge-windows.py /usr/local/bin/bridge-windows.py
COPY run-sync.sh /usr/local/bin/run-sync.sh
COPY provision-google.py /usr/local/bin/provision-google.py


RUN chmod +x \
    /usr/local/bin/caldir \
    /usr/local/bin/caldir-provider-google \
    /usr/local/bin/caldir-provider-outlook \
    /usr/local/bin/caldir-provider-icloud \
    /usr/local/bin/caldir-provider-caldav \
    /usr/local/bin/caldir-provider-webcal \
    /usr/local/bin/entrypoint.sh \
    /usr/local/bin/provision-radicale.sh \
    /usr/local/bin/merge-ucrawler.py \
    /usr/local/bin/bridge-vtodo.py \
    /usr/local/bin/provision-google.py \
    /usr/local/bin/bridge-windows.py \ 
    /usr/local/bin/run-sync.sh

ENV HOME=/data
WORKDIR /data

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
