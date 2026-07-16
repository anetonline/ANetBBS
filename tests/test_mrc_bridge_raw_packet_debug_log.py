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
import logging
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


if __name__ == '__main__':
    unittest.main()
