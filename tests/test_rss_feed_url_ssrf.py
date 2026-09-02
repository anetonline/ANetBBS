"""Regression tests for anetbbs.rss.poller._import_one_feed()'s URL
validation -- real gap found in a security/performance audit:
feedparser.parse() was called directly on the raw feed.url with no
scheme/host validation at all. feedparser treats a bare path or a
file:// URI as a LOCAL FILE to read, not a network fetch -- confirmed
empirically (see test_feedparser_itself_would_read_a_local_file_...
below) both forms are read straight off disk. Reachable via the
feed-URL admin config field. Also had no restriction against an
http(s) URL targeting an internal-only address. Fixed by validating
scheme + resolving/checking the host via the shared
core.net_safety.resolve_safe_destination() before ever calling
feedparser.parse().
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class FeedparserLocalFileReadConfirmationTests(unittest.TestCase):
    """Confirms the underlying feedparser behavior the fix guards
    against is real, not a theoretical concern -- run once,
    independent of the app/DB fixtures below."""

    def test_feedparser_itself_would_read_a_local_file_given_a_bare_path(self):
        import feedparser
        with tempfile.NamedTemporaryFile(
                mode='w', suffix='.xml', delete=False) as f:
            f.write('<rss><channel><title>local secret</title></channel></rss>')
            path = f.name
        try:
            parsed = feedparser.parse(path)
            self.assertEqual(parsed.feed.get('title'), 'local secret',
                             'feedparser reads a bare path as a local file, '
                             'not a network URL -- the real behavior this '
                             'fix guards against')
        finally:
            os.unlink(path)


class RssFeedUrlSsrfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.rss_ssrf_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    _counter = 0

    def _make_feed(self, url):
        from anetbbs.models import db, RssFeed
        RssFeedUrlSsrfTests._counter += 1
        n = RssFeedUrlSsrfTests._counter
        with self.app.app_context():
            feed = RssFeed(name=f'Test Feed {n}', url=url, is_active=True)
            db.session.add(feed)
            db.session.commit()
            return feed.id

    def test_file_scheme_url_is_refused_before_any_fetch_attempt(self):
        from anetbbs.rss.poller import _import_one_feed
        from anetbbs.models import RssFeed
        feed_id = self._make_feed('file:///etc/passwd')
        count = _import_one_feed(self.app, feed_id)
        self.assertEqual(count, 0)
        with self.app.app_context():
            feed = RssFeed.query.get(feed_id)
            self.assertIn('http(s)', feed.last_error)

    def test_bare_path_url_is_refused_before_any_fetch_attempt(self):
        from anetbbs.rss.poller import _import_one_feed
        from anetbbs.models import RssFeed
        with tempfile.NamedTemporaryFile(
                mode='w', suffix='.xml', delete=False) as f:
            f.write('<rss><channel><title>should never be read</title></channel></rss>')
            path = f.name
        try:
            feed_id = self._make_feed(path)
            count = _import_one_feed(self.app, feed_id)
            self.assertEqual(count, 0)
            with self.app.app_context():
                feed = RssFeed.query.get(feed_id)
                self.assertIn('http(s)', feed.last_error)
        finally:
            os.unlink(path)

    def test_internal_ip_http_url_is_refused(self):
        from anetbbs.rss.poller import _import_one_feed
        from anetbbs.models import RssFeed
        feed_id = self._make_feed('http://169.254.169.254/latest/meta-data/')
        count = _import_one_feed(self.app, feed_id)
        self.assertEqual(count, 0)
        with self.app.app_context():
            feed = RssFeed.query.get(feed_id)
            self.assertIn('refused', feed.last_error)

    def test_private_lan_https_url_is_refused(self):
        from anetbbs.rss.poller import _import_one_feed
        from anetbbs.models import RssFeed
        feed_id = self._make_feed('https://192.168.1.1/feed.xml')
        count = _import_one_feed(self.app, feed_id)
        self.assertEqual(count, 0)
        with self.app.app_context():
            feed = RssFeed.query.get(feed_id)
            self.assertIn('refused', feed.last_error)

    def test_public_https_url_passes_validation_and_reaches_feedparser(self):
        """No real network access needed: mocks resolve_safe_destination
        to simulate a real public address passing the SSRF check, then
        confirms the fetch is pinned to that resolved address (via
        curl --resolve, not a re-resolve of the hostname at connect
        time -- see test_dns_rebinding_gap_is_closed_by_pinning_to_the_
        resolved_address below for the fix this specifically guards)
        and that feedparser.parse() is handed the fetched bytes --
        proving the validation code doesn't accidentally block (or get
        bypassed for) a legitimate public feed."""
        from unittest import mock
        import anetbbs.rss.poller as poller_mod
        from anetbbs.models import RssFeed
        url = 'https://example.com/feed.xml'
        feed_id = self._make_feed(url)
        fake_result = mock.Mock(returncode=0, stdout=b'<rss></rss>', stderr=b'')
        # _import_one_feed() does `from ..core.net_safety import
        # resolve_safe_destination` as a LOCAL import at call time, so
        # patching the real source attribute (not a module-level name
        # in poller.py, which doesn't have one) is what actually takes
        # effect -- same reasoning as patching 'feedparser.parse' below
        # working regardless of where `import feedparser` happens to run.
        with mock.patch('anetbbs.core.net_safety.resolve_safe_destination',
                        return_value=(2, ('93.184.216.34', 443), None)) as mock_resolve, \
             mock.patch('subprocess.run', return_value=fake_result) as mock_run, \
             mock.patch('feedparser.parse') as mock_parse:
            mock_parse.return_value = mock.Mock(bozo=False, entries=[], feed={})
            poller_mod._import_one_feed(self.app, feed_id)
        mock_resolve.assert_called_once()
        mock_run.assert_called_once()
        mock_parse.assert_called_once()
        called_bytes = mock_parse.call_args[0][0]
        self.assertEqual(called_bytes, fake_result.stdout,
                         'feedparser must receive the already-fetched bytes, '
                         'not a URL it would re-resolve itself')
        with self.app.app_context():
            feed = RssFeed.query.get(feed_id)
            self.assertIsNone(feed.last_error)

    def test_dns_rebinding_gap_is_closed_by_pinning_to_the_resolved_address(self):
        """Direct regression test for the real High finding (2026-09-02):
        resolve_safe_destination() validated the resolved IP, but the
        fetch itself used to re-resolve the hostname independently at
        connect time (feedparser.parse(url, ...) does its own DNS
        lookup) -- reopening the exact DNS-rebinding TOCTOU window
        resolving-once is supposed to close. Confirms the fetch is
        pinned via curl's --resolve host:port:ip to the SAME address
        resolve_safe_destination() validated, not re-resolved."""
        from unittest import mock
        import anetbbs.rss.poller as poller_mod
        url = 'https://example.com/rebind-check-feed.xml'
        feed_id = self._make_feed(url)
        fake_result = mock.Mock(returncode=0, stdout=b'<rss></rss>', stderr=b'')
        with mock.patch('anetbbs.core.net_safety.resolve_safe_destination',
                        return_value=(2, ('93.184.216.34', 443), None)), \
             mock.patch('subprocess.run', return_value=fake_result) as mock_run, \
             mock.patch('feedparser.parse',
                        return_value=mock.Mock(bozo=False, entries=[], feed={})):
            poller_mod._import_one_feed(self.app, feed_id)
        args = mock_run.call_args[0][0]
        self.assertIn('--resolve', args)
        resolve_idx = args.index('--resolve')
        self.assertEqual(args[resolve_idx + 1], 'example.com:443:93.184.216.34',
                         'curl must be pinned to the exact address '
                         'resolve_safe_destination() already validated')


if __name__ == '__main__':
    unittest.main()
