"""Regression test: anetbbs.rss.poller._import_one_feed() called
feedparser.parse() with no timeout at all -- checked feedparser's own
signature, it has no timeout= parameter, and nothing else bounded the
connect/read time either. Since _poll_loop fetches every active feed
ONE AT A TIME on a single dedicated background thread, a single slow
or deliberately-stalling feed server could hang that call
indefinitely, blocking every OTHER active feed's refresh for as long
as the hang lasted -- no upper bound at all. Found in a
security/performance audit.

Originally fixed via the socket.setdefaulttimeout() workaround, scoped
around the feedparser.parse() call itself. A LATER security/
performance audit round (2026-09-02) replaced that fetch mechanism
entirely -- feedparser.parse() used to be handed the raw feed URL,
which re-resolved the hostname independently at connect time,
reopening a DNS-rebinding SSRF gap (see test_rss_feed_url_ssrf.py).
The fix fetches via curl (pinned to the already-validated resolved
address) and hands feedparser the raw bytes instead, so the bound is
now curl's own `--max-time` flag rather than the socket-global
workaround -- this file was updated to match.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class RssPollerFetchTimeoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.rss_timeout_test.db')
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
        RssPollerFetchTimeoutTests._counter += 1
        n = RssPollerFetchTimeoutTests._counter
        with self.app.app_context():
            feed = RssFeed(name=f'Timeout Test Feed {n}', url=url, is_active=True)
            db.session.add(feed)
            db.session.commit()
            return feed.id

    def test_a_bounded_max_time_is_passed_to_curl(self):
        """Proves a timeout bound is actually applied to the fetch --
        the curl invocation must carry --max-time set to the expected
        bound, not run unbounded."""
        import anetbbs.rss.poller as poller_mod
        from anetbbs.models import RssFeed

        url = 'https://example.com/feed.xml'
        feed_id = self._make_feed(url)
        fake_result = mock.Mock(returncode=0, stdout=b'<rss></rss>', stderr=b'')

        with mock.patch('anetbbs.core.net_safety.resolve_safe_destination',
                        return_value=(2, ('93.184.216.34', 443), None)), \
             mock.patch('subprocess.run', return_value=fake_result) as mock_run, \
             mock.patch('feedparser.parse',
                        return_value=mock.Mock(bozo=False, entries=[], feed={})):
            poller_mod._import_one_feed(self.app, feed_id)

        args = mock_run.call_args[0][0]
        self.assertIn('--max-time', args)
        idx = args.index('--max-time')
        self.assertEqual(args[idx + 1], str(poller_mod._FEED_FETCH_TIMEOUT))
        with self.app.app_context():
            feed = RssFeed.query.get(feed_id)
            self.assertIsNone(feed.last_error)

    def test_curl_timeout_is_handled_gracefully_not_left_hanging(self):
        """When curl itself times out (subprocess.TimeoutExpired), the
        fetch must fail gracefully with a clear last_error -- not raise
        an unhandled exception that would kill the poller thread and
        block every other feed behind it."""
        import subprocess
        import anetbbs.rss.poller as poller_mod
        from anetbbs.models import RssFeed

        url = 'https://example.com/feed3.xml'
        feed_id = self._make_feed(url)

        with mock.patch('anetbbs.core.net_safety.resolve_safe_destination',
                        return_value=(2, ('93.184.216.34', 443), None)), \
             mock.patch('subprocess.run',
                        side_effect=subprocess.TimeoutExpired(cmd='curl', timeout=20)):
            count = poller_mod._import_one_feed(self.app, feed_id)

        self.assertEqual(count, 0)
        with self.app.app_context():
            feed = RssFeed.query.get(feed_id)
            self.assertIn('timed out', feed.last_error)


if __name__ == '__main__':
    unittest.main()
