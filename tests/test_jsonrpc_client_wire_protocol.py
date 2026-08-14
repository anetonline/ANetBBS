"""Tests for anetbbs/games/jsonrpc_client.py -- the pure-Python client
for Synchronet's real JSON-RPC interbbs protocol (port 10088 by
convention). Part of the Synchronet JSON-RPC interbbs integration
kickoff: this client is what ANetBBS's json-client.js replacement shim
(anetbbs/games/sbbs_stubs/json-client.js) shells out to for the actual
wire protocol, since Node has no synchronous TCP read primitive for
doors to use directly.

Packet shapes and default wait-for-response behavior are verified
against the module's own docstring table, which was transcribed
directly from the real upstream json-client.js
(anetbbs/games/sbbs_reference/json-client.js, pulled from
github.com/SynchronetBBS/sbbs) -- not guessed. A throwaway in-process
TCP server (not a mock) exercises the real wire encoding/decoding
end-to-end for the round-trip tests, matching this project's usual
preference for a real socket over a mocked one when the wire format
itself is what's under test.
"""
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.games.jsonrpc_client import (
    JSONRPCClient, JSONRPCError, run_one_call, run_listen_session,
    _LineReader,
    LOCK_READ, LOCK_WRITE, LOCK_UNLOCK,
)


class _RecordingServer:
    """A minimal real TCP server that records every packet it receives
    and can be scripted to send canned responses. Speaks the real
    newline-delimited JSON wire format directly, no protocol library
    involved on this side either -- keeps the test honest about what's
    actually on the wire.

    Accepts connections in a loop (one handler thread per connection)
    since JSONRPCClient deliberately opens a fresh connection per call
    (no persistent session) -- a test that makes several calls in
    sequence needs the server to keep accepting, not just service one
    connection and stop."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(5)
        self.host, self.port = self.sock.getsockname()
        self.received = []
        self.connections = []  # every still-open connection, accept order
        self._lock = threading.Lock()
        # Keyed by (func, oper) -- e.g. ('QUERY', 'READ') or ('QUERY', None)
        # for a plain-func match. A FIFO queue would let a fire-and-forget
        # connection that never reads a reply (e.g. WRITE, opened on its
        # own connection right alongside a READ the test also queued a
        # response for) accidentally consume a response meant for a
        # different, later connection -- keying by the actual operation
        # keeps each queued response paired with the call it belongs to.
        self._responses = {}
        self._closing = False
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

    def queue_response(self, packet, func='QUERY', oper=None):
        with self._lock:
            self._responses.setdefault((func, oper), []).append(packet)

    def queue_raw_response(self, raw_bytes, func='QUERY', oper=None):
        """Like queue_response(), but for a pre-encoded raw byte string
        instead of a dict -- needed to put a genuinely invalid-UTF-8
        byte on the wire, which json.dumps(...).encode('utf-8') could
        never produce (it always emits valid UTF-8 by construction)."""
        with self._lock:
            self._responses.setdefault((func, oper), []).append(raw_bytes)

    def wait_for(self, count, timeout=2.0):
        """Block until at least `count` packets have been received, or
        raise AssertionError on timeout -- deterministic replacement
        for a blind sleep() when asserting on a fire-and-forget call
        that the client doesn't wait for a response to."""
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if len(self.received) >= count:
                    return
            time.sleep(0.01)
        raise AssertionError(
            f'timed out waiting for {count} packet(s), got {len(self.received)}')

    def _accept_loop(self):
        while not self._closing:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with self._lock:
                self.connections.append(conn)
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        f = conn.makefile('r', encoding='utf-8', newline='\n')
        try:
            while True:
                line = f.readline()
                if not line:
                    break
                pkt = json.loads(line.strip())
                with self._lock:
                    self.received.append(pkt)
                    key = (pkt.get('func'), pkt.get('oper'))
                    # Pop and send the WHOLE queued list for this one
                    # incoming request, not just one item -- lets a test
                    # script e.g. a PING followed by the real RESPONSE
                    # for a single call, matching how a real server can
                    # interleave keepalive packets before the actual
                    # answer.
                    bucket = self._responses.pop(key, [])
                for resp in bucket:
                    if isinstance(resp, bytes):
                        conn.sendall(resp)
                    else:
                        conn.sendall((json.dumps(resp) + '\r\n').encode('utf-8'))
        except OSError:
            pass
        finally:
            with self._lock:
                try:
                    self.connections.remove(conn)
                except ValueError:
                    pass
            try:
                conn.close()
            except OSError:
                pass

    def wait_for_connection(self, timeout=2.0):
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self.connections:
                    return self.connections[-1]
            time.sleep(0.01)
        raise AssertionError('no connection arrived within timeout')

    def push(self, packet):
        """Sends `packet` unprompted on the most recent connection --
        for testing real subscribe()-driven server pushes, distinct
        from queue_response() (which only replies to a matching
        incoming request)."""
        conn = self.wait_for_connection()
        conn.sendall((json.dumps(packet) + '\r\n').encode('utf-8'))

    def close(self):
        self._closing = True
        try:
            self.sock.close()
        except OSError:
            pass


class PacketShapeTests(unittest.TestCase):
    """Confirms every JSONRPCClient method sends exactly the packet
    shape the real client sends -- same field names, same func/oper
    values, same default-wait behavior. Uses the real client against a
    real (if throwaway) TCP server, not introspection of the method
    bodies, so a refactor that changes the wire output would actually
    be caught here."""

    def setUp(self):
        self.server = _RecordingServer()
        self.addCleanup(self.server.close)

    def _client(self):
        return JSONRPCClient(self.server.host, self.server.port,
                             connect_timeout=2, recv_timeout=2)

    def test_ident_sends_md5_of_uppercased_password_and_does_not_wait(self):
        with self._client() as c:
            c.ident('MYSCOPE', 'jerry', 'hunter2')
        self.server.wait_for(1)
        pkt = self.server.received[0]
        self.assertEqual(pkt['scope'], 'MYSCOPE')
        self.assertEqual(pkt['func'], 'IDENT')
        self.assertEqual(pkt['username'], 'jerry')
        import hashlib
        expected = hashlib.md5('HUNTER2'.encode('utf-8')).hexdigest()  # nosec B324 -- matches real Synchronet protocol scheme, verified test value
        self.assertEqual(pkt['pw'], expected)
        self.assertNotIn('oper', pkt, 'IDENT has no oper field in the real protocol')

    def test_read_sends_query_read_and_waits_for_response(self):
        self.server.queue_response({'func': 'RESPONSE', 'data': {'score': 99}}, oper='READ')
        with self._client() as c:
            result = c.read('SCOPE', 'SCORES.HIGH', LOCK_READ)
        self.assertEqual(result, {'score': 99})
        pkt = self.server.received[0]
        self.assertEqual(pkt['func'], 'QUERY')
        self.assertEqual(pkt['oper'], 'READ')
        self.assertEqual(pkt['location'], 'SCORES.HIGH')
        self.assertEqual(pkt['lock'], LOCK_READ)

    def test_write_does_not_wait_by_default_matching_real_client(self):
        """Real bug class this guards against: the real client's write()
        only calls wait() when TIMEOUT>=0, which defaults to -1 (never).
        A client that waited anyway would hang here since the server
        deliberately sends nothing back for WRITE."""
        with self._client() as c:
            result = c.write('SCOPE', 'SCORES.LATEST', {'score': 1}, LOCK_WRITE)
        self.assertIsNone(result)
        self.server.wait_for(1)
        pkt = self.server.received[0]
        self.assertEqual(pkt['oper'], 'WRITE')
        self.assertEqual(pkt['data'], {'score': 1})
        self.assertEqual(pkt['lock'], LOCK_WRITE)

    def test_raw_sends_the_bare_packet_unchanged_and_waits_for_a_response(self):
        """Real gap found live bundling Thirstyville: the door's own
        `jsonClient.send({scope:"ADMIN", func:"TIME"});
        jsonClient.wait();` sends a bare packet with no scope/location/
        oper QUERY structure at all -- none of the higher-level
        methods above cover this shape. Confirms raw() sends exactly
        the caller-supplied packet (no extra fields injected beyond
        what JSONRPCClient always adds elsewhere) and unconditionally
        waits for the response, matching the real client's own
        send()/wait() pairing."""
        self.server.queue_response({'func': 'TIME', 'data': 1785847211}, func='TIME', oper=None)
        with self._client() as c:
            result = c.raw({'scope': 'ADMIN', 'func': 'TIME'})
        self.assertEqual(result, 1785847211)
        pkt = self.server.received[0]
        self.assertEqual(pkt['scope'], 'ADMIN')
        self.assertEqual(pkt['func'], 'TIME')
        self.assertNotIn('oper', pkt)

    def test_push_pop_shift_unshift_use_correct_opers(self):
        # push/unshift don't wait by default (separate, unordered
        # connections); pop/shift always do -- assert opers as a
        # multiset, not arrival order, for the same reason as the
        # lock/unlock test above. Response queue only needs entries
        # for pop/shift since push/unshift never read a reply.
        self.server.queue_response({'func': 'RESPONSE', 'data': 'popped'}, oper='POP')
        self.server.queue_response({'func': 'RESPONSE', 'data': 'shifted'}, oper='SHIFT')
        with self._client() as c:
            c.push('S', 'L', 'x', LOCK_WRITE)
            c.unshift('S', 'L', 'y', LOCK_WRITE)
            popped = c.pop('S', 'L', LOCK_WRITE)
            shifted = c.shift('S', 'L', LOCK_WRITE)
        self.server.wait_for(4)
        opers = sorted(p['oper'] for p in self.server.received)
        self.assertEqual(opers, sorted(['PUSH', 'UNSHIFT', 'POP', 'SHIFT']))
        self.assertEqual(popped, 'popped')
        self.assertEqual(shifted, 'shifted')

    def test_lock_and_unlock_send_correct_lock_values(self):
        # lock()/unlock() are fire-and-forget (separate connections,
        # no response awaited) -- assert on content, not arrival
        # order, since two independent connections opened back-to-back
        # aren't guaranteed to be handled in send order by the server.
        with self._client() as c:
            c.lock('S', 'L', LOCK_WRITE)
            c.unlock('S', 'L')
        self.server.wait_for(2)
        opers = {p['oper'] for p in self.server.received}
        self.assertEqual(opers, {'LOCK'})
        lock_values = sorted(p['data'] for p in self.server.received)
        self.assertEqual(lock_values, sorted([LOCK_WRITE, LOCK_UNLOCK]))

    def test_remove_sends_delete_oper(self):
        with self._client() as c:
            c.remove('S', 'L', LOCK_WRITE)
        self.server.wait_for(1)
        self.assertEqual(self.server.received[0]['oper'], 'DELETE')

    def test_subscribe_includes_nick_and_system_when_provided(self):
        with self._client() as c:
            c.subscribe('S', 'L', nick='StingRay', system_name='ANetBBS')
        self.server.wait_for(1)
        pkt = self.server.received[0]
        self.assertEqual(pkt['oper'], 'SUBSCRIBE')
        self.assertEqual(pkt['nick'], 'StingRay')
        self.assertEqual(pkt['system'], 'ANetBBS')

    def test_who_and_status_always_wait(self):
        self.server.queue_response({'func': 'RESPONSE', 'data': ['1.2.3.4']}, oper='WHO')
        self.server.queue_response({'func': 'RESPONSE', 'data': {'locked': False}}, oper='STATUS')
        with self._client() as c:
            who = c.who('S', 'L')
            status = c.status('S', 'L')
        self.assertEqual(who, ['1.2.3.4'])
        self.assertEqual(status, {'locked': False})

    def test_slice_sends_start_end_in_data(self):
        self.server.queue_response({'func': 'RESPONSE', 'data': [1, 2]}, oper='SLICE')
        with self._client() as c:
            c.slice('S', 'L', 0, 2, LOCK_READ)
        pkt = self.server.received[0]
        self.assertEqual(pkt['data'], {'start': 0, 'end': 2})

    def test_slice_start_and_end_are_optional(self):
        """Real reproduction of a crash found auditing Synchronetris:
        json-chat.js's JSONChat.join() calls client.slice() without an
        explicit `end` -- confirmed against the real server source
        (json-db.js) that its slice handler is a plain
        `array.slice(request.data.start, request.data.end)`, and JS's
        own native Array.slice already treats an omitted end (or
        start) as "through the end of the array" -- both are genuinely
        optional there, not a caller bug. run_one_call() previously
        required both via a[key] lookups and raised
        "bad arguments: 'end'" (a real KeyError) whenever a caller
        omitted it."""
        self.server.queue_response({'func': 'RESPONSE', 'data': [1, 2, 3]}, oper='SLICE')
        with self._client() as c:
            c.slice('S', 'L', 0)
        pkt = self.server.received[0]
        self.assertEqual(pkt['data'], {'start': 0, 'end': None})

    def test_splice_sends_start_num_data(self):
        with self._client() as c:
            c.splice('S', 'L', 1, 2, ['x'], LOCK_WRITE)
        self.server.wait_for(1)
        pkt = self.server.received[0]
        self.assertEqual(pkt['data'], {'start': 1, 'num': 2, 'data': ['x']})


class ServerErrorAndPingTests(unittest.TestCase):
    def setUp(self):
        self.server = _RecordingServer()
        self.addCleanup(self.server.close)

    def _client(self):
        return JSONRPCClient(self.server.host, self.server.port,
                             connect_timeout=2, recv_timeout=2)

    def test_error_packet_raises_jsonrpc_error(self):
        self.server.queue_response(
            {'func': 'ERROR', 'data': {'description': 'scope not found'}}, oper='READ')
        with self._client() as c:
            with self.assertRaises(JSONRPCError) as ctx:
                c.read('BADSCOPE', 'X', LOCK_READ)
        self.assertIn('scope not found', str(ctx.exception))

    def test_ping_is_answered_with_pong_transparently(self):
        """Real behavior confirmed from json-sock.js: a PING from the
        server must be answered with a PONG, and neither is handed
        back to the caller as if it were the real response."""
        self.server.queue_response({'func': 'PING', 'data': 12345}, oper='READ')
        self.server.queue_response({'func': 'RESPONSE', 'data': 'actual answer'}, oper='READ')
        with self._client() as c:
            result = c.read('S', 'L', LOCK_READ)
        self.assertEqual(result, 'actual answer')
        # Two packets in: the READ query, then our PONG reply to the PING.
        # The PONG is sent by the client right after read() returns (as
        # part of the same connection, before it's closed), so the
        # server's own recording of it can still lag the test's own
        # assertions without this -- same race class as the
        # fire-and-forget ops above.
        self.server.wait_for(2)
        self.assertEqual(len(self.server.received), 2)
        self.assertEqual(self.server.received[1]['func'], 'PONG')
        self.assertEqual(self.server.received[1]['data'], 12345)


class InvalidUtf8OnTheWireTests(unittest.TestCase):
    """Real crash found live bundling Star Trek (a Synchronet JSON-RPC
    door): reading a real scoreboard scope from Jerry's real live
    server threw "bad arguments: 'utf-8' codec can't decode byte 0x81
    in position 1732: invalid start byte" -- some free-text field in
    real historical scoreboard data (a player alias, ship name, etc)
    contains a raw extended-ASCII/CP437 byte, not valid UTF-8.
    connect()'s socket.makefile() was decoding in strict mode (the
    default), so ANY invalid byte anywhere on the wire crashed the
    ENTIRE line read, not just corrupted that one field. Confirms the
    fix (errors='replace') survives an invalid byte and still returns
    the rest of the payload intact, matching the leniency already used
    by the OTHER read path in this file (the subscribe() daemon)."""

    def setUp(self):
        self.server = _RecordingServer()
        self.addCleanup(self.server.close)

    def _client(self):
        return JSONRPCClient(self.server.host, self.server.port,
                             connect_timeout=2, recv_timeout=2)

    def test_read_survives_an_invalid_utf8_byte_inside_a_string_field(self):
        # Real 0x81 is not valid UTF-8 on its own (a lone continuation
        # byte) -- exactly the kind of byte a CP437 ship/player name
        # can contain. Hand-built raw packet: a RESPONSE whose data is
        # {"name": "<0x81>OOPS"}.
        raw = b'{"func": "RESPONSE", "data": {"name": "\x81OOPS", "score": 5}}\r\n'
        self.server.queue_raw_response(raw, oper='READ')
        with self._client() as c:
            result = c.read('SCOPE', 'SCORES.HIGH', LOCK_READ)
        self.assertEqual(result['score'], 5)
        self.assertIn('OOPS', result['name'])

    def test_read_of_a_clean_payload_still_works_unaffected(self):
        """Confirms errors='replace' doesn't silently corrupt or
        mangle perfectly valid UTF-8 data -- only kicks in for the
        actually-invalid byte."""
        self.server.queue_response(
            {'func': 'RESPONSE', 'data': {'name': 'Fearchar', 'score': 12}},
            oper='READ')
        with self._client() as c:
            result = c.read('SCOPE', 'SCORES.HIGH', LOCK_READ)
        self.assertEqual(result, {'name': 'Fearchar', 'score': 12})


class UnboundedLineGuardTests(unittest.TestCase):
    """Real gap found in a security/performance audit: _recv_one()'s
    readline() had no max-line-length cap -- a broken or hostile
    JSON-RPC peer sending an infinite line with no '\\n' terminator
    would grow this process's memory unboundedly while readline()
    waited forever for a terminator that never arrives. _MAX_LINE_CHARS
    is patched down to a small value here so the test doesn't need to
    push a real 1MB+ payload over a loopback socket to exercise it."""

    def setUp(self):
        self.server = _RecordingServer()
        self.addCleanup(self.server.close)

    def _client(self):
        return JSONRPCClient(self.server.host, self.server.port,
                             connect_timeout=2, recv_timeout=2)

    def test_line_exceeding_the_cap_with_no_terminator_raises_cleanly(self):
        oversized = b'{"func": "RESPONSE", "data": "' + (b'x' * 200) + b'"'  # deliberately no closing quote/brace/newline
        self.server.queue_raw_response(oversized, oper='READ')
        with patch.object(JSONRPCClient, '_MAX_LINE_CHARS', 50):
            with self._client() as c:
                with self.assertRaises(JSONRPCError) as ctx:
                    c.read('SCOPE', 'L', LOCK_READ)
        self.assertIn('exceeded', str(ctx.exception))

    def test_a_normal_response_under_the_cap_still_works(self):
        """Confirms the cap doesn't break real, well-formed traffic --
        only kicks in for a line that genuinely never terminates."""
        self.server.queue_response(
            {'func': 'RESPONSE', 'data': {'score': 7}}, oper='READ')
        with patch.object(JSONRPCClient, '_MAX_LINE_CHARS', 50):
            with self._client() as c:
                result = c.read('SCOPE', 'L', LOCK_READ)
        self.assertEqual(result, {'score': 7})


class ConnectionFailureTests(unittest.TestCase):
    def test_unreachable_server_raises_cleanly_not_hangs(self):
        client = JSONRPCClient('127.0.0.1', 1, connect_timeout=2)
        with self.assertRaises(OSError):
            client.connect()


class RunOneCallCliContractTests(unittest.TestCase):
    """run_one_call() is what the stdin/stdout CLI entrypoint (and the
    JS shim, indirectly) actually drives -- exercised directly here
    without a subprocess, matching this project's usual split between
    "test the logic directly" and "test the real subprocess/JS
    integration separately" (see test_jsonrpc_client_js_shim.py)."""

    def setUp(self):
        self.server = _RecordingServer()
        self.addCleanup(self.server.close)

    def test_slice_op_works_without_an_end_key_in_the_args_dict(self):
        """Direct reproduction of the real crash via the actual CLI
        contract: a SLICE call whose args dict has no 'end' key at all
        (exactly what json-client.js's shim sends when a door calls
        client.slice(scope, location, start) with no end argument)
        used to raise a real KeyError ("bad arguments: 'end'") inside
        run_one_call() itself, not just JSONRPCClient.slice()'s own
        signature default."""
        self.server.queue_response({'func': 'RESPONSE', 'data': [1, 2]}, oper='SLICE')
        result = run_one_call({
            'host': self.server.host, 'port': self.server.port, 'op': 'SLICE',
            'scope': 'S', 'location': 'L', 'start': 0,
        })
        self.assertTrue(result['ok'], msg=result)
        self.assertEqual(result['data'], [1, 2])

    def test_unknown_op_reports_failure_without_connecting(self):
        result = run_one_call({'host': '127.0.0.1', 'port': self.server.port, 'op': 'NOPE'})
        self.assertFalse(result['ok'])
        self.assertIn('unknown op', result['error'])
        self.assertEqual(self.server.received, [])

    def test_write_then_read_round_trip_via_run_one_call(self):
        write_result = run_one_call({
            'host': self.server.host, 'port': self.server.port, 'op': 'WRITE',
            'scope': 'S', 'location': 'L', 'data': {'v': 1}, 'lock': LOCK_WRITE,
        })
        self.assertTrue(write_result['ok'])
        self.server.queue_response({'func': 'RESPONSE', 'data': {'v': 1}}, oper='READ')
        read_result = run_one_call({
            'host': self.server.host, 'port': self.server.port, 'op': 'READ',
            'scope': 'S', 'location': 'L', 'lock': LOCK_READ,
        })
        self.assertTrue(read_result['ok'])
        self.assertEqual(read_result['data'], {'v': 1})

    def test_ident_username_is_forwarded_on_a_later_call(self):
        """Real design point: since each op is a fresh connection (no
        persistent session), a caller that already called ident() once
        needs it re-sent on every subsequent call for the server to
        keep recognizing that identity -- run_one_call()'s
        ident_username/ident_password passthrough is what the JS shim
        relies on for this."""
        self.server.queue_response({'func': 'RESPONSE', 'data': 'ok'}, oper='READ')
        result = run_one_call({
            'host': self.server.host, 'port': self.server.port, 'op': 'READ',
            'scope': 'S', 'location': 'L', 'lock': LOCK_READ,
            'ident_username': 'jerry', 'ident_password': 'hunter2',
        })
        self.assertTrue(result['ok'])
        # First packet on the wire must be the IDENT, sent before the READ.
        self.assertEqual(self.server.received[0]['func'], 'IDENT')
        self.assertEqual(self.server.received[0]['username'], 'jerry')
        self.assertEqual(self.server.received[1]['oper'], 'READ')


class LineReaderTests(unittest.TestCase):
    """Direct unit test for the real bug behind run_listen_session()
    (subscribe()-driven push updates) never seeing a door's first
    subscribe() command: select() reports readiness at the OS/kernel
    level, but a single os.read() can pull in more than one line's
    worth of bytes at once -- json-client.js's ensureDaemon() writes
    the daemon's config line and its first command in immediate
    succession, so this isn't a hypothetical, it's the normal case.
    Once those extra bytes are out of the kernel pipe buffer and
    sitting in _LineReader's own internal buffer, select() has nothing
    left to report on them -- pop_buffered_lines() (checked before
    every select() call, not only in response to one) is what
    recovers them."""

    def test_readline_blocking_returns_only_the_first_line(self):
        r, w = os.pipe()
        try:
            os.write(w, b'{"a":1}\n{"b":2}\n')
            reader = _LineReader(r)
            self.assertEqual(reader.readline_blocking(), '{"a":1}')
        finally:
            os.close(r)
            os.close(w)

    def test_pop_buffered_lines_recovers_a_line_already_absorbed_by_an_earlier_read(self):
        r, w = os.pipe()
        try:
            os.write(w, b'{"a":1}\n{"b":2}\n')
            reader = _LineReader(r)
            reader.readline_blocking()  # consumes {"a":1}, silently also
                                        # pulls {"b":2} into the internal buffer
            os.close(w)  # close the write end -- a NEW os.read() here would
                        # return EOF; pop_buffered_lines() must NOT need one
            self.assertEqual(reader.pop_buffered_lines(), ['{"b":2}'])
        finally:
            os.close(r)

    def test_pop_buffered_lines_empty_when_nothing_buffered(self):
        r, w = os.pipe()
        try:
            reader = _LineReader(r)
            self.assertEqual(reader.pop_buffered_lines(), [])
        finally:
            os.close(r)
            os.close(w)

    def test_pop_ready_lines_returns_none_on_eof(self):
        r, w = os.pipe()
        os.close(w)
        try:
            reader = _LineReader(r)
            self.assertIsNone(reader.pop_ready_lines())
        finally:
            os.close(r)


class RunListenSessionTests(unittest.TestCase):
    """run_listen_session() is the persistent-connection daemon
    json-client.js's subscribe()/cycle() spawn and drive (see that
    file's ensureDaemon()) -- exercised directly here via a real
    os.pipe() standing in for stdin and a real throwaway TCP server,
    without needing a Node subprocess at all (test_jsonrpc_client_js_
    shim.py covers the full real-shim integration separately, but
    much more slowly given the Node + subprocess overhead)."""

    def setUp(self):
        self.server = _RecordingServer()
        self.addCleanup(self.server.close)
        self.updates_file = tempfile.NamedTemporaryFile(delete=False, suffix='.jsonl').name
        os.unlink(self.updates_file)  # must start nonexistent -- session creates it lazily

    def tearDown(self):
        try:
            os.unlink(self.updates_file)
        except OSError:
            pass

    def _start_session(self, extra_first_write=b''):
        """Returns (write_fd, thread, result_holder). `extra_first_write`
        is appended to the SAME os.write() call as the config line --
        used to reproduce the exact same-read() race the config+first-
        command case hits in real usage."""
        r, w = os.pipe()
        args = {'host': self.server.host, 'port': self.server.port,
               'scope': 'S', 'updates_file': self.updates_file}
        os.write(w, (json.dumps(args) + '\n').encode() + extra_first_write)
        reader = _LineReader(r)
        config_line = reader.readline_blocking()
        config = json.loads(config_line)
        result = {}
        def run():
            result['rc'] = run_listen_session(config, reader)
        t = threading.Thread(target=run, daemon=True)
        t.start()
        return w, t, result

    def test_subscribe_command_landing_in_the_same_read_as_config_is_not_lost(self):
        """Direct regression test for the real bug: reproduces config
        line + first command arriving in ONE os.write() (hence
        plausibly one underlying read()), confirming the subscribe
        command still reaches the server."""
        w, t, result = self._start_session(
            extra_first_write=(json.dumps({'cmd': 'subscribe', 'location': 'games'}) + '\n').encode())
        try:
            self.server.wait_for(1, timeout=3.0)
            self.assertEqual(self.server.received[0]['oper'], 'SUBSCRIBE')
            self.assertEqual(self.server.received[0]['location'], 'games')
        finally:
            os.write(w, (json.dumps({'cmd': 'quit'}) + '\n').encode())
            t.join(timeout=5.0)
            self.assertFalse(t.is_alive(), 'run_listen_session did not exit after quit')
            os.close(w)

    def test_pushed_update_is_appended_to_the_updates_file(self):
        w, t, result = self._start_session()
        try:
            os.write(w, (json.dumps({'cmd': 'subscribe', 'location': 'games'}) + '\n').encode())
            self.server.wait_for(1, timeout=3.0)
            self.server.push({'func': 'UPDATE', 'oper': 'WRITE',
                              'location': 'games.1', 'data': {'ready': True}})
            deadline_ok = False
            import time
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if os.path.exists(self.updates_file) and os.path.getsize(self.updates_file) > 0:
                    deadline_ok = True
                    break
                time.sleep(0.02)
            self.assertTrue(deadline_ok, 'update was never written to the updates file')
            with open(self.updates_file) as f:
                lines = [json.loads(line) for line in f if line.strip()]
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0]['data'], {'ready': True})
        finally:
            os.write(w, (json.dumps({'cmd': 'quit'}) + '\n').encode())
            t.join(timeout=5.0)
            self.assertFalse(t.is_alive())
            os.close(w)

    def test_quit_command_exits_cleanly(self):
        w, t, result = self._start_session()
        os.write(w, (json.dumps({'cmd': 'quit'}) + '\n').encode())
        t.join(timeout=5.0)
        self.assertFalse(t.is_alive())
        self.assertEqual(result.get('rc'), 0)
        os.close(w)

    def test_stdin_eof_exits_cleanly(self):
        """Matches the real deployment: the door process exits (e.g.
        the player quits), Node's own process exit closes the daemon's
        stdin pipe -- the daemon must notice and exit on its own,
        not hang forever."""
        w, t, result = self._start_session()
        os.close(w)  # EOF, no explicit quit command
        t.join(timeout=5.0)
        self.assertFalse(t.is_alive())
        self.assertEqual(result.get('rc'), 0)


if __name__ == '__main__':
    unittest.main()
