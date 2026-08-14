"""Regression tests for BBSMenuUI._rss_render_sixel()'s SSRF guard --
real gap found in a security/performance audit, follow-up to a pre-
release fix that only restricted the URL's SCHEME (http/https, closing
a file:// local-file-read vector) but left the underlying private/
internal-IP SSRF surface open, by its own admission ("residual risk
noted but not fully closed"). image_url comes from an individual RSS
item's own content -- entirely publisher-controlled, not the sysop's
feed-URL config -- so this is reachable by any regular subscriber to
any feed, not just admins. Fixed via the same shared
core.net_safety.resolve_safe_destination() guard used by
web_terminal.py and the RSS feed-URL fetch.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.features.bbs_ui import BBSMenuUI


class _FakeSession:
    def __init__(self):
        self.written = []

    async def write(self, text):
        self.written.append(text)


class RssSixelSsrfTests(unittest.TestCase):
    def setUp(self):
        self.ui = BBSMenuUI(_FakeSession())

    def _run(self, image_url):
        with mock.patch('shutil.which', return_value='/usr/bin/img2sixel'):
            return asyncio.run(self.ui._rss_render_sixel(image_url))

    def test_file_scheme_is_rejected(self):
        result = self._run('file:///etc/passwd')
        self.assertFalse(result)

    def test_internal_ip_http_url_is_rejected(self):
        result = self._run('http://169.254.169.254/latest/meta-data/')
        self.assertFalse(result)

    def test_private_lan_https_url_is_rejected(self):
        result = self._run('https://192.168.1.1/x.jpg')
        self.assertFalse(result)

    def test_loopback_url_is_rejected(self):
        """No own_ports exception for this feature at all -- unlike
        web_terminal.py, there's no legitimate reason an RSS item's
        image would ever point at this BBS's own service ports."""
        result = self._run('http://127.0.0.1:8080/x.jpg')
        self.assertFalse(result)

    def test_unresolvable_host_is_rejected_cleanly_not_an_exception(self):
        result = self._run('https://this-should-not-resolve.invalid.example/x.jpg')
        self.assertFalse(result)

    def test_public_url_passes_the_ssrf_check_and_reaches_the_fetch(self):
        """Mocks the actual network fetch (urlopen) to avoid needing
        real internet access -- proves the SSRF check passes for a
        genuine public address and execution reaches the download
        step, not that the whole feature works end-to-end."""
        with mock.patch('anetbbs.core.net_safety.resolve_safe_destination',
                        return_value=(2, ('93.184.216.34', 443), None)), \
             mock.patch('shutil.which', return_value='/usr/bin/img2sixel'), \
             mock.patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.side_effect = OSError('simulated network failure')
            result = asyncio.run(
                self.ui._rss_render_sixel('https://example.com/photo.jpg'))
        self.assertFalse(result)  # fetch itself failed, but...
        mock_urlopen.assert_called_once()  # ...proves we reached the fetch

    def test_no_scheme_at_all_is_rejected(self):
        result = self._run('example.com/x.jpg')
        self.assertFalse(result)


if __name__ == '__main__':
    unittest.main()
