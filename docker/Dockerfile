# docker/Dockerfile
#
# One shared image for all 5 ANetBBS service roles (web, terminal,
# MRC bridge, finger, binkp) -- they all use the same requirements.txt/
# package today (5 systemd units all pointing at the same venv), so
# the compose file differentiates purely via `command:`/`entrypoint:`
# per service rather than building 5 near-identical images.
#
# Build from the repo root:
#   docker buildx build --platform linux/amd64,linux/arm64 \
#       -f docker/Dockerfile -t anetbbs:local --load .
#
# syntax=docker/dockerfile:1

# ── Stage 1: build Python dependencies ──────────────────────────────────────
FROM python:3.12-slim-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
        python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY setup.py requirements.txt requirements-docker.txt ./
COPY anetbbs ./anetbbs
COPY mrc ./mrc
RUN pip install --no-cache-dir --prefix=/install -e . \
    && pip install --no-cache-dir --prefix=/install -r requirements-docker.txt

# ── Stage 2: grab the docker CLI + compose plugin (self-upgrade path only) ──
FROM docker:27-cli AS docker-cli

# ── Stage 3: runtime image ──────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS runtime

# lrzsz: ZMODEM for terminal file transfers (file areas, ebook reader
# download). openssh-client: outbound dial-out / BinkP over SSH tunnels.
# curl: HEALTHCHECK target. bash: entrypoint scripts. gosu: privilege
# drop from root (only the web entrypoint needs to start as root, to
# read the mounted docker.sock's GID -- see entrypoint-web.sh). tini:
# PID 1 for the single-container path (correct signal forwarding/zombie
# reaping -- supervisord itself shouldn't be PID 1 directly).
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        bash \
        gosu \
        lrzsz \
        openssh-client \
        libcap2-bin \
        tini \
    && rm -rf /var/lib/apt/lists/*

# supervisord for the single-container "quick start" path only (compose
# mode never uses it -- each container just runs entrypoint.sh + its
# own command directly). Small, pure-Python, not worth a separate image.
RUN pip install --no-cache-dir supervisor

# Fixed UID/GID 1000 -- load-bearing for the shared-SQLite-file design
# (see anetbbs/models.py's WAL-mode hook and docs/22-containers.md):
# every container that touches data/anetbbs.db must run as the SAME
# user, matching the "5 processes, 1 OS user" pattern that already
# makes this safe on a traditional bare-metal install.
RUN groupadd -g 1000 anetbbs \
    && useradd -u 1000 -g anetbbs -m -d /home/anetbbs -s /bin/bash anetbbs

# Ports 79 (finger), 18 (MSP), 11 (SYSTAT) are <1024 and these processes
# run as the non-root anetbbs user. `cap_add: [NET_BIND_SERVICE]` at the
# container level only widens what the container is ALLOWED to grant --
# a non-root process still needs the capability in its OWN effective set,
# which Linux normally strips on setuid(). File capabilities on the
# interpreter binary are what actually let a non-root-invoked python
# process bind a low port, independent of who's running it.
RUN setcap 'cap_net_bind_service=+ep' "$(readlink -f "$(command -v python3)")"

COPY --from=builder /install /usr/local
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-cli /usr/local/libexec/docker/cli-plugins/docker-compose \
     /usr/local/libexec/docker/cli-plugins/docker-compose

WORKDIR /app
COPY . /app
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY docker/entrypoint-web.sh /usr/local/bin/entrypoint-web.sh
# The source tree lives on a VFAT/FAT32 external drive during
# development, which has no concept of Unix executable bits at all --
# every file always reads back as non-executable regardless of chmod
# on the host. COPY preserves whatever the source filesystem reports,
# so every script actually exec'd directly (not via `bash script.sh`)
# needs its +x re-applied explicitly here, not just the two copied
# individually above.
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/entrypoint-web.sh \
                /app/docker/single/entrypoint-single.sh \
                /app/docker/updater/upgrade.sh \
    && mkdir -p /app/data \
    && chown -R anetbbs:anetbbs /app /home/anetbbs

ENV PYTHONUNBUFFERED=1 \
    ANETBBS_RUNTIME=docker-compose

# tini is ALWAYS PID 1 (both single-container and compose modes) for
# correct signal forwarding/zombie reaping -- matters even for a
# single-process compose container, since the terminal service can
# spawn door-game/dial-out subprocesses. Image starts as root by
# default; entrypoint.sh/entrypoint-web.sh are each responsible for
# dropping to UID 1000 via gosu before exec'ing the real command
# (entrypoint-web.sh additionally handles the docker.sock GID dance).
# Compose mode: each service overrides `command:` to
# ["/usr/local/bin/entrypoint.sh", "<its own command>"] (or
# entrypoint-web.sh for the web service). Single-container mode:
# overrides `command:` to ["/usr/local/bin/entrypoint-single.sh"]
# (supervisord manages its own per-process user= drop, no gosu needed).
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/usr/local/bin/entrypoint.sh", "python", "deploy/serve.py"]
