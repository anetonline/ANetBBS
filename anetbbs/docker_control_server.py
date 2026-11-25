"""Privileged Docker control service for docker-compose installs.

Runs as its own compose service (``control``), the ONLY container with
/var/run/docker.sock mounted. The web container talks to it over a unix
socket on a shared volume and can ask for exactly four things (audit
N-3):

* ``state`` / ``logs`` for a known ANetBBS compose service,
* ``change`` -- start/stop/restart/reload a known service,
* ``upgrade`` -- pull + recreate the project at a validated version tag
  (image repository fixed by the read-only compose file).

A compromised web process therefore gets those operations, not the whole
Docker API (which is root on the host).
"""
import json
import logging
import os
import re
import socket
import threading

logger = logging.getLogger(__name__)

SOCKET_PATH = os.environ.get('ANETBBS_CONTROL_SOCKET', '/run/anetbbs-control/control.sock')
_TAG_RE = re.compile(r'v\d+\.\d+(?:\.\d+|[ab]\d+\.\d+)')
_ACTIONS = ('start', 'stop', 'restart', 'reload')

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
    if not _TAG_RE.fullmatch(image_tag or ''):
        raise ValueError(f'invalid image tag: {image_tag!r}')
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


# --------------------------------------------------------------------------
# Socket server
# --------------------------------------------------------------------------

def handle_request(req):
    """Validate one request dict and run it. Returns a JSON-able dict."""
    if not isinstance(req, dict):
        return {'ok': False, 'error': 'bad request'}
    op = req.get('op')
    unit = req.get('unit')
    if op in ('state', 'logs', 'change') and unit not in UNIT_TO_COMPOSE_SERVICE:
        return {'ok': False, 'error': f'unknown unit {unit!r}'}
    if op == 'state':
        return {'ok': True, 'state': unit_state(unit)}
    if op == 'logs':
        try:
            lines = max(1, min(int(req.get('lines', 200)), 2000))
        except (TypeError, ValueError):
            return {'ok': False, 'error': 'bad lines'}
        return {'ok': True, 'text': read_logs(unit, lines)}
    if op == 'change':
        action = req.get('action')
        if action not in _ACTIONS:
            return {'ok': False, 'error': f'unsupported action {action!r}'}
        ok, out, err = change_state(unit, action)
        return {'ok': ok, 'out': out, 'err': err}
    if op == 'upgrade':
        tag = req.get('tag')
        if not isinstance(tag, str) or not _TAG_RE.fullmatch(tag):
            return {'ok': False, 'error': 'invalid tag'}
        try:
            return {'ok': True, 'name': spawn_container_upgrade(tag)}
        except Exception as exc:  # pylint: disable=broad-except
            return {'ok': False, 'error': str(exc)}
    return {'ok': False, 'error': f'unknown op {op!r}'}


def _serve_conn(conn):
    with conn:
        conn.settimeout(10)
        buf = b''
        while not buf.endswith(b'\n') and len(buf) < 8192:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
        try:
            req = json.loads(buf.decode('utf-8', errors='replace') or 'null')
        except ValueError:
            req = None
        resp = handle_request(req)
        conn.sendall((json.dumps(resp) + '\n').encode())


def serve(path=SOCKET_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    os.chmod(path, 0o660)
    srv.listen(8)
    logger.info('docker control service listening on %s', path)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=_serve_conn, args=(conn,), daemon=True).start()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    serve()
