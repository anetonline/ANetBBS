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

# Real gap found live: unlike the data/ volume above, single-container
# mode had no documented mount for the MRC bridge's own config.json --
# mrc/bridge/main.py's _find_config_path() silently falls back to
# config.example.json (placeholder "My BBS" / blank sysop name) if
# nothing is mounted, with no warning anywhere. This is non-fatal (the
# bridge still runs, just under the wrong name) so it doesn't belong
# next to the ANETBBS_DB_URL check above, but a sysop deserves a loud
# hint in the logs rather than silently wrong branding forever.
if [ ! -f /app/mrc/bridge/config.json ]; then
    echo "WARNING: /app/mrc/bridge/config.json is not mounted -- the MRC" >&2
    echo "  chat bridge will advertise the built-in example BBS name and" >&2
    echo "  a blank sysop name instead of your real settings. See the" >&2
    echo "  single-container quick start in docs/22-containers.md for the" >&2
    echo "  -v mount to add." >&2
fi

exec supervisord -c /app/docker/single/supervisord.conf
