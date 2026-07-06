"""Tests for tools/post_tech_news.py -- the standalone daily tech-news
poster script (bbs.a-net.fyi-specific, not a general ANetBBS feature;
see the script's own docstring). Mocks feedparser so this never needs
real network access.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


def _fake_entry(title, link, summary):
    e = MagicMock()
    e.title = title
    e.link = link
    e.summary = summary
    e.description = summary
    return e


class PostTechNewsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.tech_news_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app, _create_default_data
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            _create_default_data()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _fake_feed(self, entries):
        feed = MagicMock()
        feed.bozo = False
        feed.entries = entries
        feed.feed = {'title': 'Fake Tech Feed'}
        return feed

    def test_posts_to_both_ann_tech_area_rows(self):
        from anetbbs.models import EchomailMessage
        from tools.post_tech_news import main as post_main

        entries = [_fake_entry('Story One', 'https://example.com/1', 'Summary one')]
        with self.app.app_context():
            with patch('feedparser.parse', return_value=self._fake_feed(entries)), \
                 patch('anetbbs.web_app.create_app', return_value=self.app), \
                 patch('sys.argv', ['post_tech_news']):
                post_main()

            msgs = EchomailMessage.query.filter_by(from_name='Tech News Bot').all()
            self.assertEqual(len(msgs), 2)  # one per ANN.TECH row (BinkP + QWK)
            network_ids = {m.network_id for m in msgs}
            self.assertEqual(len(network_ids), 2)
            for m in msgs:
                self.assertEqual(m.subject, 'Story One')
                self.assertIn('https://example.com/1', m.body)
                self.assertEqual(m.direction, 'outbound')

    def test_second_run_does_not_repost_same_story(self):
        from anetbbs.models import EchomailMessage
        from tools.post_tech_news import main as post_main

        entries = [_fake_entry('Story Two', 'https://example.com/2', 'Summary two')]
        with self.app.app_context():
            with patch('feedparser.parse', return_value=self._fake_feed(entries)), \
                 patch('anetbbs.web_app.create_app', return_value=self.app), \
                 patch('sys.argv', ['post_tech_news']):
                post_main()
                post_main()  # second run, same entry

            msgs = EchomailMessage.query.filter(
                EchomailMessage.body.like('%example.com/2%')).all()
            self.assertEqual(len(msgs), 2)  # still just 2 (one per area), not 4

    def test_dry_run_does_not_write_to_db(self):
        from anetbbs.models import EchomailMessage
        from tools.post_tech_news import main as post_main

        entries = [_fake_entry('Story Three', 'https://example.com/3', 'Summary three')]
        with self.app.app_context():
            with patch('feedparser.parse', return_value=self._fake_feed(entries)), \
                 patch('anetbbs.web_app.create_app', return_value=self.app), \
                 patch('sys.argv', ['post_tech_news', '--dry-run']):
                post_main()

            msgs = EchomailMessage.query.filter(
                EchomailMessage.body.like('%example.com/3%')).all()
            self.assertEqual(len(msgs), 0)


if __name__ == '__main__':
    unittest.main()
