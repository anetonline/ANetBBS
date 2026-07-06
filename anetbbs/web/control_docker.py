# anetbbs/web/control_docker.py
"""
Docker-compose backend for the Sysop Control Panel (anetbbs/web/control.py).

Only used when ANETBBS_RUNTIME=docker-compose. Talks to the Docker
Engine API over the socket mounted into the web container
(/var/run/docker.sock) via the `docker` Python SDK -- see
docs/22-containers.md for the security tradeoff this represents
(mounting the socket grants root-equivalent host control to anything
reaching the panel's endpoints).

Containers are resolved via the `com.docker.compose.service` label
(not by guessing a container name from a project-name prefix), which
is robust regardless of Compose v1/v2 naming or a sysop renaming their
project. `docker` is an optional dependency (see requirements-docker.txt)
-- every function here is only ever called when ANETBBS_RUNTIME is
actually docker-compose, so a bare-metal install never needs it
installed at all.
"""
import logging

logger = logging.getLogger(__name__)

# anetbbs/web/control.py's KNOWN_UNITS systemd-unit names -> the
# service name used in docker/compose/docker-compose.yml. Keep this in
# sync with that compose file if service names ever change there.
UNIT_TO_COMPOSE_SERVICE = {
    'anetbbs-web': 'web',
    'anetbbs': 'terminal',
    'anetbbs-mrc-bridge': 'mrc-bridge',
    'anetbbs-finger': 'finger',
    'anetbbs-binkp': 'binkp',
}


def _client():
    import docker
    return docker.from_env()


def _find_container(service):
    client = _client()
    containers = client.containers.list(
        all=True, filters={'label': f'com.docker.compose.service={service}'})
    return containers[0] if containers else None


def unit_state(unit):
    """Same return shape as control.py's _unit_state() for systemd, so
    _enrich_unit() doesn't need to know which backend produced it."""
    state = {'ActiveState': 'unknown', 'SubState': '',
             'ActiveEnterTimestamp': '', 'MainPID': '0',
             'LoadState': 'unknown'}
    service = UNIT_TO_COMPOSE_SERVICE.get(unit)
    if not service:
        return state
    try:
        c = _find_container(service)
        if c is None:
            state['LoadState'] = 'not-found'
            return state
        state['LoadState'] = 'loaded'
        status = c.status  # 'running' / 'exited' / 'paused' / 'restarting' / ...
        state['ActiveState'] = 'active' if status == 'running' else 'inactive'
        state['SubState'] = status
        docker_state = c.attrs.get('State', {}) or {}
        state['ActiveEnterTimestamp'] = docker_state.get('StartedAt', '') or ''
        state['MainPID'] = str(docker_state.get('Pid', 0) or 0)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning('control_docker: unit_state(%s) failed: %s', unit, exc)
    return state


def change_state(unit, action):
    """Returns (ok, out, err) matching control.py's _systemctl_change()
    contract, so service_action() doesn't need to branch on backend."""
    service = UNIT_TO_COMPOSE_SERVICE.get(unit)
    if not service:
        return False, '', f'no compose service mapped for unit {unit}'
    try:
        c = _find_container(service)
        if c is None:
            return False, '', f'no container found for compose service {service!r} -- is it running?'
        if action in ('restart', 'reload'):
            c.restart(timeout=10)
        elif action == 'start':
            c.start()
        elif action == 'stop':
            c.stop(timeout=10)
        else:
            return False, '', f'unsupported action: {action}'
        return True, f'{action} {service}: OK', ''
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning('control_docker: change_state(%s, %s) failed: %s',
                       unit, action, exc)
        return False, '', str(exc)


def read_logs(unit, lines=200):
    """Returns log text, matching control.py's _read_journal() contract."""
    service = UNIT_TO_COMPOSE_SERVICE.get(unit)
    if not service:
        return f'No compose service mapped for unit {unit}'
    try:
        c = _find_container(service)
        if c is None:
            return f'No container found for compose service {service!r} -- is it running?'
        raw = c.logs(tail=lines, timestamps=True)
        return raw.decode('utf-8', errors='replace') or '(empty)'
    except Exception as exc:  # pylint: disable=broad-except
        return f'Could not read container logs: {exc}'


def spawn_container_upgrade(image_tag):
    """Launch a detached, ephemeral sibling container to run
    docker compose pull + up -d against the sysop's own project files.
    Runs from the SAME image already running (which bundles the docker
    CLI + compose plugin, see ../../docker/Dockerfile), so it survives
    the web container itself being recreated mid-upgrade -- the
    container analogue of today's `systemd-run --scope` cgroup-escape
    trick for bare-metal self-upgrades (see anetbbs/web/upgrades.py).

    Docker's bind-mount source must be a HOST path, not a path as seen
    from inside the calling (web) container -- the web container's own
    /project mount doesn't tell it what the HOST-side source path was.
    ANETBBS_COMPOSE_DIR must be set (in .env) to the absolute host
    directory containing docker-compose.yml/.env, so this can bind-mount
    the SAME host files into the new sibling container.
    """
    import os
    import time
    host_dir = os.environ.get('ANETBBS_COMPOSE_DIR')
    if not host_dir:
        raise RuntimeError(
            'ANETBBS_COMPOSE_DIR is not set -- required so the updater '
            'container can bind-mount the same docker-compose.yml/.env '
            'directory from the HOST (set it in .env to the absolute '
            'path containing your docker-compose.yml)')
    client = _client()
    self_container = _find_container('web')
    image = self_container.image if self_container else None
    if image is None:
        raise RuntimeError('could not determine the currently running image')
    name = f'anetbbs-updater-{int(time.time())}'
    client.containers.run(
        image=image,
        entrypoint=['/usr/local/bin/entrypoint.sh'],
        command=['bash', '/app/docker/updater/upgrade.sh'],
        environment={'ANETBBS_IMAGE_TAG': image_tag},
        volumes={
            '/var/run/docker.sock': {'bind': '/var/run/docker.sock', 'mode': 'rw'},
            host_dir: {'bind': '/project', 'mode': 'ro'},
        },
        working_dir='/project',
        detach=True,
        remove=True,
        name=name,
    )
    return name
