# anetbbs/features/multinode.py
"""
Multinode chat — concurrent telnet/SSH/rlogin terminal sessions can
broadcast lines to each other in real time, like Synchronet's
multinode chat / Mystic's interbbs chat.

The registry is process-local: a single anetbbs daemon process holds
all of its terminal sessions in `_NODES` and pushes broadcasts to each
asyncio Queue. Web users don't appear here — they have IRC / MRC /
shoutbox.
"""
import asyncio
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


# Slot index -> NodeEntry. Slot 1..BBS_NODES.
_NODES = {}


class NodeEntry:
    """One active terminal session: which user, which slot, message queue."""

    def __init__(self, slot, user, protocol, peer, session=None):
        self.slot = slot
        self.user = user
        self.username = (user or {}).get('username') or '?'
        self.protocol = protocol
        self.peer = peer
        self.connected_at = datetime.utcnow()
        self.queue = asyncio.Queue()
        # Per-session "in chat" flag — when True we forward broadcasts
        # straight to this user's terminal. Otherwise, broadcasts queue
        # silently and the user sees them when they enter chat.
        self.listening = False
        # Optional reference to the owning BBSSession so admin can
        # kick the user via writer.close(). Stored as a weak-ish ref
        # — multinode doesn't manage the session's lifetime, just
        # has a handle so it can disconnect on demand.
        self.session = session
        # Set by :func:`kick_node` so the session can show a goodbye
        # message before its writer closes (the close itself triggers
        # the disconnect; this just lets us print "you've been kicked"
        # first).
        self.kick_reason = None


def acquire_slot(user, protocol, peer, max_nodes, session=None):
    """Find the first free slot 1..max_nodes and register a NodeEntry there.

    Returns the NodeEntry on success, or None if all slots are taken.

    Pass `session` (the BBSSession) so the sysop's NodeSpy kick action
    has a handle to disconnect this user.
    """
    for slot in range(1, max_nodes + 1):
        if slot not in _NODES:
            entry = NodeEntry(slot, user, protocol, peer, session=session)
            _NODES[slot] = entry
            return entry
    return None


def release_slot(entry):
    if entry is None:
        return
    _NODES.pop(entry.slot, None)


def list_nodes():
    """Return a snapshot list of NodeEntry's, ordered by slot."""
    return [_NODES[s] for s in sorted(_NODES.keys())]


def broadcast(sender_username, text, kind='msg'):
    """Send a chat line to every node except the sender. Best-effort —
    queue is unbounded so this never blocks."""
    payload = {
        'kind': kind,                # 'msg' / 'join' / 'part' / 'sysop'
        'from': sender_username,
        'text': text or '',
        'when': datetime.utcnow().isoformat(),
    }
    for slot, entry in list(_NODES.items()):
        if entry.username == sender_username and kind == 'msg':
            continue
        try:
            entry.queue.put_nowait(payload)
        except asyncio.QueueFull:
            pass


def whisper(target_slot, sender_username, text):
    """Direct private message to a single node. Returns True on hit."""
    entry = _NODES.get(target_slot)
    if not entry:
        return False
    payload = {
        'kind': 'whisper',
        'from': sender_username,
        'text': text or '',
        'when': datetime.utcnow().isoformat(),
    }
    try:
        entry.queue.put_nowait(payload)
        return True
    except asyncio.QueueFull:
        return False


def kick_node(target_slot, sender_username='SYSOP', reason=''):
    """Forcibly disconnect the terminal session at ``target_slot``.

    Best-effort: writes a goodbye message to the user's terminal first
    (so they see why they were kicked) then closes the underlying
    transport, which causes the session's reader to EOF and the session
    to clean up naturally.

    Returns True if the slot was occupied (kick attempted), False if
    nothing was at that slot. Idempotent — calling twice on an already
    kicked slot is a no-op.

    Note: must be called from a thread/coroutine that has access to
    the asyncio loop the session's writer lives on. In practice this
    means calling from a Flask route handler in the same gunicorn
    worker that runs the BBS terminal listener — which is true for
    the bundled deployment (one anetbbs process serves
    telnet+ssh+rlogin+web).
    """
    entry = _NODES.get(target_slot)
    if not entry:
        return False
    entry.kick_reason = reason or 'Disconnected by sysop'

    # Push a 'kick' payload onto the queue — sessions that are listening
    # in chat see this and can react. Best-effort.
    try:
        entry.queue.put_nowait({
            'kind': 'kick',
            'from': sender_username,
            'text': entry.kick_reason,
            'when': datetime.utcnow().isoformat(),
        })
    except asyncio.QueueFull:
        pass

    # Forcibly close the underlying transport. This is what actually
    # disconnects the user — the session's `await reader.read(...)`
    # in its main loop returns EOF, the menu engine drops out, and
    # session cleanup releases the slot.
    sess = entry.session
    if sess is not None:
        # Try to write a goodbye line first. Wrap in try/except so a
        # broken writer doesn't prevent the close.
        msg = (f"\r\n\r\n\x1b[1;31m*** Disconnected by sysop: "
               f"{entry.kick_reason} ***\x1b[0m\r\n")
        try:
            w = getattr(sess, 'writer', None)
            if w is not None:
                # writer.write is sync; drain is async. Skip drain to
                # avoid needing a coroutine context. The kernel buffer
                # carries it for the few ms before close.
                try:
                    w.write(msg.encode('utf-8', errors='replace'))
                except Exception:  # pylint: disable=broad-except
                    pass
                try:
                    w.close()
                except Exception:  # pylint: disable=broad-except
                    pass
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning('kick_node: writer close failed for slot %d: %s',
                           target_slot, exc)
    return True
