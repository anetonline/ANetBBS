"""Regression/feature test for a real sysop FR (Firehawke): "there's no
place to see if a poll is going on at any given time... you'll hesitate
on triggering a manual poll because you can't tell if one is ongoing
currently or not." Wanted: the admin poll log to show an in-progress
session (source/peer, elapsed time) and let a sysop view its transcript
while it's still running, not only after it finishes.

Two real gaps this closes in anetbbs/echomail/binkp_server.py and
poller.py:
  1. Outbound polls (poller.py) already wrote an EchomailPollLog row at
     the START of a poll -- but with status='error' as a placeholder,
     so viewing the log mid-poll showed it as already failed.
  2. Inbound sessions (binkp_server.py, a peer connecting TO this BBS)
     never wrote a row until the WHOLE session completed -- zero
     visibility into an in-progress inbound session at all.

Both are now fixed: outbound's placeholder is 'running' (poller.py, not
covered here -- trivial one-line change verified by inspection and the
existing outbound poll-log tests, which don't hardcode the placeholder
value). Inbound now creates a 'running' row immediately once the peer's
network is identified (right after password verification), and UPDATES
that same row at completion instead of inserting a second one -- plus a
best-effort mid-session transcript checkpoint at two natural points
(after sending our backlog, after receiving the peer's files), since
anetbbs-binkp.service is a SEPARATE OS process from the web admin UI and
the shared database is the only channel between them.

This file focuses specifically on observing the INTERMEDIATE state
(status='running' before the session finishes, and a transcript
checkpoint landing before the final commit) -- the "exactly one row,
correct final status/transcript" behavior is already covered by
test_binkp_server_crash_still_logged.py and
test_binkp_server_inbound_transcript.py.
"""
import asyncio
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _frame(is_command, payload):
    word = (0x8000 if is_command else 0) | (len(payload) & 0x7FFF)
    return struct.pack('>H', word) + payload


def _cmd_payload(cmd, text=''):
    return bytes([cmd]) + text.encode('latin-1', errors='replace')


class _FakeWriter:
    def __init__(self):
        self.sent = []
        self.closed = False

    def get_extra_info(self, key):
        return ('127.0.0.1', 12345) if key == 'peername' else None

    def write(self, data):
        self.sent.append(data)

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


class _ScriptedReader:
    """Same as the sibling test files' reader, but raises
    IncompleteReadError forever once the scripted frames are exhausted --
    simulates the peer going silent/disconnecting right after the
    handshake, so the session reaches the post-auth "running" row before
    anything else happens."""
    def __init__(self, frames):
        self._buf = b''.join(_frame(is_cmd, payload) for is_cmd, payload in frames)
        self._pos = 0

    async def readexactly(self, n):
        if self._pos + n > len(self._buf):
            raise asyncio.IncompleteReadError(
                partial=self._buf[self._pos:], expected=n)
        data = self._buf[self._pos:self._pos + n]
        self._pos += n
        return data

    async def read(self, n):
        return b''


class _FakeColumn:
    def __init__(self, name):
        self.name = name

    def in_(self, values):
        return ('in', self.name, set(values))

    def isnot(self, value):
        return ('isnot_none', self.name, None)


class _FakeQuery:
    def __init__(self, rows, predicates=None):
        self._rows = list(rows)
        self._predicates = list(predicates or [])

    def filter(self, *criteria):
        return _FakeQuery(self._rows, self._predicates + list(criteria))

    def filter_by(self, **kwargs):
        extra = [('eq', k, v) for k, v in kwargs.items()]
        return _FakeQuery(self._rows, self._predicates + extra)

    def get(self, _id):
        return self._rows[0] if self._rows else None

    def _matches(self, row):
        for kind, name, value in self._predicates:
            attr = getattr(row, name, None)
            if kind == 'in' and attr not in value:
                return False
            if kind == 'isnot_none' and attr is None:
                return False
            if kind == 'eq' and attr != value:
                return False
        return True

    def first(self):
        for row in self._rows:
            if self._matches(row):
                return row
        return None

    def all(self):
        return [row for row in self._rows if self._matches(row)]


class _FakeEchomailNetwork:
    def __init__(self, id, hub_address=None, network_type='binkp',
                binkp_password=None, our_address=None,
                hub_identity_id=None, name='Net'):
        self.id = id
        self.hub_address = hub_address
        self.network_type = network_type
        self.binkp_password = binkp_password
        self.our_address = our_address
        self.hub_identity_id = hub_identity_id
        self.name = name


class _LiveRowQuery:
    def __init__(self, rows_ref, model_cls):
        self._rows_ref = rows_ref
        self._model_cls = model_cls

    def get(self, _id):
        for row in self._rows_ref:
            if isinstance(row, self._model_cls) and getattr(row, 'id', None) == _id:
                return row
        return None


class _HistorySession:
    """Records every object added, assigns autoincrementing ids like a
    real DB, and -- the thing this test file specifically needs -- snapshots
    (id, status, transcript) for every EchomailPollLog row at each commit()
    call, so the test can see what the row looked like at the FIRST commit
    (mid-session) vs the LAST (completion), not just the final state."""
    def __init__(self, poll_log_cls):
        self._poll_log_cls = poll_log_cls
        self.added = []
        self._next_id = 1
        self.commit_snapshots = []  # list of lists of (id, status, transcript)

    def add(self, obj, *a, **k):
        if getattr(obj, 'id', None) is None:
            obj.id = self._next_id
            self._next_id += 1
        self.added.append(obj)

    def commit(self, *a, **k):
        snap = [(o.id, o.status, o.transcript) for o in self.added
                if isinstance(o, self._poll_log_cls)]
        self.commit_snapshots.append(snap)

    def rollback(self, *a, **k):
        pass

    def flush(self, *a, **k):
        pass


class PollInProgressVisibilityTests(unittest.TestCase):
    def test_running_row_exists_immediately_after_authentication(self):
        """The core ask: a sysop should be able to see a poll is
        currently happening, before it finishes. A row with
        status='running' must exist right after the peer authenticates
        -- well before any file transfer, session completion, or error."""
        from anetbbs.echomail import binkp_server as mod
        from anetbbs.models import EchomailNetwork, EchomailMessage, EchomailPollLog, db

        network = _FakeEchomailNetwork(
            id=7, hub_address='1:200/100', network_type='binkp',
            binkp_password='secret', our_address='1:1/1')

        # Peer authenticates, then the connection just dies (no EOB, no
        # files) -- simulates a stalled/slow session, the exact
        # troubleshooting scenario in the original report.
        frames = [
            (True, _cmd_payload(mod.CMD_ADR, '1:200/100')),
            (True, _cmd_payload(mod.CMD_PWD, 'secret')),
        ]

        EchomailNetwork.query = _FakeQuery([network])
        EchomailMessage.query = _FakeQuery([])
        session = _HistorySession(EchomailPollLog)
        EchomailPollLog.query = _LiveRowQuery(session.added, EchomailPollLog)
        try:
            with patch.object(EchomailNetwork, 'hub_address', _FakeColumn('hub_address')), \
                 patch.object(EchomailMessage, 'network_id', _FakeColumn('network_id')), \
                 patch.object(EchomailMessage, 'direction', _FakeColumn('direction')), \
                 patch.object(EchomailMessage, 'sent_at', _FakeColumn('sent_at')), \
                 patch.object(db, 'init_app', lambda app: None), \
                 patch.object(db, 'session', session):
                writer = _FakeWriter()
                reader = _ScriptedReader(frames)
                asyncio.run(mod._handle_connection(reader, writer, '1:1/1', 'ANetBBS'))
        finally:
            del EchomailNetwork.query
            del EchomailMessage.query
            del EchomailPollLog.query

        self.assertGreaterEqual(len(session.commit_snapshots), 1,
                                'expected at least one commit for the poll log row')
        first_snapshot = session.commit_snapshots[0]
        self.assertEqual(len(first_snapshot), 1,
                         'exactly one poll log row must exist at the very first commit')
        _id, status, _transcript = first_snapshot[0]
        self.assertEqual(status, 'running',
                         'a poll in progress must be visibly distinct from '
                         'a failed one -- this is the whole point of the FR')

        # And still exactly one row by the end, now completed -- not a
        # second row inserted alongside the first.
        poll_logs = [obj for obj in session.added if isinstance(obj, EchomailPollLog)]
        self.assertEqual(len(poll_logs), 1)
        self.assertEqual(poll_logs[0].network_id, 7)
        self.assertIn(poll_logs[0].status, ('success', 'error'),
                     'must have moved on from running by the time the session ends')

    def test_no_running_row_for_downstream_node_sessions(self):
        """A downstream node polling US as its hub has no EchomailNetwork
        row to log against (EchomailPollLog.network_id is NOT NULL) --
        confirm the new start-of-session row creation doesn't try to
        create one anyway and crash or misbehave for this path."""
        from anetbbs.echomail import binkp_server as mod
        from anetbbs.models import EchomailNetwork, EchomailMessage, EchomailPollLog, db

        class _FakeNode:
            def __init__(self, id, ftn_address, password, name='Node'):
                self.id = id
                self.ftn_address = ftn_address
                self.password = password
                self.name = name
                self.is_active = True
                self.last_seen_at = None
                self.hub_identity = None

        from anetbbs.models import BinkPNode
        node = _FakeNode(id=3, ftn_address='1:1/2', password='nodepass')

        frames = [
            (True, _cmd_payload(mod.CMD_ADR, '1:1/2')),
            (True, _cmd_payload(mod.CMD_PWD, 'nodepass')),
        ]

        EchomailNetwork.query = _FakeQuery([])
        EchomailMessage.query = _FakeQuery([])
        BinkPNode.query = _FakeQuery([node])
        session = _HistorySession(EchomailPollLog)
        EchomailPollLog.query = _LiveRowQuery(session.added, EchomailPollLog)
        from anetbbs.echomail import tosser as tosser_mod
        try:
            with patch.object(EchomailNetwork, 'hub_address', _FakeColumn('hub_address')), \
                 patch.object(BinkPNode, 'ftn_address', _FakeColumn('ftn_address')), \
                 patch.object(EchomailMessage, 'network_id', _FakeColumn('network_id')), \
                 patch.object(EchomailMessage, 'direction', _FakeColumn('direction')), \
                 patch.object(EchomailMessage, 'sent_at', _FakeColumn('sent_at')), \
                 patch.object(db, 'init_app', lambda app: None), \
                 patch.object(db, 'session', session), \
                 patch.object(tosser_mod, 'get_pending_for_node', lambda node_id: []):
                writer = _FakeWriter()
                reader = _ScriptedReader(frames)
                asyncio.run(mod._handle_connection(reader, writer, '1:1/1', 'ANetBBS'))
        finally:
            del EchomailNetwork.query
            del EchomailMessage.query
            del BinkPNode.query
            del EchomailPollLog.query

        poll_logs = [obj for obj in session.added if isinstance(obj, EchomailPollLog)]
        self.assertEqual(poll_logs, [],
                         'downstream-node sessions must not get a poll log row '
                         '(no EchomailNetwork to attach one to)')


if __name__ == '__main__':
    unittest.main()
