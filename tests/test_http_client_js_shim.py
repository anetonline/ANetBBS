"""Integration test for anetbbs/games/sbbs_stubs/http.js -- the
ANetBBS-authored replacement for Synchronet's real http.js (see that
file's own docstring for the full compatibility-contract reasoning:
only SendRequest is replaced, since real Synchronet's own
HTTPRequest.prototype.SetupGet/SetupPost/AddDefaultHeaders/
AddExtraHeaders/BasicAuth/ReadStatus/ReadHeaders/ReadBody/ReadResponse/
Get/Post/Head are all pure string/regex logic with zero Socket
dependency, and are copied unmodified into the shim).

Found live while scoping Jeopardized (the 4th Synchronet JSON-RPC
door): its own func.js calls `(new HTTPRequest()).Get(...)` against a
real Web API to check answers -- the first bundled door to need real
outbound HTTP at all. Runs the REAL shim file under a real Node.js
process against a real local HTTP server (Python's stdlib
http.server), matching the same "real script through the real Node
shim, real wire round trip" pattern already established for
json-client.js's own tests.

Skipped entirely (not failed) when no real Node.js binary is present,
matching every other Node-shim test in this suite.
"""
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HTTP_SHIM_PATH = REPO_ROOT / 'anetbbs' / 'games' / 'sbbs_stubs' / 'http.js'
SOCKDEFS_PATH = REPO_ROOT / 'anetbbs' / 'games' / 'sbbs_stubs' / 'sockdefs.js'
URL_PATH = REPO_ROOT / 'anetbbs' / 'games' / 'sbbs_stubs' / 'url.js'
CLI_PATH = REPO_ROOT / 'anetbbs' / 'games' / 'http_client.py'

_NODE_PATH = os.environ.get('NODEJS_PATH', '/usr/bin/node')
_HAVE_NODE = os.path.isfile(_NODE_PATH)


class _FakeHTTPHandler(http.server.BaseHTTPRequestHandler):
    """Echoes back enough of the request (path, method, a couple of
    headers, body) as JSON for tests to assert on -- real request
    handling, not a canned response, so a test can confirm the shim
    actually sent what it claims to."""

    def _respond(self, status, extra=None):
        payload = {
            'method': self.command,
            'path': self.path,
            'headers': dict(self.headers.items()),
        }
        if extra:
            payload.update(extra)
        body = json.dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/notfound':
            self._respond(404)
            return
        self._respond(200)

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        data = self.rfile.read(length).decode('utf-8', errors='replace')
        self._respond(201, {'body': data})

    def log_message(self, *args):
        pass


class _FakeHTTPServer:
    def __init__(self):
        self.httpd = http.server.HTTPServer(('127.0.0.1', 0), _FakeHTTPHandler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@unittest.skipUnless(_HAVE_NODE, 'requires a real Node.js binary')
class HttpClientShimTests(unittest.TestCase):
    def setUp(self):
        self.server = _FakeHTTPServer()
        self.addCleanup(self.server.close)
        self.assertTrue(HTTP_SHIM_PATH.is_file(), f'shim not found at {HTTP_SHIM_PATH}')
        self.assertTrue(CLI_PATH.is_file(), f'CLI not found at {CLI_PATH}')

    def _run_node_script(self, script_body):
        """Minimal harness a real door script gets from the compat
        shim: load() resolves http.js/sockdefs.js/url.js to the real
        shim/vendored files, plus the small set of compat-shim globals
        http.js's own real (unmodified) code touches directly
        (system.name, base64_encode, js.global, format) -- these exist
        for real in synchronet_compat.py; stubbed minimally here since
        this test drives http.js standalone, not through the full
        generated compat script."""
        harness = (
            "global._node_require = require;\n"
            "global._fs = require('fs');\n"
            "global._path = require('path');\n"
            "var _vm = require('vm');\n"
            "global.js = { global: {}, flatten_string: function () {} };\n"
            "global.system = { name: 'TestBBS' };\n"
            "global.format = function (fmt) { return fmt; };\n"
            "global.base64_encode = function (s) {\n"
            "  return Buffer.from(s, 'binary').toString('base64');\n"
            "};\n"
            "global.Socket = { PF_INET: 2, PF_INET6: 10, AF_INET: 2, AF_INET6: 10 };\n"
            "function _loadReal(f, p) {\n"
            "  _vm.runInThisContext(\n"
            "    require('fs').readFileSync(p, 'utf8'), { filename: p }\n"
            "  );\n"
            "}\n"
            "global.load = function (f) {\n"
            "  if (f === 'sockdefs.js') return _loadReal(f, " + json.dumps(str(SOCKDEFS_PATH)) + ");\n"
            "  if (f === 'url.js') return _loadReal(f, " + json.dumps(str(URL_PATH)) + ");\n"
            "  if (f === 'http.js') return _loadReal(f, " + json.dumps(str(HTTP_SHIM_PATH)) + ");\n"
            "  throw new Error('unexpected load(' + f + ')');\n"
            "};\n"
            "global.require = function (filename, objname) {\n"
            "  load(filename);\n"
            "  return global[objname];\n"
            "};\n"
            "load('http.js');\n"
            + script_body
        )
        with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as f:
            f.write(harness)
            script_path = f.name
        try:
            env = dict(os.environ)
            env['ANETBBS_JSONRPC_CLI_PYTHON'] = sys.executable
            env['ANETBBS_HTTP_CLI_PATH'] = str(CLI_PATH)
            result = subprocess.run(
                [_NODE_PATH, script_path], env=env,
                capture_output=True, text=True, timeout=30)
            return result
        finally:
            os.unlink(script_path)

    def test_get_reaches_the_real_server_and_returns_the_real_body(self):
        script = (
            "var req = new HTTPRequest();\n"
            f"var body = req.Get('http://127.0.0.1:{self.server.port}/clues/1/compare/answer');\n"
            "process.stdout.write('CODE:' + req.response_code + '\\n');\n"
            "process.stdout.write('BODY:' + body);\n"
        )
        result = self._run_node_script(script)
        self.assertEqual(result.returncode, 0, msg=f'stderr: {result.stderr}')
        self.assertIn('CODE:200', result.stdout, msg=result.stdout)
        parsed = json.loads(result.stdout.split('BODY:', 1)[1])
        self.assertEqual(parsed['method'], 'GET')
        self.assertEqual(parsed['path'], '/clues/1/compare/answer')

    def test_get_of_a_404_path_still_returns_the_body_not_an_exception(self):
        """Real Synchronet's Get() only follows redirects specially;
        a 4xx/5xx status is returned like any other -- the door itself
        (Jeopardized's compareAnswer(), confirmed reading func.js)
        JSON.parse()s the body directly with no status check at all,
        so Get() throwing on a non-200 would be a real behavioral
        deviation from the source this was verified against."""
        script = (
            "var req = new HTTPRequest();\n"
            f"var body = req.Get('http://127.0.0.1:{self.server.port}/notfound');\n"
            "process.stdout.write('CODE:' + req.response_code + '\\n');\n"
            "process.stdout.write('BODY:' + body);\n"
        )
        result = self._run_node_script(script)
        self.assertEqual(result.returncode, 0, msg=f'stderr: {result.stderr}')
        self.assertIn('CODE:404', result.stdout, msg=result.stdout)

    def test_post_sends_the_real_body_and_content_type(self):
        script = (
            "var req = new HTTPRequest();\n"
            f"var body = req.Post('http://127.0.0.1:{self.server.port}/submit', 'answer=42');\n"
            "process.stdout.write('CODE:' + req.response_code + '\\n');\n"
            "process.stdout.write('BODY:' + body);\n"
        )
        result = self._run_node_script(script)
        self.assertEqual(result.returncode, 0, msg=f'stderr: {result.stderr}')
        self.assertIn('CODE:201', result.stdout, msg=result.stdout)
        parsed = json.loads(result.stdout.split('BODY:', 1)[1])
        self.assertEqual(parsed['method'], 'POST')
        self.assertEqual(parsed['body'], 'answer=42')
        self.assertEqual(
            parsed['headers'].get('Content-Type'),
            'application/x-www-form-urlencoded')

    def test_extra_headers_are_actually_sent(self):
        script = (
            "var req = new HTTPRequest(undefined, undefined, {'X-Test-Header': 'hello'});\n"
            f"var body = req.Get('http://127.0.0.1:{self.server.port}/');\n"
            "process.stdout.write('BODY:' + body);\n"
        )
        result = self._run_node_script(script)
        self.assertEqual(result.returncode, 0, msg=f'stderr: {result.stderr}')
        parsed = json.loads(result.stdout.split('BODY:', 1)[1])
        self.assertEqual(parsed['headers'].get('X-Test-Header'), 'hello')

    def test_connection_failure_throws_a_catchable_error_not_a_crash(self):
        """Real Synchronet's SendRequest() throws a real Error on
        connection failure -- confirmed against the real vendored
        source (sbbs_reference/http.js) preserved unmodified in this
        shim. A door catching that (or letting it propagate as a
        normal script error) must see the same behavior, not an
        uncaught subprocess crash."""
        script = (
            "var req = new HTTPRequest();\n"
            "try {\n"
            "  req.Get('http://127.0.0.1:1/unreachable');\n"
            "  process.stdout.write('NO_ERROR_THROWN');\n"
            "} catch (e) {\n"
            "  process.stdout.write('THREW:' + e.message);\n"
            "}\n"
        )
        result = self._run_node_script(script)
        self.assertEqual(result.returncode, 0, msg=f'stderr: {result.stderr}')
        self.assertIn('THREW:', result.stdout, msg=result.stdout)

    def test_sockdefs_loads_cleanly_via_the_real_two_arg_require(self):
        """Real crash found live: sockdefs.js (real, unmodified vendored
        file) unconditionally reads Socket.PF_INET/.PF_INET6/.AF_INET/
        .AF_INET6 at load time -- this project deliberately never
        implements a real Socket (no synchronous TCP in Node; both
        json-client.js and http.js's shim replacements exist specifically
        to avoid needing one), so `Socket` was undefined and any door
        merely loading sockdefs.js (a standard pattern for anything
        network-adjacent -- Jeopardized's own
        `require('sockdefs.js', 'SOCK_STREAM')`) crashed immediately,
        before ever reaching its own logic. This test loads http.js
        itself (which requires sockdefs.js as its own first line) as
        the real reproduction -- if the Socket stub is missing or
        incomplete, this whole harness fails to even load the shim."""
        script = "process.stdout.write('SOCK_STREAM:' + SOCK_STREAM);\n"
        result = self._run_node_script(script)
        self.assertEqual(result.returncode, 0, msg=f'stderr: {result.stderr}')
        self.assertIn('SOCK_STREAM:1', result.stdout, msg=result.stdout)
