"""Regression test for a real leak found in a security/performance audit
of anetbbs/games/dos_bridge.py: DosBridge.bind_emit()'s pump thread, on
discovering DOSBox never dialed in within the connect-wait window, used
to just `return` -- skipping the `self.stop()` + `on_close()` call the
`finally:` block does for every OTHER exit path.

door_runner._on_bridge_close() (SIGTERMs the door's process group and
runs the session's real DB/node teardown) only ever fires via
`on_close`, so a DOSBox that never connects (broken binary, xvfb-run
failure, a snap-confine issue that slipped past the earlier detection,
etc.) used to leak the bridge's listening socket forever -- one of the
fixed BASE_PORT..MAX_PORT (5000..5100) port slots every DosBridge draws
from -- and left GameSession/node/process-group cleanup stuck until
node_manager's 1-hour stale-session backstop, instead of firing
immediately like every other door_dos failure path.

This test forces the "never connected" branch deterministically (by
pre-setting the internal stop event so the connect-wait loop exits on
its very first check, matching a real DOSBox-never-connects timeout
without waiting out the real ~60s window) and verifies both symptoms
the fix closes: the listening socket is actually released (so the port
becomes bindable again), and on_close() actually fires.
"""
import socket
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.games.dos_bridge import DosBridge


class DosBridgeNeverConnectedCleanupTests(unittest.TestCase):
    def test_stop_and_on_close_fire_when_dosbox_never_connects(self):
        bridge = DosBridge()
        port = bridge.start()
        self.addCleanup(bridge.stop)

        # Force the "never connected" branch to fire on the pump
        # thread's very first check instead of waiting out the real
        # ~60s connect window -- the wait loop's own condition is
        # `if self._dos_sock or self._stop_event.is_set(): break`.
        bridge._stop_event.set()

        on_close_called = threading.Event()
        bridge.bind_emit(lambda data: None,
                         on_close=on_close_called.set,
                         idle_timeout=300)

        self.assertTrue(
            on_close_called.wait(timeout=5),
            'on_close was not called when DOSBox never connected')
        self.assertIsNone(bridge.listener,
                          'listener socket was not released (stop() never ran)')

        # The port must be genuinely free again, not just logically
        # "closed" on the Python object -- this is the real symptom of
        # the leak (a held OS-level listening socket, not just stale
        # bookkeeping).
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(('127.0.0.1', port))
        except OSError as exc:
            self.fail(f'port {port} still held after DosBridge cleanup: {exc}')
        finally:
            probe.close()


if __name__ == '__main__':
    unittest.main()
