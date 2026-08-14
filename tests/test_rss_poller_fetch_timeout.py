"""Regression test: anetbbs.rss.poller._import_one_feed() called
feedparser.parse() with no timeout at all -- checked feedparser's own
signature, it has no timeout= parameter, and nothing else bounded the
connect/read time either. Since _poll_loop fetches every active feed
ONE AT A TIME on a single dedicated background thread, a single slow
or deliberately-stalling feed server could hang that call
indefinitely, blocking every OTHER active feed's refresh for as long
as the hang lasted -- no upper bound at all. Found in a
security/performance audit.

Fixed via the standard socket.setdefaulttimeout() workaround (the only
one available since feedparser exposes no timeout parameter itself),
scoped tightly around just the feedparser.parse() call and restored
immediately afterward in a finally -- it's a process-global setting,
not thread-local, so this test also confirms it doesn't leak past the
call.
"""
import os
import socket
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

    def test_a_bounded_socket_timeout_is_active_during_the_fetch(self):
        """Proves a timeout is actually applied around the fetch --
        the mocked feedparser.parse() reads socket.getdefaulttimeout()
        from INSIDE the call to confirm it's set to the expected bound,
        not left at whatever the process default was."""
        import anetbbs.rss.poller as poller_mod
        from anetbbs.models import RssFeed

        url = 'https://example.com/feed.xml'
        feed_id = self._make_feed(url)

        observed = {}

        def _fake_parse(*args, **kwargs):
            observed['timeout_during_call'] = socket.getdefaulttimeout()
            return mock.Mock(bozo=False, entries=[], feed={})

        with mock.patch('anetbbs.core.net_safety.resolve_safe_destination',
                        return_value=(2, ('93.184.216.34', 443), None)), \
             mock.patch('feedparser.parse', side_effect=_fake_parse):
            poller_mod._import_one_feed(self.app, feed_id)

        self.assertEqual(observed.get('timeout_during_call'),
                         poller_mod._FEED_FETCH_TIMEOUT,
                         'a bounded timeout must be active for the '
                         'duration of the feedparser.parse() call')
        with self.app.app_context():
            feed = RssFeed.query.get(feed_id)
            self.assertIsNone(feed.last_error)

    def test_the_global_timeout_is_restored_after_the_fetch_completes(self):
        """The socket default timeout is process-global, not
        thread-local -- must not leak past this one fetch and affect
        unrelated code elsewhere in the process."""
        import anetbbs.rss.poller as poller_mod
        from anetbbs.models import RssFeed

        url = 'https://example.com/feed2.xml'
        feed_id = self._make_feed(url)

        original = socket.getdefaulttimeout()
        socket.setdefaulttimeout(None)
        try:
            with mock.patch('anetbbs.core.net_safety.resolve_safe_destination',
                            return_value=(2, ('93.184.216.34', 443), None)), \
                 mock.patch('feedparser.parse',
                           return_value=mock.Mock(bozo=False, entries=[], feed={})):
                poller_mod._import_one_feed(self.app, feed_id)

            self.assertIsNone(
                socket.getdefaulttimeout(),
                'the process-global socket default timeout must be '
                'restored to what it was before this fetch, not left '
                'set to the feed-fetch bound')
        finally:
            socket.setdefaulttimeout(original)

    def test_timeout_is_restored_even_when_feedparser_raises(self):
        """Guard against the finally: block being skipped on an
        exception path."""
        import anetbbs.rss.poller as poller_mod
        from anetbbs.models import RssFeed

        url = 'https://example.com/feed3.xml'
        feed_id = self._make_feed(url)

        original = socket.getdefaulttimeout()
        socket.setdefaulttimeout(None)
        try:
            with mock.patch('anetbbs.core.net_safety.resolve_safe_destination',
                            return_value=(2, ('93.184.216.34', 443), None)), \
                 mock.patch('feedparser.parse',
                           side_effect=OSError('simulated network failure')):
                count = poller_mod._import_one_feed(self.app, feed_id)

            self.assertEqual(count, 0)
            self.assertIsNone(socket.getdefaulttimeout())
            with self.app.app_context():
                feed = RssFeed.query.get(feed_id)
                self.assertIn('fetch failed', feed.last_error)
        finally:
            socket.setdefaulttimeout(original)


if __name__ == '__main__':
    unittest.main()
