#!/usr/bin/env bash
# docker/entrypoint-web.sh
#
# Web-service entrypoint. Same ANETBBS_DB_URL check as entrypoint.sh,
# plus: if /var/run/docker.sock is mounted (the sysop opted into
# Docker-socket-backed control-panel restart/upgrade -- see
# docs/22-containers.md's trust-boundary warning), the socket's group
# ownership varies by host (Debian uses GID 999 for "docker", other
# distros differ) and can't be hardcoded into the image. This script
# starts as root just long enough to discover that GID and add the
# anetbbs user to it, then drops to UID 1000 the same way
# entrypoint.sh does for every other service.
set -euo pipefail

if [ -z "${ANETBBS_DB_URL:-}" ]; then
    echo "FATAL: ANETBBS_DB_URL is not set. Every ANetBBS container must" >&2
    echo "set this explicitly to the SAME absolute path (see" >&2
    echo "docs/22-containers.md) so all containers agree on which" >&2
    echo "SQLite file they're sharing." >&2
    exit 1
fi
export DATABASE_URL="${DATABASE_URL:-$ANETBBS_DB_URL}"

mkdir -p /app/data
chown anetbbs:anetbbs /app/data 2>/dev/null || true

if [ -S /var/run/docker.sock ]; then
    SOCK_GID="$(stat -c '%g' /var/run/docker.sock)"
    if ! getent group "$SOCK_GID" >/dev/null 2>&1; then
        groupadd -g "$SOCK_GID" dockerhost
    fi
    SOCK_GROUP="$(getent group "$SOCK_GID" | cut -d: -f1)"
    usermod -aG "$SOCK_GROUP" anetbbs
fi

exec gosu anetbbs "$@"
