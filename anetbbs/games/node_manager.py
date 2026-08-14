# anetbbs/games/node_manager.py
"""
Thread-safe node allocation and release for the ANetBBS Game Center.

Prevents exceeding max_nodes per game and cleans up stale sessions.
"""
import threading
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_lock = threading.Lock()
# _active[(game_id, node_number)] = session_id
_active = {}


def _active_node_numbers_from_db(game_id):
    """Real gap found in a security/performance audit: allocate_node()
    below only ever checked THIS PROCESS's own in-memory _active dict,
    but ANetBBS runs at least two separate OS processes that both call
    it -- anetbbs-web.service for browser-launched doors
    (web/games.py), anetbbs.service for terminal-launched doors
    (games/door_runner.py) -- each with its own independent copy of
    _active. A door configured with max_nodes=1 (e.g. to protect a
    single heavy DOSBox instance) could have one active session from
    each process at once, since neither process's in-memory count ever
    saw the other's allocation -- the sysop-configured cap was a per-
    protocol-surface limit in practice, not the global one it was
    presented as. GameSession IS genuinely shared (one real SQLite file
    every process writes to), so consulting it here closes the gap for
    the realistic case.

    Not a full distributed lock: two allocations landing at the exact
    same instant in different processes could still both briefly
    believe a slot is free before either commits its own GameSession
    row (SQLite's own write-serialization means only one of the two
    subsequent inserts actually lands as 'active', but the loser's
    already-returned node number was still handed out to a caller).
    Meaningfully narrower than the previous "completely unenforced
    across processes" gap, and closing it fully would need either a
    cross-process advisory lock or folding allocation into the same
    transaction as the GameSession insert at every call site -- a much
    larger refactor than warranted for a Medium-severity race that
    only bites two near-simultaneous launches across DIFFERENT
    processes for the SAME game.
    """
    try:
        from ..models import GameSession
        rows = GameSession.query.filter_by(
            game_id=game_id, status='active').all()
        return {r.node_number for r in rows}
    except Exception:
        # No Flask app context available, or DB unreachable -- fall
        # back to in-process-only tracking rather than raising out of
        # a resource-allocation call site most callers don't wrap in
        # try/except.
        logger.debug('Could not check GameSession rows for game %d; '
                     'falling back to in-process tracking only', game_id)
        return set()


def allocate_node(game_id, max_nodes, session_id):
    """
    Allocate a free node number for a game session.

    Args:
        game_id: Integer game ID
        max_nodes: Maximum simultaneous players allowed
        session_id: The GameSession.id being started

    Returns:
        Allocated node number (1-based) or None if all nodes are full
    """
    with _lock:
        used = {node for (gid, node), sid in _active.items() if gid == game_id}
        used |= _active_node_numbers_from_db(game_id)
        for node in range(1, max_nodes + 1):
            if node not in used:
                _active[(game_id, node)] = session_id
                logger.debug('Allocated node %d for game %d (session %d)',
                             node, game_id, session_id)
                return node
        return None


def release_node(game_id, node_number):
    """
    Release a previously allocated node.

    Args:
        game_id: Integer game ID
        node_number: Node number to release
    """
    with _lock:
        key = (game_id, node_number)
        if key in _active:
            del _active[key]
            logger.debug('Released node %d for game %d', node_number, game_id)


def active_node_count(game_id):
    """Return the number of currently allocated nodes for a game."""
    with _lock:
        return sum(1 for (gid, _) in _active if gid == game_id)


def cleanup_stale_sessions(timeout_seconds=3600):
    """
    Remove stale sessions from the in-memory table.
    Should be called periodically or before allocation to prune dead processes.

    Args:
        timeout_seconds: Sessions older than this are considered stale
    """
    from ..models import db, GameSession
    cutoff = datetime.utcnow() - timedelta(seconds=timeout_seconds)
    try:
        stale = GameSession.query.filter(
            GameSession.status == 'active',
            GameSession.started_at < cutoff
        ).all()
        for session in stale:
            session.status = 'timeout'
            session.ended_at = datetime.utcnow()
            release_node(session.game_id, session.node_number)
        if stale:
            db.session.commit()
            logger.info('Cleaned up %d stale game sessions', len(stale))
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning('Error cleaning up stale sessions: %s', exc)
