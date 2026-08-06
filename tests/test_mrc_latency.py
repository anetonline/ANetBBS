"""Real round-trip latency measurement (mrc/bridge/latency.py).

Deferred request from 2026-08-04 (memory: project_mrc_ping_latency_status_
swap.md): replace the terminal MRC status bar's clock with real latency,
add a latency indicator to the web UI's topic bar. Ported from the
vendored reference Mystic client rather than invented -- see latency.py's
module docstring for the mechanism (a registry of outbound packet text ->
send time, matched against inbound lines the hub echoes back verbatim).
"""
import asyncio
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrc.bridge.latency import LatencyTracker


def _run(coro):
    return asyncio.run(coro)


class LatencyTrackerTests(unittest.TestCase):
    def test_no_match_for_a_line_never_sent(self):
        t = LatencyTracker()
        self.assertFalse(t.check_received('SERVER~~~CLIENT~~~PING~'))
        self.assertIsNone(t.latency_ms)

    def test_matching_echo_computes_a_real_positive_latency(self):
        t = LatencyTracker()
        t.note_sent('CLIENT~MyBBS~~SERVER~~~IMALIVE:MyBBS~\n')
        time.sleep(0.02)
        matched = t.check_received('CLIENT~MyBBS~~SERVER~~~IMALIVE:MyBBS~')
        self.assertTrue(matched)
        self.assertIsNotNone(t.latency_ms)
        self.assertGreater(t.latency_ms, 0)
        self.assertLess(t.latency_ms, 5000)  # sane upper bound for a local test

    def test_match_clears_the_whole_registry_not_just_the_matched_entry(self):
        """Matches the reference client's own coarse semantics -- this is
        'time since our most recent unacknowledged send', not per-packet
        RTT. Documented behavior, not an accident."""
        t = LatencyTracker()
        t.note_sent('AAA~1~~SERVER~~~x~')
        t.note_sent('BBB~1~~SERVER~~~y~')
        t.check_received('AAA~1~~SERVER~~~x~')
        # BBB was never matched but the registry was cleared alongside AAA
        self.assertFalse(t.check_received('BBB~1~~SERVER~~~y~'))

    def test_trailing_newline_and_whitespace_do_not_prevent_a_match(self):
        t = LatencyTracker()
        t.note_sent('AAA~1~~SERVER~~~x~\n')
        self.assertTrue(t.check_received('  AAA~1~~SERVER~~~x~  '))

    def test_registry_is_bounded_and_evicts_oldest_first(self):
        t = LatencyTracker(max_entries=3)
        for i in range(5):
            t.note_sent(f'PKT{i}~1~~SERVER~~~x~')
        # PKT0 and PKT1 should have been evicted already
        self.assertFalse(t.check_received('PKT0~1~~SERVER~~~x~'))
        self.assertFalse(t.check_received('PKT1~1~~SERVER~~~x~'))
        self.assertTrue(t.check_received('PKT4~1~~SERVER~~~x~'))

    def test_empty_or_blank_packets_are_never_registered(self):
        t = LatencyTracker()
        t.note_sent('')
        t.note_sent('   ')
        self.assertFalse(t.check_received(''))


class MRCConnectionLatencyIntegrationTests(unittest.TestCase):
    def setUp(self):
        try:
            import aiohttp  # noqa: F401
        except ImportError:
            raise unittest.SkipTest('aiohttp not installed')

    def test_handle_packet_fires_latency_callback_on_echo_match(self):
        from mrc.bridge.main import MRCConnection
        conn = MRCConnection({'mrc_host': 'x', 'mrc_port': 1})
        seen = []

        async def cb(ms):
            seen.append(ms)
        conn.latency_callback = cb

        packet = 'StingRay~ANetBBS~lobby~SERVER~~lobby~IAMHERE~'

        async def drive():
            conn._latency.note_sent(packet)
            await conn._handle_packet(packet)
        _run(drive())

        self.assertEqual(len(seen), 1)
        self.assertIsInstance(seen[0], float)
        self.assertEqual(conn.latency_ms, seen[0])

    def test_handle_packet_does_not_fire_callback_for_unrelated_traffic(self):
        from mrc.bridge.main import MRCConnection
        conn = MRCConnection({'mrc_host': 'x', 'mrc_port': 1})
        seen = []

        async def cb(ms):
            seen.append(ms)
        conn.latency_callback = cb

        async def drive():
            await conn._handle_packet('SomeoneElse~OtherBBS~lobby~~~lobby~hey there~')
        _run(drive())

        self.assertEqual(seen, [])
        self.assertIsNone(conn.latency_ms)


class BridgeAppLatencyBroadcastTests(unittest.TestCase):
    def setUp(self):
        try:
            import aiohttp  # noqa: F401
        except ImportError:
            raise unittest.SkipTest('aiohttp not installed')

    def test_broadcast_latency_sends_rounded_ms_payload_to_all_sockets(self):
        import json
        import tempfile
        from mrc.bridge.main import BridgeApp

        with tempfile.TemporaryDirectory() as td:
            cfg = json.loads((Path(__file__).resolve().parents[1] /
                               'mrc' / 'bridge' / 'config.example.json').read_text())
            cfg['data_dir'] = td
            cfg_path = Path(td) / 'config.json'
            cfg_path.write_text(json.dumps(cfg))
            app = BridgeApp(str(cfg_path))

            sent = []

            class _FakeWs:
                async def send_json(self, payload):
                    sent.append(payload)

            app.websockets[1] = _FakeWs()
            app.websockets[2] = _FakeWs()

            _run(app._broadcast_latency(123.6))
            self.assertEqual(len(sent), 2)
            for payload in sent:
                self.assertEqual(payload, {"type": "latency", "ms": 124})


if __name__ == '__main__':
    unittest.main()
