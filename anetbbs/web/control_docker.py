"""Docker-compose backend for the Sysop Control Panel and container
self-upgrade (only used when ANETBBS_RUNTIME=docker-compose).

The web container does NOT have the Docker socket (audit N-3). It asks
the separate ``control`` service (anetbbs/docker_control_server.py) over
a unix socket on a shared volume, which accepts only state/logs/
start-stop-restart of known services and upgrade-to-a-validated-tag.
Function names and return shapes are unchanged for control.py/upgrades.py.
"""
import json
import os
import socket

from ..docker_control_server import UNIT_TO_COMPOSE_SERVICE

__all__ = ['UNIT_TO_COMPOSE_SERVICE', 'unit_state', 'change_state', 'read_logs',
           'spawn_container_upgrade']

SOCKET_PATH = os.environ.get('ANETBBS_CONTROL_SOCKET', '/run/anetbbs-control/control.sock')


def _call(req, timeout=30):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(SOCKET_PATH)
        s.sendall((json.dumps(req) + '\n').encode())
        buf = b''
        while not buf.endswith(b'\n') and len(buf) < 4 * 1024 * 1024:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf.decode('utf-8', errors='replace') or '{}')
    finally:
        s.close()


def unit_state(unit):
    try:
        r = _call({'op': 'state', 'unit': unit}, timeout=10)
        if r.get('ok'):
            return r['state']
    except (OSError, ValueError):
        pass
    return {'ActiveState': 'unknown', 'SubState': 'control service unreachable',
            'ActiveEnterTimestamp': '', 'MainPID': '0', 'LoadState': 'unknown'}


def change_state(unit, action):
    try:
        r = _call({'op': 'change', 'unit': unit, 'action': action})
    except (OSError, ValueError) as exc:
        return False, '', f'control service unreachable: {exc}'
    return bool(r.get('ok')), r.get('out', ''), r.get('err') or r.get('error', '')


def read_logs(unit, lines=200):
    try:
        r = _call({'op': 'logs', 'unit': unit, 'lines': lines})
    except (OSError, ValueError) as exc:
        return f'Control service unreachable: {exc}'
    return r.get('text') if r.get('ok') else f"Could not read logs: {r.get('error')}"


def spawn_container_upgrade(image_tag):
    r = _call({'op': 'upgrade', 'tag': image_tag}, timeout=60)
    if not r.get('ok'):
        raise RuntimeError(r.get('error') or 'upgrade request refused')
    return r.get('name', '')
