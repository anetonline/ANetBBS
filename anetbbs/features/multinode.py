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


def broadcast(sender_username, text, kind='msg', sender_slot=None):
    """Send a chat line to every node except the sender. Best-effort —
    queue is unbounded so this never blocks.

    Pass `sender_slot` (the sending NodeEntry's own slot number) for
    'msg' broadcasts whenever it's available. Without it, self-
    exclusion falls back to matching by username -- which silently
    drops the message for EVERY node sharing that username, not just
    the sender, whenever the same account is logged in on more than
    one node at once (e.g. testing with the same login on two
    terminals). `sender_slot` identifies the one specific node that
    sent it instead.
    """
    payload = {
        'kind': kind,                # 'msg' / 'join' / 'part' / 'sysop'
        'from': sender_username,
        'text': text or '',
        'when': datetime.utcnow().isoformat(),
    }
    for slot, entry in list(_NODES.items()):
        if kind == 'msg':
            if sender_slot is not None:
                if slot == sender_slot:
                    continue
            elif entry.username == sender_username:
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


async def run_chat_session(session):
    """Interactive multinode chat loop for one terminal session.

    Real-time broadcast to every other node's NodeEntry.queue via
    `broadcast()`, plus /list and /w <slot> <msg>. Shared by both the
    main-menu 'multinode' action and ChatManager's "Local Chat" menu
    option -- those used to be two separate code paths, one fully
    working and one a stub that only ever echoed back to the sender
    (never actually broadcast anywhere), which is why "Local Chat"
    looked like you could only talk to yourself.
    """
    entry = getattr(session, '_node_entry', None)
    if entry is None:
        await session.write("\r\nNo multinode slot - chat unavailable.\r\n")
        return
    entry.listening = True

    async def pump():
        """Forward incoming queue messages to the user's terminal."""
        try:
            while entry.listening:
                msg = await entry.queue.get()
                k = msg.get('kind', 'msg')
                fr = msg.get('from', '?')
                tx = msg.get('text', '')
                if k == 'whisper':
                    line = f'\r\n\x1b[1;35m[whisper from {fr}]\x1b[0m {tx}\r\n'
                elif k == 'join':
                    line = f'\r\n\x1b[1;32m*** {fr} {tx}\x1b[0m\r\n'
                elif k == 'part':
                    line = f'\r\n\x1b[1;33m*** {fr} {tx}\x1b[0m\r\n'
                elif k == 'sysop':
                    line = f'\r\n\x1b[1;31m[sysop] {tx}\x1b[0m\r\n'
                else:
                    line = f'\r\n\x1b[1;36m<{fr}>\x1b[0m {tx}\r\n'
                try:
                    await session.write(line)
                except Exception:
                    return
        except asyncio.CancelledError:
            return

    pump_task = asyncio.create_task(pump())
    await session.write(
        "\r\n\x1b[1;36m=== Local Chat ===\x1b[0m\r\n"
        "Commands: /list  /w <slot> <msg>  /q to quit\r\n\r\n")
    try:
        while True:
            line = await session.read_line('chat> ')
            if line is None:
                break
            line = line.strip()
            if not line:
                continue
            if line == '/q' or line.lower() == '/quit':
                break
            if line == '/list':
                nodes = list_nodes()
                if not nodes:
                    await session.write("\r\n(no other nodes online)\r\n")
                else:
                    await session.write("\r\n")
                    for n in nodes:
                        await session.write(
                            f'  Node {n.slot}: {n.username} '
                            f'({n.protocol}) since '
                            f'{n.connected_at.strftime("%H:%M")}\r\n')
                continue
            if line.startswith('/w '):
                parts = line[3:].split(None, 1)
                if len(parts) == 2 and parts[0].isdigit():
                    target = int(parts[0])
                    text = parts[1]
                    if whisper(target, session.user.get('username', '?'), text):
                        await session.write(
                            f"\x1b[2m(whispered to node {target})\x1b[0m\r\n")
                    else:
                        await session.write(
                            f"\r\nNode {target} not online.\r\n")
                else:
                    await session.write("Usage: /w <slot> <message>\r\n")
                continue
            broadcast(session.user.get('username', '?'), line, kind='msg',
                      sender_slot=entry.slot)
    finally:
        entry.listening = False
        pump_task.cancel()
        try:
            await pump_task
        except (asyncio.CancelledError, Exception):
            pass


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
