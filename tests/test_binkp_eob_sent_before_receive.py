"""Regression test for a real live bug: a peer sysop's own binkd log
showed it delivering files to our inbound BinkP listener successfully,
then sitting in total silence for ~2 minutes waiting to hear ANYTHING
back from us, before giving up, closing the connection, and marking
the whole transfer failed (0 bytes) even though every file had already
been individually M_GOT-acknowledged -- so it kept resending the same
backlog on every subsequent connection.

Root cause: anetbbs/echomail/binkp_server.py's _handle_connection()
used to fully drain the peer's inbound stream (_receive_files(),
waiting up to 120s for the PEER's own M_EOB) BEFORE ever sending our
own outbound mail or our own M_EOB. If the peer's own mailer was
itself waiting to hear from us first (a common real-world pattern --
nothing in BinkP requires either side to speak first), neither side
would say anything until someone's timeout fired.

Fix: send our own outbound mail (if any) and our own M_EOB immediately
after authentication, BEFORE waiting on the peer's stream at all. A
second consequence of moving the send phase earlier: a peer may
interleave its own file offers while we're still mid-send waiting for
its M_GOT on ours -- previously those frames were logged and silently
dropped by _send_pkt_file's wait loop. Fixed by having _send_pkt_file
feed unrecognized frames through the same _consume_inbound_file_frame
helper _receive_files() uses (mirrors the already-proven pattern in
binkp.py's BinkPClient on the outbound-poller side).

Reuses the real-frame session-simulator pattern from
test_binkp_finish_before_import_ordering.py.
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

    def sent_commands(self):
        """Decode every command frame written so far into (cmd, text)."""
        out = []
        i = 0
        buf = b''.join(self.sent)
        while i + 2 <= len(buf):
            word = struct.unpack('>H', buf[i:i+2])[0]
            is_cmd = bool(word & 0x8000)
            length = word & 0x7FFF
            i += 2
            payload = buf[i:i+length]
            i += length
            if is_cmd and payload:
                out.append((payload[0],
                            payload[1:].decode('latin-1', errors='replace')))
        return out


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


class _FakeOutboundMsg:
    """Just enough attributes for _build_ftn_packet() to accept it as
    a queued outbound echomail message."""
    def __init__(self, id):
        self.id = id
        self.area = None
        self.body = 'hi'
        self.from_name = 'Sysop'
        self.to_name = 'All'
        self.subject = 'Test'
        self.tear_line = None
        self.origin_line = None
        self.kludges = None
        self.seenby = None
        self.path = None
        self.chrs = 'CP437 2'
        self.msg_id = None
        self.reply_id = None
        self.from_address = ''
        self.to_address = ''
        self.direction = 'outbound'
        self.network_id = 1
        self.sent_at = None


class _NoOpSession:
    def add(self, *a, **k):
        pass

    def commit(self, *a, **k):
        pass

    def rollback(self, *a, **k):
        pass

    def flush(self, *a, **k):
        pass


def _minimal_fts_packet():
    import struct as _s
    hdr = bytearray(58)
    _s.pack_into('<H', hdr, 18, 2)
    return bytes(hdr)


class EobSentBeforeReceiveTests(unittest.TestCase):
    def test_our_eob_is_sent_before_receive_files_is_called(self):
        """The exact bug: previously our own EOB was only ever sent
        AFTER fully draining the peer's stream. Now it must be sent
        before _receive_files() is even invoked -- verified by
        patching _receive_files and checking the writer already has a
        CMD_EOB frame queued by the time it runs."""
        from anetbbs.echomail import binkp_server as mod
        from anetbbs.models import EchomailNetwork, EchomailMessage, db

        eob_sent_before_receive = []

        real_receive_files = mod._receive_files

        async def _tracking_receive_files(reader, writer, peer, state, files,
                                          transcript=None):
            sent_cmds = writer.sent_commands()
            eob_sent_before_receive.append(
                any(cmd == mod.CMD_EOB for cmd, _ in sent_cmds))
            return await real_receive_files(reader, writer, peer, state, files,
                                            transcript=transcript)

        network = _FakeEchomailNetwork(
            id=1, hub_address='1:200/100', network_type='binkp',
            binkp_password='secret', our_address='1:1/1')

        # Peer never sends its own M_EOB at all -- exactly the reported
        # scenario (it went silent after delivering its files). Old
        # code would have blocked here for up to 120s; new code must
        # not need anything from the peer to send our own EOB.
        frames = [
            (True, _cmd_payload(mod.CMD_ADR, '1:200/100')),
            (True, _cmd_payload(mod.CMD_PWD, 'secret')),
        ]

        EchomailNetwork.query = _FakeQuery([network])
        EchomailMessage.query = _FakeQuery([])
        try:
            with patch.object(EchomailNetwork, 'hub_address', _FakeColumn('hub_address')), \
                 patch.object(EchomailMessage, 'network_id', _FakeColumn('network_id')), \
                 patch.object(EchomailMessage, 'direction', _FakeColumn('direction')), \
                 patch.object(EchomailMessage, 'sent_at', _FakeColumn('sent_at')), \
                 patch.object(db, 'init_app', lambda app: None), \
                 patch.object(db, 'session', _NoOpSession()), \
                 patch.object(mod, '_receive_files', _tracking_receive_files):
                writer = _FakeWriter()
                reader = _ScriptedReader(frames)
                asyncio.run(mod._handle_connection(reader, writer, '1:1/1', 'ANetBBS'))
        finally:
            del EchomailNetwork.query
            del EchomailMessage.query

        self.assertEqual(eob_sent_before_receive, [True],
                         'our own M_EOB must be sent BEFORE _receive_files() '
                         'is called -- a peer waiting to hear from us first '
                         'must not be kept in total silence')
        self.assertTrue(writer.closed)

    def test_interleaved_peer_file_during_our_send_is_captured(self):
        """While we're mid-send waiting for the peer's M_GOT on OUR
        file, the peer starts delivering ITS OWN file. Must not be
        silently dropped."""
        from anetbbs.echomail import binkp_server as mod
        from anetbbs.models import EchomailNetwork, EchomailMessage, db

        imported = []

        def _tracking_import_pkt_payload(pkt_bytes, network_id, filename, peer_address=None):
            imported.append(filename)
            return 0

        network = _FakeEchomailNetwork(
            id=1, hub_address='1:200/100', network_type='binkp',
            binkp_password='secret', our_address='1:1/1')
        outbound_msg = _FakeOutboundMsg(id=1)

        their_pkt = _minimal_fts_packet()
        frames = [
            (True,  _cmd_payload(mod.CMD_ADR, '1:200/100')),
            (True,  _cmd_payload(mod.CMD_PWD, 'secret')),
            # Peer interleaves its own file WHILE we're still waiting
            # for M_GOT on the file we just sent it.
            (True,  _cmd_payload(mod.CMD_FILE, f'theirs.pkt {len(their_pkt)} 0 0')),
            (False, their_pkt),
            # THEN it finally acks ours.
            (True,  _cmd_payload(mod.CMD_GOT, 'x')),
            # And sends its own M_EOB promptly (it heard from us first).
            (True,  _cmd_payload(mod.CMD_EOB)),
        ]

        EchomailNetwork.query = _FakeQuery([network])
        EchomailMessage.query = _FakeQuery([outbound_msg])
        try:
            with patch.object(EchomailNetwork, 'hub_address', _FakeColumn('hub_address')), \
                 patch.object(EchomailMessage, 'network_id', _FakeColumn('network_id')), \
                 patch.object(EchomailMessage, 'direction', _FakeColumn('direction')), \
                 patch.object(EchomailMessage, 'sent_at', _FakeColumn('sent_at')), \
                 patch.object(db, 'init_app', lambda app: None), \
                 patch.object(db, 'session', _NoOpSession()), \
                 patch.object(mod, '_import_pkt_payload', _tracking_import_pkt_payload):
                writer = _FakeWriter()
                reader = _ScriptedReader(frames)
                asyncio.run(mod._handle_connection(reader, writer, '1:1/1', 'ANetBBS'))
        finally:
            del EchomailNetwork.query
            del EchomailMessage.query

        self.assertEqual(imported, ['theirs.pkt'],
                         'a file the peer interleaves while we are still '
                         'waiting for our own M_GOT must still be received '
                         'and imported, not silently dropped')


if __name__ == '__main__':
    unittest.main()
