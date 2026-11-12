FROM caddy:2.8.4 AS caddy
FROM ghcr.io/astral-sh/uv:0.11.28 AS uv

FROM python:3.13-slim-bookworm
ARG S6_OVERLAY_VERSION=3.2.0.2
ARG TARGETARCH

RUN apt-get update \
 && apt-get install -y --no-install-recommends git openssh-client curl ca-certificates xz-utils tzdata \
 && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    case "${TARGETARCH:-amd64}" in amd64) arch=x86_64 ;; arm64) arch=aarch64 ;; *) echo "unsupported arch: $TARGETARCH"; exit 1 ;; esac; \
    base="https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}"; \
    curl -fsSL -o /tmp/noarch.tar.xz "$base/s6-overlay-noarch.tar.xz"; \
    curl -fsSL -o /tmp/arch.tar.xz "$base/s6-overlay-${arch}.tar.xz"; \
    tar -C / -Jxpf /tmp/noarch.tar.xz; tar -C / -Jxpf /tmp/arch.tar.xz; rm /tmp/*.tar.xz

ARG SUPERCRONIC_VERSION=0.2.33
RUN set -eux; \
    curl -fsSL -o /usr/local/bin/supercronic \
      "https://github.com/aptible/supercronic/releases/download/v${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH:-amd64}"; \
    chmod +x /usr/local/bin/supercronic

COPY --from=caddy /usr/bin/caddy /usr/local/bin/caddy
COPY --from=uv /uv /usr/local/bin/uv

RUN useradd -m -u 1000 -s /bin/bash cortex \
 && mkdir -p /data && chown cortex:cortex /data /run

USER cortex
ENV PATH=/home/cortex/.local/bin:$PATH
COPY --chown=cortex:cortex docker/basic-memory-constraints.txt /tmp/bm-constraints.txt
RUN uv tool install --python /usr/local/bin/python3 --prerelease=allow \
      --constraints /tmp/bm-constraints.txt basic-memory==0.23.0
RUN curl -fsSL https://claude.ai/install.sh | bash

USER root
COPY pyproject.toml README.md /app/
COPY src /app/src
RUN uv pip install --system --no-cache /app
COPY docker/Caddyfile /etc/caddy/Caddyfile
COPY docker/s6-rc.d /etc/s6-overlay/s6-rc.d

ENV CORTEX_DATA=/data \
    S6_KEEP_ENV=1 \
    S6_BEHAVIOUR_IF_STAGE2_FAILS=2 \
    S6_CMD_WAIT_FOR_SERVICES_MAXTIME=0 \
    S6_VERBOSITY=1

USER cortex
WORKDIR /home/cortex
VOLUME /data
EXPOSE 8700
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
  CMD curl -fsS -o /dev/null http://127.0.0.1:8700/setup/login || exit 1
ENTRYPOINT ["/init"]
