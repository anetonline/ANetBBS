"""Integration test for anetbbs/games/sbbs_stubs/json-client.js -- the
ANetBBS-authored drop-in replacement for Synchronet's real
json-client.js (see that file's own docstring for the full
compatibility-contract reasoning). Runs the REAL shim file under a
real Node.js process (not a reimplementation of it), driving a
synthetic door script that calls JSONClient exactly the way a real
Synchronet door does, against a throwaway Python fake server -- same
"real script through the real Node shim" pattern as
test_door_synchronet_temp_cleanup.py, extended to also exercise a real
TCP round trip.

Skipped entirely (not failed) when no real Node.js binary is present,
matching every other Node-shim test in this suite -- this environment
isn't guaranteed to have Node installed, and CI shouldn't fail over an
optional runtime dependency.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[1]
SHIM_PATH = REPO_ROOT / 'anetbbs' / 'games' / 'sbbs_stubs' / 'json-client.js'
CLI_PATH = REPO_ROOT / 'anetbbs' / 'games' / 'jsonrpc_client.py'

_NODE_PATH = os.environ.get('NODEJS_PATH', '/usr/bin/node')
_HAVE_NODE = os.path.isfile(_NODE_PATH)


class _FakeServer:
    """Same real-socket, real-wire-format fake server as the pure
    Python client tests -- exists independently here (rather than
    importing the other test module's helper) so this file stays
    runnable/skippable on its own, and so a change to one test's
    helper can't silently affect the other."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(5)
        self.host, self.port = self.sock.getsockname()
        self.store = {}
        self.received = []  # every parsed packet, in arrival order
        self.connections = []  # every still-open connection, in accept order
        self._closing = False
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self):
        while not self._closing:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
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
                self.received.append(pkt)
                if pkt.get('func') == 'IDENT':
                    continue
                # A bare admin-style packet (no 'oper' at all -- real
                # Synchronet's ADMIN/TIME query, exactly what
                # send()/wait() sends raw with none of the higher-level
                # methods' scope/location/oper structure).
                if pkt.get('scope') == 'ADMIN' and pkt.get('func') == 'TIME':
                    conn.sendall((json.dumps({'func': 'RESPONSE', 'data': 1234567890}) + '\r\n').encode())
                    continue
                oper, scope, loc = pkt.get('oper'), pkt.get('scope'), pkt.get('location')
                if oper == 'WRITE':
                    self.store.setdefault(scope, {})[loc] = pkt.get('data')
                    continue
                if oper == 'READ':
                    data = self.store.get(scope, {}).get(loc)
                    conn.sendall((json.dumps({'func': 'RESPONSE', 'data': data}) + '\r\n').encode())
        except OSError:
            pass
        finally:
            try:
                self.connections.remove(conn)
            except ValueError:
                pass
            try:
                conn.close()
            except OSError:
                pass

    def wait_for_connection(self, timeout=5.0):
        """Polls for at least one currently-open connection -- used by
        subscribe()-related tests to know the daemon has actually
        connected before pushing an unprompted UPDATE packet at it."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.connections:
                return self.connections[-1]
            time.sleep(0.05)
        raise AssertionError('no connection arrived within timeout')

    def wait_for_received(self, predicate, timeout=5.0):
        """Polls until some packet in self.received matches `predicate`
        -- used to know a specific command (e.g. a real SUBSCRIBE
        packet) has actually reached the server before proceeding."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if any(predicate(p) for p in self.received):
                return
            time.sleep(0.05)
        raise AssertionError(f'no matching packet within timeout: {self.received}')

    def close(self):
        self._closing = True
        try:
            self.sock.close()
        except OSError:
            pass


@unittest.skipUnless(_HAVE_NODE, 'requires a real Node.js binary')
class JsonClientShimTests(unittest.TestCase):
    def setUp(self):
        self.server = _FakeServer()
        self.addCleanup(self.server.close)
        self.assertTrue(SHIM_PATH.is_file(), f'shim not found at {SHIM_PATH}')
        self.assertTrue(CLI_PATH.is_file(), f'CLI not found at {CLI_PATH}')

    def _run_node_script(self, script_body, env_overrides=None):
        """Wraps `script_body` with the minimal harness a real door
        script gets from the compat shim: a `load()` that resolves
        json-client.js to the real shim file (and no-ops the shim's
        own `load("json-sock.js")`, exactly as the real shim's
        docstring notes it doesn't need it), and `_node_require`
        bound the same way the real compat shim binds it
        (module.require). `env_overrides` lets a test point the
        daemon's own interpreter at something other than the real
        jsonrpc_client.py (e.g. a program that exits immediately, to
        deliberately produce a dead-daemon-pipe scenario)."""
        harness = (
            "global._node_require = require;\n"
            "global._fs = require('fs');\n"
            "global._path = require('path');\n"
            "var _vm = require('vm');\n"
            "global.load = function(f) {\n"
            "  if (f === 'json-sock.js') return;\n"
            "  if (f === 'json-client.js') {\n"
            # Matches the real compat shim's own load() exactly (see its
            # comment on why runInThisContext is required and indirect
            # eval is NOT equivalent in Node's CommonJS module wrapper
            # context): top-level var/function in the loaded file must
            # land on globalThis for the rest of the script to see them.
            "    _vm.runInThisContext(\n"
            "      require('fs').readFileSync(" + json.dumps(str(SHIM_PATH)) + ", 'utf8'),\n"
            "      { filename: " + json.dumps(str(SHIM_PATH)) + " }\n"
            "    );\n"
            "    return;\n"
            "  }\n"
            "  throw new Error('unexpected load(' + f + ')');\n"
            "};\n"
            + script_body
        )
        with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as f:
            f.write(harness)
            script_path = f.name
        try:
            env = dict(os.environ)
            env['ANETBBS_JSONRPC_CLI_PYTHON'] = sys.executable
            env['ANETBBS_JSONRPC_CLI_PATH'] = str(CLI_PATH)
            if env_overrides:
                env.update(env_overrides)
            result = subprocess.run(
                [_NODE_PATH, script_path], env=env,
                capture_output=True, text=True, timeout=45)
            return result
        finally:
            os.unlink(script_path)

    def test_synthetic_door_write_then_read_round_trip(self):
        """Mirrors a real door's own usage pattern (e.g.
        chickendelivery.js's pushScore()/highScores() -- write a score
        record, read it back), calling ONLY the documented public
        JSONClient methods, exactly as an unmodified real door
        script would."""
        script = (
            "load('json-client.js');\n"
            f"var client = new JSONClient('127.0.0.1', {self.server.port});\n"
            "client.write('CHICKENDELIVERY2', 'SCORES.LATEST', "
            "{uid:'amF5', level:3, score:42, date:1234567890}, 2);\n"
            "var result = client.read('CHICKENDELIVERY2', 'SCORES.LATEST', 1);\n"
            "console.log(JSON.stringify(result));\n"
        )
        result = self._run_node_script(script)
        self.assertEqual(result.returncode, 0, msg=f'stderr: {result.stderr}')
        data = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(data, {'uid': 'amF5', 'level': 3, 'score': 42, 'date': 1234567890})

    def test_read_of_missing_key_returns_null_not_a_crash(self):
        script = (
            "load('json-client.js');\n"
            f"var client = new JSONClient('127.0.0.1', {self.server.port});\n"
            "var result = client.read('NOSCOPE', 'NOTHING.HERE', 1);\n"
            "console.log(JSON.stringify(result === undefined ? null : result));\n"
        )
        result = self._run_node_script(script)
        self.assertEqual(result.returncode, 0, msg=f'stderr: {result.stderr}')
        self.assertEqual(result.stdout.strip().splitlines()[-1], 'null')

    def test_write_does_not_block_waiting_for_a_response(self):
        """The fake server never replies to WRITE (matching the real
        server's own behavior for a fire-and-forget op) -- if the shim
        incorrectly waited for a response here, this would hang until
        the subprocess timeout and fail, not just run slow."""
        script = (
            "load('json-client.js');\n"
            f"var client = new JSONClient('127.0.0.1', {self.server.port});\n"
            "client.write('S', 'L', {x:1}, 2);\n"
            "console.log('done');\n"
        )
        result = self._run_node_script(script)
        self.assertEqual(result.returncode, 0, msg=f'stderr: {result.stderr}')
        self.assertEqual(result.stdout.strip(), 'done')

    def test_send_and_wait_round_trip_a_bare_packet(self):
        """Real gap found live bundling Thirstyville: `jsonClient.send(
        {scope:"ADMIN", func:"TIME"}); jsonClient.wait();` -- the real
        client's own low-level send()/wait() pair, used for packet
        shapes (like this real Synchronet admin query) that don't fit
        any of the higher-level read/write/etc convenience methods.
        Neither existed in this shim at all before this fix."""
        script = (
            "load('json-client.js');\n"
            f"var client = new JSONClient('127.0.0.1', {self.server.port});\n"
            "client.send({scope:'ADMIN', func:'TIME'});\n"
            "var result = client.wait();\n"
            "console.log('RESULT:' + result);\n"
        )
        result = self._run_node_script(script)
        self.assertEqual(result.returncode, 0, msg=f'stderr: {result.stderr}')
        self.assertIn('RESULT:1234567890', result.stdout)

    def test_wait_without_a_prior_send_throws_a_catchable_error(self):
        script = (
            "load('json-client.js');\n"
            f"var client = new JSONClient('127.0.0.1', {self.server.port});\n"
            "try {\n"
            "  client.wait();\n"
            "  console.log('NO_ERROR');\n"
            "} catch (e) {\n"
            "  console.log('THREW:' + e.message);\n"
            "}\n"
        )
        result = self._run_node_script(script)
        self.assertEqual(result.returncode, 0, msg=f'stderr: {result.stderr}')
        self.assertIn('THREW:', result.stdout)

    def test_missing_host_or_port_throws_synchronously(self):
        script = (
            "load('json-client.js');\n"
            "try {\n"
            "  var client = new JSONClient(undefined, 10088);\n"
            "  console.log('NO ERROR THROWN');\n"
            "} catch (e) {\n"
            "  console.log('THREW: ' + e.message);\n"
            "}\n"
        )
        result = self._run_node_script(script)
        self.assertEqual(result.returncode, 0, msg=f'stderr: {result.stderr}')
        self.assertIn('THREW', result.stdout)
        self.assertIn('no host specified', result.stdout)

    def test_lock_then_bare_read_auto_injects_the_tracked_lock(self):
        """Real reproduction of a hang found live on the Pi3: Bubble
        Boggle's game.js does `client.lock(scope,"month",2); ...;
        client.read(scope,"month"); ...; client.unlock(...)`. Real
        Synchronet's server (confirmed against the real json-db.js
        source) requires a bare read/write to have been locked by the
        SAME connection/client identity -- but this shim opens a fresh
        TCP connection per call, so a lock() sent on one connection was
        never visible to a later read() on a different connection, and
        the read just hung until this shim's own recv timeout ("
        connection error: timed out"). Fixed by tracking lock()/
        unlock() purely client-side (no network call at all) and
        auto-injecting the tracked lock into any bare (no explicit
        lock argument) call for a location lock() covers -- confirmed
        here by checking the actual packet the fake server received."""
        script = (
            "load('json-client.js');\n"
            f"var client = new JSONClient('127.0.0.1', {self.server.port});\n"
            "client.lock('S', 'month', 2);\n"
            "client.read('S', 'month');\n"
            "console.log('done');\n"
        )
        result = self._run_node_script(script)
        self.assertEqual(result.returncode, 0, msg=f'stderr: {result.stderr}')
        reads = [p for p in self.server.received if p.get('oper') == 'READ']
        self.assertEqual(len(reads), 1, msg=self.server.received)
        self.assertEqual(reads[0].get('lock'), 2, msg=reads[0])

    def test_unlock_clears_the_tracked_lock(self):
        script = (
            "load('json-client.js');\n"
            f"var client = new JSONClient('127.0.0.1', {self.server.port});\n"
            "client.lock('S', 'month', 2);\n"
            "client.unlock('S', 'month');\n"
            "client.read('S', 'month');\n"
            "console.log('done');\n"
        )
        result = self._run_node_script(script)
        self.assertEqual(result.returncode, 0, msg=f'stderr: {result.stderr}')
        reads = [p for p in self.server.received if p.get('oper') == 'READ']
        self.assertEqual(len(reads), 1, msg=self.server.received)
        self.assertIsNone(reads[0].get('lock'), msg=reads[0])

    def test_lock_on_parent_location_covers_bare_child_operations(self):
        """Real reproduction of game.js's other lock pattern:
        `client.lock(scope,"boards",2); ...;
        client.write(scope,"boards."+dn,board);` (bare, no inline
        lock, inside a loop writing several numbered sub-records) --
        matches real Synchronet's own hierarchical lock inheritance
        (confirmed in json-db.js's identify_remains()/investigate(),
        which walks a dot-path's ancestors accumulating their lock
        state) for a lock held on the parent to cover a child path."""
        script = (
            "load('json-client.js');\n"
            f"var client = new JSONClient('127.0.0.1', {self.server.port});\n"
            "client.lock('S', 'boards', 2);\n"
            "client.read('S', 'boards.5');\n"
            "console.log('done');\n"
        )
        result = self._run_node_script(script)
        self.assertEqual(result.returncode, 0, msg=f'stderr: {result.stderr}')
        reads = [p for p in self.server.received if p.get('oper') == 'READ']
        self.assertEqual(len(reads), 1, msg=self.server.received)
        self.assertEqual(reads[0].get('location'), 'boards.5')
        self.assertEqual(reads[0].get('lock'), 2, msg=reads[0])

    def test_explicit_lock_argument_overrides_tracked_state(self):
        """An explicit lock argument (Chicken Delivery's own pattern --
        it never calls lock()/unlock() at all, always passes lock
        inline) must never be silently overridden by unrelated tracked
        state from a separate lock() call."""
        script = (
            "load('json-client.js');\n"
            f"var client = new JSONClient('127.0.0.1', {self.server.port});\n"
            "client.lock('S', 'month', 2);\n"
            "client.read('S', 'month', 1);\n"
            "console.log('done');\n"
        )
        result = self._run_node_script(script)
        self.assertEqual(result.returncode, 0, msg=f'stderr: {result.stderr}')
        reads = [p for p in self.server.received if p.get('oper') == 'READ']
        self.assertEqual(len(reads), 1, msg=self.server.received)
        self.assertEqual(reads[0].get('lock'), 1, msg=reads[0])

    def test_lock_and_unlock_never_touch_the_network(self):
        """lock()/unlock() are pure local bookkeeping -- no real lock
        is ever created on the shared production server this shim
        talks to, avoiding any stray real lock outliving a one-shot
        connection."""
        script = (
            "load('json-client.js');\n"
            f"var client = new JSONClient('127.0.0.1', {self.server.port});\n"
            "client.lock('S', 'month', 2);\n"
            "client.unlock('S', 'month');\n"
            "console.log('done');\n"
        )
        result = self._run_node_script(script)
        self.assertEqual(result.returncode, 0, msg=f'stderr: {result.stderr}')
        self.assertEqual(self.server.received, [])

    def test_subscribe_then_cycle_delivers_a_pushed_update(self):
        """Real reproduction of what synchronetris's lobby depends on
        entirely: subscribe() to a location, then see updates arrive
        via cycle() whenever the server pushes one -- unprompted, on
        its own schedule, not as a reply to any specific request.
        Confirms the persistent daemon connection (spawned by
        subscribe(), see json-client.js's ensureDaemon()) actually
        receives a real UPDATE packet (Synchronet's real push-
        notification func, confirmed against json-db.js's
        send_packet(client,"UPDATE",...) calls) and that cycle()
        surfaces it in client.updates."""
        def pusher():
            conn = self.server.wait_for_connection()
            self.server.wait_for_received(lambda p: p.get('oper') == 'SUBSCRIBE')
            push_pkt = {'func': 'UPDATE', 'oper': 'WRITE',
                       'location': 'games.1', 'data': {'ready': True}}
            try:
                conn.sendall((json.dumps(push_pkt) + '\r\n').encode())
            except OSError:
                pass  # connection already closed -- the test's own assertions will fail below
        t = threading.Thread(target=pusher, daemon=True)
        t.start()
        self.addCleanup(t.join, 10)

        script = (
            "load('json-client.js');\n"
            f"var client = new JSONClient('127.0.0.1', {self.server.port});\n"
            "client.subscribe('S', 'games');\n"
            "var start = Date.now();\n"
            "while (Date.now() - start < 15000) {\n"
            "  client.cycle();\n"
            "  if (client.updates.length > 0) break;\n"
            "  var wait = Date.now() + 50; while (Date.now() < wait) {}\n"
            "}\n"
            "console.log(JSON.stringify(client.updates));\n"
            "client.disconnect();\n"
        )
        result = self._run_node_script(script)
        self.assertEqual(result.returncode, 0, msg=f'stderr: {result.stderr}')
        updates = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(len(updates), 1, msg=updates)
        self.assertEqual(updates[0]['data'], {'ready': True})
        self.assertEqual(updates[0]['oper'], 'WRITE')

    def test_cycle_delivers_via_callback_instead_of_updates_array_when_set(self):
        """Matches the real client's own cycle() semantics exactly: if
        a callback function is set, pushed packets go there instead of
        accumulating in client.updates."""
        def pusher():
            conn = self.server.wait_for_connection()
            self.server.wait_for_received(lambda p: p.get('oper') == 'SUBSCRIBE')
            push_pkt = {'func': 'UPDATE', 'oper': 'WRITE', 'location': 'x', 'data': 42}
            try:
                conn.sendall((json.dumps(push_pkt) + '\r\n').encode())
            except OSError:
                pass
        t = threading.Thread(target=pusher, daemon=True)
        t.start()
        self.addCleanup(t.join, 10)

        script = (
            "load('json-client.js');\n"
            f"var client = new JSONClient('127.0.0.1', {self.server.port});\n"
            "var seen = [];\n"
            "client.callback = function(pkt) { seen.push(pkt); };\n"
            "client.subscribe('S', 'games');\n"
            "var start = Date.now();\n"
            "while (Date.now() - start < 15000 && seen.length === 0) {\n"
            "  client.cycle();\n"
            "  var wait = Date.now() + 50; while (Date.now() < wait) {}\n"
            "}\n"
            "console.log(JSON.stringify({seen: seen, updates: client.updates}));\n"
            "client.disconnect();\n"
        )
        result = self._run_node_script(script)
        self.assertEqual(result.returncode, 0, msg=f'stderr: {result.stderr}')
        parsed = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(len(parsed['seen']), 1, msg=parsed)
        self.assertEqual(parsed['seen'][0]['data'], 42)
        self.assertEqual(parsed['updates'], [], msg='must not double-deliver to updates too')

    def test_process_exits_naturally_after_subscribe_without_explicit_disconnect(self):
        """Real Pi3 lockup: Synchronetris's own lobby.js (real,
        unmodified source) calls subscribe() once at startup and never
        calls disconnect() on a normal clean quit -- matches how real
        Synchronet doors are written, since real Synchronet's actual
        client doesn't spawn a whole separate OS process for
        subscribe() the way this shim's persistent daemon does.
        ensureDaemon()'s spawned child had a still-open stdin pipe --
        by default a REFERENCED handle -- so even after the door's own
        script finished running in full, Node's event loop never went
        idle and the process never exited. The surrounding session
        handler was left waiting forever for a process that would
        never terminate on its own -- indistinguishable from a total
        lockup, confirmed live (only a raw client-level disconnect
        escaped it). daemonProc.unref() is the fix. This asserts the
        actual observable behavior (the node process exits on its
        own) rather than just checking unref() was called -- before
        the fix this test would hang until subprocess's own 45s
        timeout and fail with TimeoutExpired."""
        script = (
            "load('json-client.js');\n"
            f"var client = new JSONClient('127.0.0.1', {self.server.port});\n"
            "client.subscribe('S', 'games');\n"
            "console.log('done');\n"
            # Deliberately no client.disconnect() call here -- matches
            # Synchronetris's real lobby.js exactly.
        )
        result = self._run_node_script(script)
        self.assertEqual(result.returncode, 0, msg=f'stderr: {result.stderr}')
        self.assertIn('done', result.stdout)

    def test_a_dead_daemon_pipe_does_not_crash_the_whole_process(self):
        """Real Pi3 crash, found immediately after the unref() fix
        above: quitting Synchronetris's actual game (not just the
        lobby) threw "Error: write EPIPE" and killed the ENTIRE node
        process, from game.js's own close() -> client.unsubscribe()
        -> sendDaemonCommand()'s daemonProc.stdin.write() call. A
        write to a pipe whose other end already closed doesn't throw
        synchronously in Node -- it surfaces later as an async
        'error' event on the stream -- so the existing try/catch
        around that write (meant to make this best-effort) never
        actually catches it, and an unlistened 'error' event crashes
        the process. Points the daemon's own interpreter at /bin/true
        (spawns successfully, then exits almost immediately,
        matching a daemon that's already gone by the time a later
        command tries to reach it) to reproduce a dead pipe
        deterministically, without needing to race a real subprocess
        exit."""
        script = (
            "load('json-client.js');\n"
            f"var client = new JSONClient('127.0.0.1', {self.server.port});\n"
            "client.subscribe('S', 'games');\n"
            # Give /bin/true time to actually exit and close its end
            # of the pipe before the next write.
            "var wait = Date.now() + 1000; while (Date.now() < wait) {}\n"
            "client.unsubscribe('S', 'games');\n"
            "console.log('done');\n"
        )
        result = self._run_node_script(script, env_overrides={'ANETBBS_JSONRPC_CLI_PYTHON': '/bin/true'})
        self.assertEqual(result.returncode, 0, msg=f'stderr: {result.stderr}')
        self.assertNotIn('EPIPE', result.stderr, msg=result.stderr)
        self.assertIn('done', result.stdout)

    def test_disconnect_stops_the_daemon(self):
        """disconnect() must actually terminate the background daemon
        process, not leak it -- confirmed here by the fake server
        observing the connection close shortly afterward."""
        script = (
            "load('json-client.js');\n"
            f"var client = new JSONClient('127.0.0.1', {self.server.port});\n"
            "client.subscribe('S', 'games');\n"
            "var start = Date.now();\n"
            "while (Date.now() - start < 2000) {}\n"  # let the daemon actually connect
            "client.disconnect();\n"
            "console.log('done');\n"
        )
        result = self._run_node_script(script)
        self.assertEqual(result.returncode, 0, msg=f'stderr: {result.stderr}')
        deadline = time.time() + 5.0
        while time.time() < deadline and self.server.connections:
            time.sleep(0.05)
        self.assertEqual(self.server.connections, [], 'daemon connection was not closed by disconnect()')

    def test_subscribe_with_no_server_reachable_does_not_crash_the_door(self):
        """A door subscribing against an unreachable server must not
        raise/crash -- the daemon simply fails quietly in the
        background (subscribe() itself never blocks on a connection
        attempt), matching how real doors treat subscribe() as
        fire-and-forget."""
        script = (
            "load('json-client.js');\n"
            "var client = new JSONClient('127.0.0.1', 1);\n"
            "client.subscribe('S', 'games');\n"
            "console.log('done');\n"
        )
        result = self._run_node_script(script)
        self.assertEqual(result.returncode, 0, msg=f'stderr: {result.stderr}')
        self.assertIn('done', result.stdout)

    def test_unreachable_server_raises_a_catchable_error(self):
        script = (
            "load('json-client.js');\n"
            "var client = new JSONClient('127.0.0.1', 1);\n"
            "try {\n"
            "  client.read('S', 'L', 1);\n"
            "  console.log('NO ERROR THROWN');\n"
            "} catch (e) {\n"
            "  console.log('THREW');\n"
            "}\n"
        )
        result = self._run_node_script(script)
        self.assertEqual(result.returncode, 0, msg=f'stderr: {result.stderr}')
        self.assertIn('THREW', result.stdout)


if __name__ == '__main__':
    unittest.main()
