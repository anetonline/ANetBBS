"""Serialize dosemu door launches of one game across nodes (audit N-9).

A dosemu door finds its drop file (DOOR.SYS etc.) in the game's shared
directory (H:). Each launch writes its own copy there just before the door
reads it, so two callers starting the same game at the same moment could
each get the other's drop file: the wrong user name, access level and time.

A per-game slot is taken before launch and held for HOLD_SECONDS, long
enough for dosemu to boot and the door to read its drop file. A second
launch of the same game waits (without blocking the event loop) until the
slot frees. Other games are unaffected. The slot is an expiry time in a
file in the temp root, updated under a short flock, so the terminal server
and the web process share it and a crashed holder can't wedge it (an
expiry implausibly far in the future, e.g. after a clock jump, is ignored).
"""
import asyncio
import fcntl
import logging
import os
import re
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


def _claim(game, hold):
    """One atomic attempt: under a short flock, take the slot if its
    recorded expiry has passed, and record a new one. No lock or timer
    outlives this call, so a crashed holder can't wedge the slot."""
    try:
        fd = os.open(_lock_path(game), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        logger.warning('dosemu launch slot unavailable for %s; launching without it',
                       getattr(game, 'slug', '?'))
        return True
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            until = float(os.pread(fd, 64, 0).decode() or 0)
        except ValueError:
            until = 0.0
        now = time.time()
        if now < until and until - now <= hold + WAIT_SECONDS:
            return False
        os.ftruncate(fd, 0)
        os.pwrite(fd, repr(now + hold).encode(), 0)
        return True
    finally:
        os.close(fd)        # releases the flock


async def take_slot_async(game, hold=HOLD_SECONDS, wait=WAIT_SECONDS):
    """Terminal-server path. Returns True when the slot was taken."""
    deadline = time.monotonic() + wait
    while not _claim(game, hold):
        if time.monotonic() > deadline:
            logger.warning('dosemu launch slot for %s still busy after %ss; launching anyway',
                           getattr(game, 'slug', '?'), wait)
            return False
        await asyncio.sleep(_POLL)
    return True


def take_slot(game, hold=HOLD_SECONDS, wait=WAIT_SECONDS, sleep=time.sleep):
    """Web path (eventlet: time.sleep yields). Returns True when taken."""
    deadline = time.monotonic() + wait
    while not _claim(game, hold):
        if time.monotonic() > deadline:
            logger.warning('dosemu launch slot for %s still busy after %ss; launching anyway',
                           getattr(game, 'slug', '?'), wait)
            return False
        sleep(_POLL)
    return True
