"""Regression test for a real gap found while investigating a sysop's
report: "there still are no poll transcripts for incoming, only
outgoing". Confirmed directly -- outbound polls (poller.py dialing OUT,
via binkp.py's BinkPClient) have saved a frame-by-frame session
transcript to EchomailPollLog.transcript since v1.0b2.47. The inbound
listener (binkp_server.py, a peer connecting TO this BBS) never did,
on either the fix-shipping side or before it -- there was no
_log_transcript equivalent anywhere in that file at all.

This matters more than it might first appear: inbound sessions are the
exact direction this whole session's BinkP audit was chasing (a peer
hub pushing mail TO this BBS, then stalling/disconnecting) -- and
unlike an outbound poll, a sysop can't just "run it again" to capture
a transcript after the fact for an inbound session, since it's the
REMOTE peer's own scheduler that decides when to reconnect.

Fix: binkp_server.py now has its own _log_transcript() (mirroring
BinkPClient._log_transcript in binkp.py) threaded through every
frame-send/frame-receive call in _handle_connection/_receive_files/
_send_pkt_file/_finish_session, and the resulting transcript is saved
to EchomailPollLog.transcript for upstream-hub inbound sessions (the
same rows that already got a poll log entry -- see
test_binkp_finish_before_import_ordering.py's own docstring for why
that pre-existing code path exists) using the same
_format_transcript() truncation helper poller.py already uses for
outbound polls.
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


class _CapturingSession:
    """Like the _NoOpSession used elsewhere, but actually remembers what
    got add()ed so the test can inspect the EchomailPollLog row.

    add() also assigns an autoincrementing .id, mirroring a real DB --
    binkp_server.py's poll-log-update-not-insert logic (added alongside
    the poll-in-progress feature) captures a new row's .id right after
    creating it, then looks it back up later via
    EchomailPollLog.query.get(that_id) to update it in place instead of
    inserting a second row."""
    def __init__(self):
        self.added = []
        self._next_id = 1

    def add(self, obj):
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
    """Backed by a live reference to a growing list (session.added)
    rather than a snapshot copy, so a row added earlier in the test is
    findable via .get(id) later -- the way a real SQLAlchemy session's
    identity map would find it."""
    def __init__(self, rows_ref, model_cls):
        self._rows_ref = rows_ref
        self._model_cls = model_cls

    def get(self, _id):
        for row in self._rows_ref:
            if isinstance(row, self._model_cls) and getattr(row, 'id', None) == _id:
                return row
        return None


def _minimal_fts_packet():
    hdr = bytearray(58)
    struct.pack_into('<H', hdr, 18, 2)
    return bytes(hdr)


class InboundTranscriptTests(unittest.TestCase):
    def test_inbound_session_saves_a_frame_by_frame_transcript(self):
        from anetbbs.echomail import binkp_server as mod
        from anetbbs.models import EchomailNetwork, EchomailMessage, EchomailPollLog, HatchQueue, db

        network = _FakeEchomailNetwork(
            id=1, hub_address='1:200/100', network_type='binkp',
            binkp_password='secret', our_address='1:1/1')

        pkt_bytes = _minimal_fts_packet()
        frames = [
            (True,  _cmd_payload(mod.CMD_ADR, '1:200/100')),
            (True,  _cmd_payload(mod.CMD_PWD, 'secret')),
            (True,  _cmd_payload(mod.CMD_FILE, f'test.pkt {len(pkt_bytes)} 0 0')),
            (False, pkt_bytes),
            (True,  _cmd_payload(mod.CMD_EOB)),
        ]

        EchomailNetwork.query = _FakeQuery([network])
        EchomailMessage.query = _FakeQuery([])
        session = _CapturingSession()
        EchomailPollLog.query = _LiveRowQuery(session.added, EchomailPollLog)
        HatchQueue.query = _FakeQuery([])
        try:
            with patch.object(EchomailNetwork, 'hub_address', _FakeColumn('hub_address')), \
                 patch.object(EchomailMessage, 'network_id', _FakeColumn('network_id')), \
                 patch.object(EchomailMessage, 'direction', _FakeColumn('direction')), \
                 patch.object(EchomailMessage, 'sent_at', _FakeColumn('sent_at')), \
                 patch.object(db, 'init_app', lambda app: None), \
                 patch.object(db, 'session', session), \
                 patch('anetbbs.echomail.tosser.get_pending_netmail_for_network', lambda network_id: []), \
                 patch.object(mod, '_import_pkt_payload', lambda *a, **k: 0):
                writer = _FakeWriter()
                reader = _ScriptedReader(frames)
                asyncio.run(mod._handle_connection(reader, writer, '1:1/1', 'ANetBBS'))
        finally:
            del EchomailNetwork.query
            del EchomailMessage.query
            del EchomailPollLog.query
            del HatchQueue.query

        log_rows = [obj for obj in session.added if isinstance(obj, EchomailPollLog)]
        self.assertEqual(len(log_rows), 1, 'expected exactly one poll log row')
        log = log_rows[0]

        self.assertIsNotNone(
            log.transcript,
            'inbound session must save a transcript, same as outbound polls do')
        # Sanity-check it actually looks like a real frame-by-frame log,
        # not just a placeholder -- both directions should be present.
        self.assertIn('>> CMD', log.transcript, 'must record frames WE sent')
        self.assertIn('<< CMD', log.transcript, 'must record frames the peer sent')
        self.assertIn('ADR', log.transcript)
        self.assertIn('FILE', log.transcript)
        self.assertIn('EOB', log.transcript)
        # Each line should be timestamped HH:MM:SS.mmm, matching
        # binkp.py's own BinkPClient._log_transcript format for
        # consistency in the admin UI's transcript viewer.
        import re
        first_line = log.transcript.split('\n')[0]
        self.assertRegex(first_line, r'^\d{2}:\d{2}:\d{2}\.\d{3} ')


if __name__ == '__main__':
    unittest.main()
