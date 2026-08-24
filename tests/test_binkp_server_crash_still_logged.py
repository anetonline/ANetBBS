"""Regression test for a real gap found while investigating a live peer
report (Nicholas Boel / pharcyde.org's own binkd log): his mailer showed
ANetBBS's inbound listener closing the connection ~60 seconds into a
session, mid-transfer, with none of his files ever M_GOT-acknowledged --
but there was no corresponding ANetBBS-side transcript to diagnose it
from, because inbound sessions only ever wrote an EchomailPollLog row on
the SUCCESS path: _import_and_log() (the last statement in
_handle_connection) was the ONLY code that ever created one. Any
exception before that point -- in _send_pkt_file, _receive_files,
_finish_session, or the import itself -- meant the whole session
vanished with zero trace in the sysop-facing Poll Log, only a bare stack
trace in the application log.

Fixed by wrapping the post-authentication session in a try/except that,
on ANY exception, still writes an EchomailPollLog row (status='error')
with whatever frame-by-frame transcript was captured up to the failure
point -- mirroring poller.py's _do_poll(), which has always done this
for outbound polls via its own try/except/finally.

Reuses the real-frame session-simulator pattern from
test_binkp_eob_sent_before_receive.py.
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
    """Feeds a scripted sequence of real BinkP frames, then raises
    IncompleteReadError forever once exhausted."""
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

    def order_by(self, *args, **kwargs):
        return self

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


class _RecordingSession:
    """Records every object passed to add(); commit/rollback/flush are
    no-ops, matching _NoOpSession elsewhere but with add() observable.

    add() also assigns an autoincrementing .id to whatever's passed in,
    if it doesn't already have one -- mirrors a real DB's real behavior
    (the row's primary key becomes available once added+committed), which
    binkp_server.py's poll-log-update-not-insert logic (added alongside
    the poll-in-progress feature) now depends on: it captures a new row's
    .id as a plain scalar right after creating it, then looks that row
    back up later via EchomailPollLog.query.get(that_id) to update it in
    place instead of inserting a second row."""
    def __init__(self):
        self.added = []
        self._next_id = 1

    def add(self, obj, *a, **k):
        if getattr(obj, 'id', None) is None:
            obj.id = self._next_id
            self._next_id += 1
        self.added.append(obj)

    def commit(self, *a, **k):
        pass

    def rollback(self, *a, **k):
        pass

    def flush(self, *a, **k):
        pass


class _LiveRowQuery:
    """Like _FakeQuery, but backed by a live reference to a growing list
    (session.added) rather than a snapshot copy -- needed so a row added
    earlier in the same test is actually findable via .get(id) later,
    the way a real SQLAlchemy session's identity map would find it."""
    def __init__(self, rows_ref, model_cls):
        self._rows_ref = rows_ref
        self._model_cls = model_cls

    def get(self, _id):
        for row in self._rows_ref:
            if isinstance(row, self._model_cls) and getattr(row, 'id', None) == _id:
                return row
        return None


class ServerCrashStillLoggedTests(unittest.TestCase):
    def test_exception_during_receive_files_still_writes_error_poll_log(self):
        """The exact real-world shape: authentication succeeds, we send
        our own EOB, then something raises while draining the peer's
        inbound stream -- an EchomailPollLog row (status='error') with
        the captured transcript must still be written, not silently
        lost."""
        from anetbbs.echomail import binkp_server as mod
        from anetbbs.models import EchomailNetwork, EchomailMessage, EchomailPollLog, HatchQueue, FreqRequest, db

        async def _raising_receive_files(reader, writer, peer, state, files,
                                         transcript=None):
            raise RuntimeError('simulated mid-session crash')

        network = _FakeEchomailNetwork(
            id=1, hub_address='1:200/100', network_type='binkp',
            binkp_password='secret', our_address='1:1/1')

        frames = [
            (True, _cmd_payload(mod.CMD_ADR, '1:200/100')),
            (True, _cmd_payload(mod.CMD_PWD, 'secret')),
        ]

        session = _RecordingSession()
        EchomailNetwork.query = _FakeQuery([network])
        EchomailMessage.query = _FakeQuery([])
        EchomailPollLog.query = _LiveRowQuery(session.added, EchomailPollLog)
        HatchQueue.query = _FakeQuery([])
        FreqRequest.query = _FakeQuery([])
        try:
            with patch.object(EchomailNetwork, 'hub_address', _FakeColumn('hub_address')), \
                 patch.object(EchomailMessage, 'network_id', _FakeColumn('network_id')), \
                 patch.object(EchomailMessage, 'direction', _FakeColumn('direction')), \
                 patch.object(EchomailMessage, 'sent_at', _FakeColumn('sent_at')), \
                 patch.object(db, 'init_app', lambda app: None), \
                 patch.object(db, 'session', session), \
                 patch('anetbbs.echomail.tosser.get_pending_netmail_for_network', lambda network_id, include_hold=False: []), \
                 patch.object(mod, '_receive_files', _raising_receive_files):
                writer = _FakeWriter()
                reader = _ScriptedReader(frames)
                with self.assertRaises(RuntimeError):
                    asyncio.run(mod._handle_connection(reader, writer, '1:1/1', 'ANetBBS'))
        finally:
            del EchomailNetwork.query
            del EchomailMessage.query
            del EchomailPollLog.query
            del HatchQueue.query
            del FreqRequest.query

        poll_logs = [obj for obj in session.added if isinstance(obj, EchomailPollLog)]
        self.assertEqual(len(poll_logs), 1,
                         'exactly one EchomailPollLog row must be written '
                         'even though the session crashed')
        log = poll_logs[0]
        self.assertEqual(log.status, 'error')
        self.assertEqual(log.network_id, 1)
        self.assertIn('RuntimeError', log.error_message)
        self.assertIsNotNone(log.transcript)
        # The handshake frames sent before the crash must be present --
        # this is the whole point: a sysop diagnosing the failure needs
        # to see what happened on the wire up to the failure point.
        self.assertIn('CMD OK', log.transcript)

    def test_successful_session_still_logs_normally(self):
        """Baseline / guard against a too-broad fix: a session that
        completes without error must still go through the existing
        success-path logging in _import_and_log(), not the new
        error-path one."""
        from anetbbs.echomail import binkp_server as mod
        from anetbbs.models import EchomailNetwork, EchomailMessage, EchomailPollLog, HatchQueue, FreqRequest, db

        network = _FakeEchomailNetwork(
            id=1, hub_address='1:200/100', network_type='binkp',
            binkp_password='secret', our_address='1:1/1')

        # Peer never sends its own M_EOB -- exactly the shape already
        # covered by test_binkp_eob_sent_before_receive.py's clean-close
        # case, reused here just to confirm the success path is untouched.
        frames = [
            (True, _cmd_payload(mod.CMD_ADR, '1:200/100')),
            (True, _cmd_payload(mod.CMD_PWD, 'secret')),
        ]

        session = _RecordingSession()
        EchomailNetwork.query = _FakeQuery([network])
        EchomailMessage.query = _FakeQuery([])
        EchomailPollLog.query = _LiveRowQuery(session.added, EchomailPollLog)
        HatchQueue.query = _FakeQuery([])
        FreqRequest.query = _FakeQuery([])
        try:
            with patch.object(EchomailNetwork, 'hub_address', _FakeColumn('hub_address')), \
                 patch.object(EchomailMessage, 'network_id', _FakeColumn('network_id')), \
                 patch.object(EchomailMessage, 'direction', _FakeColumn('direction')), \
                 patch.object(EchomailMessage, 'sent_at', _FakeColumn('sent_at')), \
                 patch.object(db, 'init_app', lambda app: None), \
                 patch.object(db, 'session', session), \
                 patch('anetbbs.echomail.tosser.get_pending_netmail_for_network', lambda network_id, include_hold=False: []):
                writer = _FakeWriter()
                reader = _ScriptedReader(frames)
                asyncio.run(mod._handle_connection(reader, writer, '1:1/1', 'ANetBBS'))
        finally:
            del EchomailNetwork.query
            del EchomailMessage.query
            del EchomailPollLog.query
            del HatchQueue.query
            del FreqRequest.query

        poll_logs = [obj for obj in session.added if isinstance(obj, EchomailPollLog)]
        self.assertEqual(len(poll_logs), 1)
        self.assertEqual(poll_logs[0].status, 'success')


if __name__ == '__main__':
    unittest.main()
