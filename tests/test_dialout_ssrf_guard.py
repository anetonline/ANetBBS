"""Regression test for a real High finding from a security/performance
audit (2026-09-02): anetbbs/features/dialout.py's DialoutMenu._connect()
dialed `asyncio.open_connection(host, port)` directly with NO SSRF
guard at all -- unlike every other outbound-connect path in this
codebase (web_terminal.py, finger.py, msp/client.py, the RSS feed-URL/
sixel-image fetches). Worse than a typical SSRF: _proxy() is a full
bidirectional raw-byte relay with telnet IAC negotiation, giving genuine
interactive access to whatever's on the other end -- and the seeded
Dial Out menu item has no min_access override, so it reaches every
logged-in user, not just admins.

Fixed by resolving the destination once via the shared
core.net_safety.resolve_safe_destination() guard (same one used
elsewhere) and connecting to the pinned resolved address rather than
re-resolving the hostname at connect time, which would reopen the
DNS-rebinding TOCTOU gap the guard exists to close. Applied at the
shared _connect() chokepoint, not just _custom_destination(), so it
also covers sysop-configured directory entries.
"""
import asyncio
import socket
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.features.dialout import DialoutMenu


class _FakeSession:
    def __init__(self, inputs=None):
        self.written = []
        self._inputs = iter(inputs or [])

    async def write(self, text):
        self.written.append(text)

    async def read_line(self, prompt=''):
        try:
            return next(self._inputs)
        except StopIteration:
            return ''


class DialoutSsrfGuardTests(unittest.TestCase):
    def test_private_address_is_rejected_without_ever_connecting(self):
        session = _FakeSession()
        menu = DialoutMenu(session)
        with mock.patch(
                'anetbbs.core.net_safety.resolve_safe_destination',
                return_value=(None, None,
                              'Connections to private/internal addresses are not allowed')
        ) as mock_resolve, \
                mock.patch('asyncio.open_connection') as mock_open:
            asyncio.run(menu._connect('Internal', '192.168.1.1', 80, 'telnet'))
        mock_resolve.assert_called_once_with('192.168.1.1', 80)
        mock_open.assert_not_called()
        self.assertTrue(any('not allowed' in w for w in session.written), session.written)

    def test_cloud_metadata_address_via_custom_destination_is_rejected(self):
        """_custom_destination() (the free-text host:port entry every
        logged-in user can reach, per the finding) funnels into the same
        _connect() -- confirm the guard is hit from that entry point
        too, not just a direct _connect() call."""
        session = _FakeSession(inputs=['169.254.169.254', ''])
        menu = DialoutMenu(session)
        with mock.patch(
                'anetbbs.core.net_safety.resolve_safe_destination',
                return_value=(None, None,
                              'Connections to private/internal addresses are not allowed')), \
                mock.patch('asyncio.open_connection') as mock_open:
            asyncio.run(menu._custom_destination())
        mock_open.assert_not_called()

    def test_public_address_connects_to_the_pinned_resolved_address_not_the_hostname(self):
        """Confirms the fix doesn't just reject everything -- a real
        public destination still connects, using the resolved sockaddr
        (not re-resolving the hostname at connect time)."""
        session = _FakeSession()
        menu = DialoutMenu(session)
        fake_reader = mock.AsyncMock()
        fake_writer = mock.AsyncMock()
        fake_writer.close = mock.MagicMock()
        with mock.patch(
                'anetbbs.core.net_safety.resolve_safe_destination',
                return_value=(socket.AF_INET, ('93.184.216.34', 23), None)), \
                mock.patch('asyncio.open_connection',
                           mock.AsyncMock(return_value=(fake_reader, fake_writer))) as mock_open, \
                mock.patch.object(DialoutMenu, '_proxy', mock.AsyncMock()) as mock_proxy:
            asyncio.run(menu._connect('Some BBS', 'example.com', 23, 'telnet'))
        mock_open.assert_called_once_with('93.184.216.34', 23)
        mock_proxy.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
