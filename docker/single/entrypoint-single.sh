#!/usr/bin/env bash
# docker/single/entrypoint-single.sh
#
# Single-container "quick start" entrypoint. Unlike entrypoint.sh/
# entrypoint-web.sh (which each drop to the anetbbs user before exec'ing
# ONE service), this stays root the whole time -- supervisord itself
# needs root to be ABLE to setuid() its child processes to the anetbbs
# user (each [program:] stanza in supervisord.conf sets user=anetbbs).
# No docker.sock handling here: single-container restarts go through
# supervisorctl (talking to the local supervisord socket), not the
# Docker API -- see anetbbs/web/control.py's docker-single backend.
set -euo pipefail

if [ -z "${ANETBBS_DB_URL:-}" ]; then
    echo "FATAL: ANETBBS_DB_URL is not set. See docs/22-containers.md." >&2
    exit 1
fi
export DATABASE_URL="${DATABASE_URL:-$ANETBBS_DB_URL}"

mkdir -p /app/data
chown anetbbs:anetbbs /app/data 2>/dev/null || true

exec supervisord -c /app/docker/single/supervisord.conf
