"""Regression test for the MRC bridge's diagnostic raw-packet tracing,
added while chasing a live "still have to /identify every single time"
report that survived three rounds of wire-format fixes verified against
both reference client source and the actual official protocol spec.
Static analysis alone wasn't enough -- this gives a way to capture a
full real connect/identify/leave/rejoin transcript for direct
comparison, gated behind MRC_BRIDGE_LOG_LEVEL=DEBUG (not the default
INFO level) since it would otherwise mean every private chat message
lands in plaintext in the server's own logs permanently.
"""
import asyncio
import importlib
import logging
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrc.bridge.main import MRCConnection


def _run(coro):
    return asyncio.run(coro)


class RawPacketDebugLogTests(unittest.TestCase):
    def _make_conn(self):
        conn = object.__new__(MRCConnection)
        conn.connected = False
        conn.writer = None
        conn._send_queue = __import__('collections').deque()
        conn._send_queue_max = 100
        return conn

    def test_send_packet_logs_raw_out_at_debug_level(self):
        conn = self._make_conn()
        with self.assertLogs('mrc_bridge', level='DEBUG') as cm:
            _run(conn.send_packet('Alice~TestBBS~lobby~SERVER~~lobby~MOTD~\n'))
        self.assertTrue(any('MRC RAW OUT' in line for line in cm.output),
                        'send_packet must log the exact outgoing packet '
                        'at DEBUG level for diagnostic tracing')
        self.assertTrue(any('MOTD' in line for line in cm.output))


class LogLevelSurvivesPreexistingRootHandlerTests(unittest.TestCase):
    """Real bug found live: setting MRC_BRIDGE_LOG_LEVEL=DEBUG via a
    systemd override and restarting produced zero "MRC RAW" lines.
    logging.basicConfig() is a documented no-op if the root logger
    already has a handler attached (e.g. from aiohttp or anything else
    that configured logging first under systemd) -- only an explicit
    logger.setLevel() on this module's own logger is guaranteed to take
    effect regardless of import order."""

    def setUp(self):
        self._orig_handlers = list(logging.root.handlers)
        self._orig_root_level = logging.root.level
        self._orig_env = os.environ.get('MRC_BRIDGE_LOG_LEVEL')

    def tearDown(self):
        logging.root.handlers = self._orig_handlers
        logging.root.setLevel(self._orig_root_level)
        if self._orig_env is None:
            os.environ.pop('MRC_BRIDGE_LOG_LEVEL', None)
        else:
            os.environ['MRC_BRIDGE_LOG_LEVEL'] = self._orig_env
        import mrc.bridge.main as m
        importlib.reload(m)

    def test_debug_level_applies_even_with_a_preexisting_root_handler(self):
        # Simulate the real systemd scenario: something else already
        # attached a handler to the root logger before this module's
        # own logging.basicConfig() call runs, making it a no-op.
        logging.root.addHandler(logging.NullHandler())
        os.environ['MRC_BRIDGE_LOG_LEVEL'] = 'DEBUG'

        import mrc.bridge.main as m
        importlib.reload(m)

        self.assertEqual(
            logging.getLogger('mrc_bridge').getEffectiveLevel(), logging.DEBUG,
            'MRC_BRIDGE_LOG_LEVEL=DEBUG must take effect on the mrc_bridge '
            'logger even when basicConfig() itself is a no-op')


if __name__ == '__main__':
    unittest.main()
