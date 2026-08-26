"""Regression tests for the MSP-subsystem SSRF findings from the
2026-08-26 security audit (never previously targeted by ANetBBS's two
full-codebase audits).

Three independent, previously-unguarded code paths could make the
server originate a raw TCP or UDP connection at an attacker-influenced
host with no validation: send_msp() (outbound MSP, reachable by any
logged-in user via /imsg/send and the terminal IM menu), query_systat()
(reachable by any logged-in user via /imsg/directory/<id>/who, and by
the automatic background peer-probe cycle), and peer_health.py's
_systat_probe() (a SEPARATE, independently-written probe used only by
the admin "Probe now" button -- found while verifying the audit's own
citation, which pointed at query_systat() for this one but the button
actually calls its own unguarded implementation).

All three are fixed the same way: anetbbs/core/net_safety.py's
resolve_safe_destination(), already proven out on web_terminal.py/RSS
feed fetches in prior audits -- resolve once, reject private/loopback/
link-local/reserved/multicast, connect to the resolved address (not
the original hostname string, to avoid a DNS-rebinding TOCTOU gap).
"""
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.msp.client import send_msp
from anetbbs.msp.systat import query_systat
from anetbbs.web.peer_health import _systat_probe


PRIVATE_TARGETS = [
    '127.0.0.1',      # loopback
    '10.1.2.3',        # RFC1918
    '192.168.1.1',     # RFC1918
    '169.254.169.254',  # link-local / cloud metadata
]


class SendMspSsrfGuardTests(unittest.TestCase):
    def test_refuses_private_and_loopback_targets(self):
        for host in PRIVATE_TARGETS:
            with self.subTest(host=host):
                ok = send_msp(host=host, recipient='someone', message='hi')
                self.assertFalse(ok, f'send_msp should have refused {host}')

    def test_never_opens_a_socket_for_a_refused_destination(self):
        """Belt-and-suspenders: confirm the refusal happens BEFORE any
        connection attempt, not just that the overall call fails."""
        with patch('socket.socket') as mock_socket:
            ok = send_msp(host='127.0.0.1', recipient='x', message='hi')
        self.assertFalse(ok)
        mock_socket.assert_not_called()


class QuerySystatSsrfGuardTests(unittest.TestCase):
    def test_refuses_private_and_loopback_targets(self):
        for host in PRIVATE_TARGETS:
            with self.subTest(host=host):
                result = query_systat(host)
                self.assertEqual(result, '', f'query_systat should have refused {host}')

    def test_never_opens_a_socket_for_a_refused_destination(self):
        with patch('socket.socket') as mock_socket:
            result = query_systat('127.0.0.1')
        self.assertEqual(result, '')
        mock_socket.assert_not_called()

    def test_still_works_for_a_real_public_style_resolution(self):
        """Sanity check the guard doesn't just refuse everything --
        localhost resolving to a loopback address is exactly the
        rejection case; confirm the function still returns '' (not an
        exception) rather than accidentally always-True/always-False."""
        result = query_systat('localhost')
        self.assertEqual(result, '')


class PeerHealthProbeSsrfGuardTests(unittest.TestCase):
    """peer_health.py's admin 'Probe now' button had its own separate,
    unguarded TCP+UDP implementation -- not routed through
    query_systat() despite doing the same job. Confirmed and fixed
    independently; test it independently too."""

    def test_refuses_private_and_loopback_targets(self):
        for host in PRIVATE_TARGETS:
            with self.subTest(host=host):
                ok, ms, detail = _systat_probe(host, 11, timeout=1.0)
                self.assertFalse(ok, f'_systat_probe should have refused {host}')
                self.assertIn('private', detail.lower())

    def test_never_opens_a_socket_for_a_refused_destination(self):
        with patch('socket.socket') as mock_socket:
            ok, ms, detail = _systat_probe('127.0.0.1', 11)
        self.assertFalse(ok)
        mock_socket.assert_not_called()


if __name__ == '__main__':
    unittest.main()
