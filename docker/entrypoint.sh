#!/usr/bin/env bash
# docker/entrypoint.sh
#
# Generic entrypoint for every ANetBBS service EXCEPT web (which needs
# entrypoint-web.sh's extra docker.sock GID handling). Runs as root
# (the image's default), verifies the shared-DB config isn't missing,
# then drops to the fixed anetbbs UID (1000) before exec'ing the real
# command.
set -euo pipefail

# ANETBBS_DB_URL must be set explicitly and identically across every
# container -- finger_server.py/binkp_server.py/ftp/server.py don't
# check it the way deploy/serve.py and anetbbs/main.py do, so a missing
# value here could let a container silently fall back to guessing its
# own DB path from its package location, diverging from the other
# containers sharing the same volume. Fail loudly instead of silently
# drifting.
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

exec gosu anetbbs "$@"
