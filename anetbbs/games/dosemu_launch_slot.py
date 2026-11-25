"""Serialize dosemu door launches of one game across nodes (audit N-9).

A dosemu door finds its drop file (DOOR.SYS etc.) in the game's shared
directory (H:). Each launch writes its own copy there just before the door
reads it, so two callers starting the same game at the same moment could
each get the other's drop file: the wrong user name, access level and time.

A per-game slot is taken before launch and held for HOLD_SECONDS, long
enough for dosemu to boot and the door to read its drop file. A second
launch of the same game waits (without blocking the event loop) until the
slot frees. Other games are unaffected. The slot is an flock on a file in
the temp root, so the terminal server and the web process share it, and a
crashed holder releases it automatically.
"""
import asyncio
import fcntl
import logging
import os
import re
import threading
import time

logger = logging.getLogger(__name__)

HOLD_SECONDS = 15
WAIT_SECONDS = 45
_POLL = 0.25


def _lock_path(game):
    from .node_paths import temp_root
    slug = re.sub(r'[^A-Za-z0-9_.-]', '_', getattr(game, 'slug', '') or 'door')
    os.makedirs(temp_root(), exist_ok=True)
    return os.path.join(temp_root(), f'dosemu-launch-{slug}.lock')


def _try_take(fh):
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _release(fh):
    try:
        fcntl.flock(fh, fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        fh.close()


def _open(game):
    try:
        return open(_lock_path(game), 'a')
    except OSError:
        logger.warning('dosemu launch slot unavailable for %s; launching without it',
                       getattr(game, 'slug', '?'))
        return None


async def take_slot_async(game, hold=HOLD_SECONDS, wait=WAIT_SECONDS):
    """Terminal-server path. Returns True when the slot was taken."""
    fh = _open(game)
    if fh is None:
        return False
    deadline = time.monotonic() + wait
    while not _try_take(fh):
        if time.monotonic() > deadline:
            fh.close()
            logger.warning('dosemu launch slot for %s still busy after %ss; launching anyway',
                           getattr(game, 'slug', '?'), wait)
            return False
        await asyncio.sleep(_POLL)
    asyncio.get_running_loop().call_later(hold, _release, fh)
    return True


def take_slot(game, hold=HOLD_SECONDS, wait=WAIT_SECONDS, sleep=time.sleep):
    """Web path (eventlet: time.sleep yields). Returns True when taken."""
    fh = _open(game)
    if fh is None:
        return False
    deadline = time.monotonic() + wait
    while not _try_take(fh):
        if time.monotonic() > deadline:
            fh.close()
            logger.warning('dosemu launch slot for %s still busy after %ss; launching anyway',
                           getattr(game, 'slug', '?'), wait)
            return False
        sleep(_POLL)
    t = threading.Timer(hold, _release, args=(fh,))
    t.daemon = True
    t.start()
    return True
