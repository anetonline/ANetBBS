"""Regression test for a client-side JS audit finding in
anetbbs/static/mrc/client.js: the WebSocket `onmessage` handler used to
run `JSON.parse(event.data)` on every inbound frame with no size check
at all.

The MRC bridge URL a page connects to is user-selectable (see
getSelectedWsUrl() in anetbbs/templates/mrc/index.html), so this isn't
purely "trust our own server" -- a misbehaving/compromised endpoint (or
something on the wire) handing back an arbitrarily large frame could
make the browser tab try to JSON.parse() a multi-megabyte-or-larger
string on every single message. Every real payload the bridge actually
sends is small (a chat body is already capped server-side at
MRC_MAX_MESSAGE_LEN=140 by _truncate_wire_message, plus a handful of
small fixed fields / a room's userlist), so a generous defensive cap
(MAX_WS_FRAME_CHARS, client.js) drops anything wildly larger before it
ever reaches JSON.parse() -- matching this project's existing
"unbounded buffer from network I/O" fix pattern used server-side
elsewhere (e.g. the BinkP receive-frame cap).

Runs the REAL client.js file under a real Node.js process (it already
has a `module.exports` block for exactly this), with a minimal fake
`WebSocket` standing in for the browser global, and drives the real
`onopen`/`onmessage` callbacks the class attaches to it -- not a
reimplementation of the logic under test.

Skipped entirely (not failed) when no real Node.js binary is present,
matching every other Node-dependent test in this suite.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CLIENT_JS_PATH = REPO_ROOT / 'anetbbs' / 'static' / 'mrc' / 'client.js'

_NODE_PATH = os.environ.get('NODEJS_PATH', '/usr/bin/node')
_HAVE_NODE = os.path.isfile(_NODE_PATH)


@unittest.skipUnless(_HAVE_NODE, 'requires a real Node.js binary')
class MrcClientJsFrameSizeCapTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(CLIENT_JS_PATH.is_file(), f'not found at {CLIENT_JS_PATH}')

    def _run(self, big_len):
        harness = f"""
const {{ MRCClient }} = require({json.dumps(str(CLIENT_JS_PATH))});

class FakeWebSocket {{
    constructor(url) {{
        this.url = url;
        FakeWebSocket.instances.push(this);
    }}
    send(msg) {{ this.lastSent = msg; }}
    close() {{}}
}}
FakeWebSocket.instances = [];
global.WebSocket = FakeWebSocket;

const client = new MRCClient('ws://example.invalid/test');
const received = [];
client.addMessageHandler(m => received.push(m));

client.connect();
const ws = FakeWebSocket.instances[0];
ws.onopen();

ws.onmessage({{ data: JSON.stringify({{ type: 'info', message: 'hello' }}) }});

const big = 'x'.repeat({big_len});
ws.onmessage({{ data: JSON.stringify({{ type: 'info', message: big }}) }});

ws.onmessage({{ data: JSON.stringify({{ type: 'info', message: 'still alive' }}) }});

process.stdout.write(JSON.stringify(received));
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as f:
            f.write(harness)
            script_path = f.name
        try:
            result = subprocess.run(
                [_NODE_PATH, script_path],
                capture_output=True, text=True, timeout=30)
            return result
        finally:
            os.unlink(script_path)

    def test_oversized_frame_is_dropped_before_reaching_json_parse(self):
        result = self._run(big_len=200_000)
        self.assertEqual(result.returncode, 0, msg=f'stderr: {result.stderr}')
        received = json.loads(result.stdout)
        # Only the two small, legitimate messages should have reached a
        # handler -- the oversized frame in between must be silently
        # dropped, not delivered (and not crash/hang the parse either).
        self.assertEqual(len(received), 2, msg=result.stdout)
        self.assertEqual(received[0]['message'], 'hello')
        self.assertEqual(received[1]['message'], 'still alive')

    def test_a_message_just_under_the_cap_still_comes_through(self):
        # Sanity check the cap isn't so tight it eats legitimate
        # payloads -- a message just under the raw-frame cap (accounting
        # for the small JSON envelope around it) must still arrive.
        result = self._run(big_len=1000)
        self.assertEqual(result.returncode, 0, msg=f'stderr: {result.stderr}')
        received = json.loads(result.stdout)
        self.assertEqual(len(received), 3, msg=result.stdout)
        self.assertEqual(len(received[1]['message']), 1000)


if __name__ == '__main__':
    unittest.main()
