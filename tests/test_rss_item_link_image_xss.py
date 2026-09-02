"""Regression test for a real Medium finding from a security/
performance audit (2026-09-02): RssItem.link and RssItem.image_url are
rendered directly into href=/src= attributes in anetbbs/templates/rss/
item.html, feed.html, and river.html with no scheme validation --
unlike this app's own board-post linkifier (web/render_msg.py's
_linkify()), which only ever linkifies https?://. Both columns come
straight from external, publisher-controlled feed content (poller.py's
_extract_image_url() and the entry.get('link') assignment), not the
sysop, with no allowlist. A malicious or compromised RSS feed could
set an item's link to a javascript: URI; a logged-in user clicking the
article link would execute it same-origin.

Fixed by validating the scheme at ingest time in poller.py (both the
link assignment and every candidate _extract_image_url() considers)
via a shared _is_safe_http_url() helper, so every template rendering
these columns is protected without needing its own copy of the check.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class _FakeEntry(dict):
    """feedparser entries support both dict-style .get() and attribute
    access -- this stands in for the ones _extract_image_url() reads
    via getattr()."""
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


class IsSafeHttpUrlUnitTests(unittest.TestCase):
    def test_javascript_uri_is_rejected(self):
        from anetbbs.rss.poller import _is_safe_http_url
        self.assertFalse(_is_safe_http_url('javascript:alert(1)'))

    def test_data_uri_is_rejected(self):
        from anetbbs.rss.poller import _is_safe_http_url
        self.assertFalse(_is_safe_http_url('data:text/html,<script>alert(1)</script>'))

    def test_plain_http_and_https_are_accepted(self):
        from anetbbs.rss.poller import _is_safe_http_url
        self.assertTrue(_is_safe_http_url('http://example.com/x'))
        self.assertTrue(_is_safe_http_url('https://example.com/x'))

    def test_empty_or_none_is_rejected(self):
        from anetbbs.rss.poller import _is_safe_http_url
        self.assertFalse(_is_safe_http_url(''))
        self.assertFalse(_is_safe_http_url(None))


class ExtractImageUrlSchemeTests(unittest.TestCase):
    def test_javascript_uri_in_media_thumbnail_is_rejected(self):
        from anetbbs.rss.poller import _extract_image_url
        entry = _FakeEntry(media_thumbnail=[{'url': 'javascript:alert(1)'}])
        self.assertIsNone(_extract_image_url(entry, None))

    def test_javascript_uri_in_img_tag_is_rejected(self):
        from anetbbs.rss.poller import _extract_image_url
        entry = _FakeEntry()
        html = '<p>hi</p><img src="javascript:alert(1)">'
        self.assertIsNone(_extract_image_url(entry, html))

    def test_legitimate_https_thumbnail_still_works(self):
        from anetbbs.rss.poller import _extract_image_url
        entry = _FakeEntry(media_thumbnail=[{'url': 'https://cdn.example.com/x.jpg'}])
        self.assertEqual(_extract_image_url(entry, None),
                         'https://cdn.example.com/x.jpg')


class RssItemIngestXssTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.rss_xss_test.db')
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

    def test_malicious_feed_item_link_is_not_stored(self):
        import anetbbs.rss.poller as poller_mod
        from anetbbs.models import db, RssFeed, RssItem

        with self.app.app_context():
            feed = RssFeed(name='XSS Test Feed', url='https://example.com/feed.xml',
                           is_active=True)
            db.session.add(feed)
            db.session.commit()
            feed_id = feed.id

        fake_result = mock.Mock(returncode=0, stdout=b'<rss></rss>', stderr=b'')
        fake_entry = mock.Mock()
        fake_entry.get = lambda k, d=None: {
            'id': 'guid-1', 'title': 'Evil item',
            'link': 'javascript:alert(document.cookie)',
        }.get(k, d)
        fake_parsed = mock.Mock(bozo=False, feed={},
                                entries=[fake_entry])

        with mock.patch('anetbbs.core.net_safety.resolve_safe_destination',
                        return_value=(2, ('93.184.216.34', 443), None)), \
             mock.patch('subprocess.run', return_value=fake_result), \
             mock.patch('feedparser.parse', return_value=fake_parsed), \
             mock.patch('anetbbs.rss.poller._extract_image_url', return_value=None):
            poller_mod._import_one_feed(self.app, feed_id)

        with self.app.app_context():
            item = RssItem.query.filter_by(feed_id=feed_id, guid='guid-1').first()
            self.assertIsNotNone(item)
            self.assertNotIn('javascript:', item.link,
                             'a javascript: URI must never be stored as a link')
            self.assertEqual(item.link, '')


if __name__ == '__main__':
    unittest.main()
