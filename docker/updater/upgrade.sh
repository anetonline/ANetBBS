#!/usr/bin/env bash
# docker/updater/upgrade.sh
#
# Run inside the ephemeral, detached "updater" container that
# anetbbs/web/control_docker.py's spawn_container_upgrade() launches.
# Pulls the requested image tag and recreates the affected compose
# services. Delegates all container-recreation complexity to Compose's
# own reconciliation logic rather than reimplementing it via the SDK --
# see docs/22-containers.md for why.
#
# Runs with /project bind-mounted (read-only) to the sysop's own
# docker-compose.yml/.env directory (see ANETBBS_COMPOSE_DIR) and
# /var/run/docker.sock mounted so `docker compose` here can talk to
# the same daemon the caller does.
set -euo pipefail

: "${ANETBBS_IMAGE_TAG:?ANETBBS_IMAGE_TAG must be set}"

# ANETBBS_IMAGE_TAG is already in this container's environment (set by
# spawn_container_upgrade()'s `environment=` param) -- compose picks it
# up automatically for the ${ANETBBS_IMAGE_TAG:-latest} substitution in
# docker-compose.yml's x-anetbbs-image anchor.
cd /project
echo "[updater] pulling tag ${ANETBBS_IMAGE_TAG} ..."
docker compose -f docker-compose.yml --env-file .env pull

echo "[updater] recreating services with the new image ..."
docker compose -f docker-compose.yml --env-file .env up -d --remove-orphans

echo "[updater] done."
