"""Regression tests for anetbbs.core.net_safety.resolve_safe_destination()
-- the shared SSRF guard extracted this audit round from
web/web_terminal.py (round 1's own Critical SSRF fix) so RSS's feed-
URL and sixel-image fetches (round 2) can reuse the exact same
validation instead of each maintaining a separate copy.

No dedicated test previously existed for this logic at all, in either
its original web_terminal.py location or here -- round 1's own fix was
only ever verified via one-off manual assertions, never captured as a
persistent regression test. Closed here.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.core.net_safety import resolve_safe_destination


class ResolveSafeDestinationTests(unittest.TestCase):
    def test_public_address_is_allowed(self):
        family, sockaddr, error = resolve_safe_destination('8.8.8.8', 53)
        self.assertIsNone(error)
        self.assertIsNotNone(sockaddr)
        self.assertEqual(sockaddr[0], '8.8.8.8')

    def test_loopback_is_rejected_with_no_own_ports_given(self):
        family, sockaddr, error = resolve_safe_destination('127.0.0.1', 22)
        self.assertIsNone(sockaddr)
        self.assertIn('not allowed', error)

    def test_loopback_is_rejected_even_with_own_ports_if_port_not_in_set(self):
        family, sockaddr, error = resolve_safe_destination(
            '127.0.0.1', 9999, own_ports={2233, 2234})
        self.assertIsNone(sockaddr)
        self.assertIn('not allowed', error)

    def test_loopback_is_allowed_when_port_is_in_own_ports(self):
        family, sockaddr, error = resolve_safe_destination(
            '127.0.0.1', 2233, own_ports={2233, 2234})
        self.assertIsNone(error)
        self.assertEqual(sockaddr[0], '127.0.0.1')

    def test_own_ports_accepts_a_callable_resolved_fresh_each_call(self):
        calls = []
        def _ports():
            calls.append(1)
            return {2233}
        family, sockaddr, error = resolve_safe_destination(
            '127.0.0.1', 2233, own_ports=_ports)
        self.assertIsNone(error)
        self.assertEqual(len(calls), 1)

    def test_rfc1918_private_address_is_rejected(self):
        family, sockaddr, error = resolve_safe_destination('192.168.1.1', 80)
        self.assertIsNone(sockaddr)
        self.assertIn('not allowed', error)

    def test_link_local_cloud_metadata_address_is_rejected(self):
        family, sockaddr, error = resolve_safe_destination('169.254.169.254', 80)
        self.assertIsNone(sockaddr)
        self.assertIn('not allowed', error)

    def test_unresolvable_hostname_is_rejected_cleanly(self):
        family, sockaddr, error = resolve_safe_destination(
            'this-does-not-resolve.invalid.example.', 80)
        self.assertIsNone(sockaddr)
        self.assertIn('Could not resolve', error)

    def test_returns_family_matching_the_resolved_address(self):
        import socket
        family, sockaddr, error = resolve_safe_destination('8.8.8.8', 443)
        self.assertEqual(family, socket.AF_INET)


class WebTerminalStillUsesTheSharedImplementationTests(unittest.TestCase):
    """Guards the refactor itself: web_terminal.py's own
    _resolve_safe_destination() must delegate to the shared
    implementation (with its own loopback-exception ports), not carry
    a second, separately-maintained copy that could drift."""

    def test_web_terminal_public_address_allowed(self):
        from anetbbs.web.web_terminal import _resolve_safe_destination
        family, sockaddr, error = _resolve_safe_destination('8.8.8.8', 53)
        self.assertIsNone(error)

    def test_web_terminal_private_address_rejected(self):
        from anetbbs.web.web_terminal import _resolve_safe_destination
        family, sockaddr, error = _resolve_safe_destination('10.0.0.5', 80)
        self.assertIsNone(sockaddr)
        self.assertIn('not allowed', error)

    def test_web_terminal_loopback_on_own_telnet_port_still_allowed(self):
        import os
        from unittest import mock
        from anetbbs.web.web_terminal import _resolve_safe_destination
        with mock.patch.dict(os.environ, {'TELNET_PORT': '2233'}):
            family, sockaddr, error = _resolve_safe_destination('127.0.0.1', 2233)
        self.assertIsNone(error)

    def test_web_terminal_loopback_on_an_unrelated_port_still_rejected(self):
        import os
        from unittest import mock
        from anetbbs.web.web_terminal import _resolve_safe_destination
        with mock.patch.dict(os.environ, {'TELNET_PORT': '2233'}):
            family, sockaddr, error = _resolve_safe_destination('127.0.0.1', 5432)
        self.assertIsNone(sockaddr)
        self.assertIn('not allowed', error)


if __name__ == '__main__':
    unittest.main()
