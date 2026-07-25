"""Logon / Logoff module runner.

Queries active LoginModule rows for the given event_type, checks security
level, respects fast-logon skip, and dispatches each module in sort_order.

Supported module_type values:
  wall        — graffiti wall (params: none)
  ansi        — display an ANSI screen slot (params: {"slot": "welcome"})
  shell       — run a shell command (params: {"command": "/path/script.sh"})
  door_native — run a native Linux door binary (params: {"path": "...", "args": "..."})
  door_python — import and call a Python door (params: {"module": "pkg.mod", "func": "run"})
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

logger = logging.getLogger(__name__)


async def run_modules(session, event_type: str, fast_logon: bool = False) -> None:
    """Run all active login/logoff modules for *event_type*.

    *fast_logon* — when True, skip modules marked skip_on_fast_logon=True.
    """
    try:
        from ..features.bbs_ui import _app
        from ..models import LoginModule

        user = session.user or {}
        user_level = int(user.get('access_level', 10))
        is_admin = bool(user.get('is_admin'))

        app = _app()
        with app.app_context():
            mods = (LoginModule.query
                    .filter_by(is_active=True, event_type=event_type)
                    .order_by(LoginModule.sort_order, LoginModule.id)
                    .all())
            # Detach from session before async work
            mod_list = [
                {
                    'id': m.id,
                    'name': m.name,
                    'module_type': m.module_type,
                    'params_json': m.params_json or '{}',
                    'min_access_level': m.min_access_level or 0,
                    'skip_on_fast_logon': bool(m.skip_on_fast_logon),
                }
                for m in mods
            ]

        for mod in mod_list:
            if fast_logon and mod['skip_on_fast_logon']:
                continue
            if not is_admin and user_level < mod['min_access_level']:
                continue
            try:
                params = json.loads(mod['params_json'])
            except Exception:
                params = {}
            try:
                await _dispatch(session, mod['module_type'], params)
            except Exception as exc:
                logger.warning('login module %d (%s) failed: %s',
                               mod['id'], mod['module_type'], exc)
    except Exception as exc:
        logger.warning('run_modules(%s) error: %s', event_type, exc)


async def _dispatch(session, module_type: str, params: dict) -> None:
    if module_type == 'wall':
        from .wall import show_wall
        await show_wall(session, allow_post=True)

    elif module_type == 'lastcallers':
        from .lastcallers import show_last_callers
        await show_last_callers(session)

    elif module_type == 'ansi':
        slot = params.get('slot', '')
        if slot:
            await session._show_ansi_screen(slot)

    elif module_type == 'shell':
        await _run_shell(session, params)

    elif module_type == 'door_native':
        await _run_door_native(session, params)

    elif module_type == 'door_python':
        await _run_door_python(session, params)


async def _run_shell(session, params: dict) -> None:
    cmd = params.get('command', '').strip()
    if not cmd:
        return
    user = session.user or {}
    env = {**os.environ,
           'BBS_USERNAME': user.get('username', ''),
           'BBS_NODE': str(getattr(getattr(session, '_node_entry', None), 'slot', 1))}
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        if stdout:
            session.writer.write(stdout.replace(b'\n', b'\r\n'))
            await session.writer.drain()
    except asyncio.TimeoutError:
        logger.warning('login module shell cmd timed out: %s', cmd)
    except Exception as exc:
        logger.warning('login module shell error: %s', exc)


async def _run_door_native(session, params: dict) -> None:
    path = params.get('path', '').strip()
    if not path or not os.path.isfile(path):
        return
    args = params.get('args', '')
    cmd = f'{path} {args}'.strip()
    await _run_shell(session, {'command': cmd})


async def _run_door_python(session, params: dict) -> None:
    module_path = params.get('module', '').strip()
    func_name = params.get('func', 'run')
    if not module_path:
        return
    try:
        import importlib
        mod = importlib.import_module(module_path)
        fn = getattr(mod, func_name)
        result = fn(session)
        if asyncio.iscoroutine(result):
            await result
    except Exception as exc:
        logger.warning('login module door_python %s: %s', module_path, exc)
