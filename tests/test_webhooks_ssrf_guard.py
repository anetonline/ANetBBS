"""Regression test for a real finding from a security/performance audit
(2026-09-10): anetbbs/features/webhooks.py's _do_post() fired an
arbitrary POST to Webhook.url with NO SSRF guard at all -- unlike every
other outbound-connect path in this codebase (dialout.py, the RSS feed
poller, finger.py, msp/client.py, web_terminal.py, ebooks.py's Gutendex
text fetch). Reachable via the admin /admin/webhooks/ config, including
an already-compromised admin session -- same reasoning dialout.py's own
fix gives for why an admin-reachable-only destination still needs the
guard.

Fixed by validating the scheme and resolving the hostname once via the
shared core.net_safety.resolve_safe_destination() guard before ever
calling requests.post(), refusing private/loopback/link-local/reserved/
multicast targets (e.g. a cloud metadata endpoint or an internal admin
panel an attacker-controlled webhook URL could otherwise reach).
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.features.webhooks import _do_post


class WebhooksSsrfGuardTests(unittest.TestCase):
    def test_private_address_is_rejected_without_ever_posting(self):
        with mock.patch(
                'anetbbs.features.webhooks.resolve_safe_destination',
                return_value=(None, None,
                              'Connections to private/internal addresses are not allowed')
        ) as mock_resolve, \
                mock.patch('anetbbs.features.webhooks.requests.post') as mock_post:
            status, err = _do_post('http://169.254.169.254/latest/meta-data/',
                                    '{}', {})
        mock_resolve.assert_called_once_with('169.254.169.254', 80)
        mock_post.assert_not_called()
        self.assertEqual(status, 0)
        self.assertIn('not allowed', err)

    def test_non_http_scheme_is_rejected_without_ever_posting(self):
        with mock.patch('anetbbs.features.webhooks.requests.post') as mock_post:
            status, err = _do_post('file:///etc/passwd', '{}', {})
        mock_post.assert_not_called()
        self.assertEqual(status, 0)
        self.assertIn('http(s)', err)

    def test_public_address_still_posts_normally(self):
        """Confirms the fix doesn't just reject everything -- a real
        public destination still fires a normal requests.post() call."""
        fake_resp = mock.Mock(status_code=200)
        with mock.patch(
                'anetbbs.features.webhooks.resolve_safe_destination',
                return_value=(2, ('93.184.216.34', 443), None)), \
                mock.patch('anetbbs.features.webhooks.requests.post',
                           return_value=fake_resp) as mock_post:
            status, err = _do_post('https://example.com/hook', '{}',
                                    {'Content-Type': 'application/json'})
        mock_post.assert_called_once()
        self.assertEqual(status, 200)
        self.assertIsNone(err)


if __name__ == '__main__':
    unittest.main()
