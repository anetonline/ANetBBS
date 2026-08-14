"""Regression test: nothing ever pruned RssItem rows -- every RSS poll
cycle (default every 30 minutes) can add up to 200 new items per
active feed, deduped by (feed_id, guid) so no duplicate re-inserts,
but old items just accumulated forever. Over a long-running install
with several active feeds this table grows unboundedly. Found in a
security/performance audit.

Fixed with _prune_old_items(), called once per poll cycle, deleting
items older than RSS_ITEM_RETENTION_DAYS (env var, default 90) --
falling back to fetched_at for items with no published_at. Also
deletes matching RssReadStatus rows explicitly first, since a bulk
.delete() query bypasses SQLAlchemy's own ORM-level cascade (that only
fires for db.session.delete(obj), not a bulk query) and this project
never turns on SQLite's own FK enforcement (PRAGMA foreign_keys) that
the model's ondelete='CASCADE' declaration would otherwise rely on.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class RssItemRetentionPruningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.rss_retention_test.db')
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

    def setUp(self):
        from anetbbs.models import db, RssFeed
        with self.app.app_context():
            RssFeed.query.delete()
            db.session.commit()
        self.orig_retention = os.environ.get('RSS_ITEM_RETENTION_DAYS')
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self.orig_retention is None:
            os.environ.pop('RSS_ITEM_RETENTION_DAYS', None)
        else:
            os.environ['RSS_ITEM_RETENTION_DAYS'] = self.orig_retention

    def _make_feed(self):
        from anetbbs.models import db, RssFeed
        with self.app.app_context():
            feed = RssFeed(name='Retention Test Feed',
                           url=f'https://example.com/{id(self)}.xml',
                           is_active=True)
            db.session.add(feed)
            db.session.commit()
            return feed.id

    def _add_item(self, feed_id, guid, published_at=None, fetched_at=None):
        from anetbbs.models import db, RssItem
        with self.app.app_context():
            item = RssItem(feed_id=feed_id, guid=guid, title=guid,
                           published_at=published_at,
                           fetched_at=fetched_at or datetime.utcnow())
            db.session.add(item)
            db.session.commit()
            return item.id

    def test_old_items_are_deleted_recent_items_survive(self):
        os.environ['RSS_ITEM_RETENTION_DAYS'] = '30'
        from anetbbs.rss.poller import _prune_old_items
        from anetbbs.models import RssItem

        feed_id = self._make_feed()
        old_id = self._add_item(
            feed_id, 'old-item',
            published_at=datetime.utcnow() - timedelta(days=60))
        recent_id = self._add_item(
            feed_id, 'recent-item',
            published_at=datetime.utcnow() - timedelta(days=5))

        _prune_old_items(self.app)

        with self.app.app_context():
            remaining_ids = {i.id for i in RssItem.query.all()}
        self.assertNotIn(old_id, remaining_ids,
                         'an item older than the retention window must be pruned')
        self.assertIn(recent_id, remaining_ids,
                      'an item within the retention window must survive')

    def test_items_with_no_published_at_fall_back_to_fetched_at(self):
        os.environ['RSS_ITEM_RETENTION_DAYS'] = '30'
        from anetbbs.rss.poller import _prune_old_items
        from anetbbs.models import RssItem

        feed_id = self._make_feed()
        old_id = self._add_item(
            feed_id, 'old-no-pubdate', published_at=None,
            fetched_at=datetime.utcnow() - timedelta(days=60))
        recent_id = self._add_item(
            feed_id, 'recent-no-pubdate', published_at=None,
            fetched_at=datetime.utcnow() - timedelta(days=5))

        _prune_old_items(self.app)

        with self.app.app_context():
            remaining_ids = {i.id for i in RssItem.query.all()}
        self.assertNotIn(old_id, remaining_ids)
        self.assertIn(recent_id, remaining_ids)

    def test_pruning_an_item_also_removes_its_read_status_rows(self):
        """Guard against orphaned RssReadStatus rows -- a bulk delete
        bypasses the ORM-level cascade the model declares."""
        os.environ['RSS_ITEM_RETENTION_DAYS'] = '30'
        from anetbbs.rss.poller import _prune_old_items
        from anetbbs.models import db, RssItem, RssReadStatus, User

        feed_id = self._make_feed()
        old_id = self._add_item(
            feed_id, 'old-with-read-status',
            published_at=datetime.utcnow() - timedelta(days=60))

        with self.app.app_context():
            u = User(username='rss_retention_reader',
                    email='rss_retention_reader@example.com',
                    password_hash='x')
            db.session.add(u)
            db.session.commit()
            db.session.add(RssReadStatus(user_id=u.id, item_id=old_id))
            db.session.commit()
            self.assertEqual(
                RssReadStatus.query.filter_by(item_id=old_id).count(), 1)

        _prune_old_items(self.app)

        with self.app.app_context():
            self.assertIsNone(RssItem.query.get(old_id))
            self.assertEqual(
                RssReadStatus.query.filter_by(item_id=old_id).count(), 0,
                'pruning an item must not leave an orphaned read-status row')

    def test_retention_disabled_when_set_to_zero_or_negative(self):
        os.environ['RSS_ITEM_RETENTION_DAYS'] = '0'
        from anetbbs.rss.poller import _prune_old_items
        from anetbbs.models import RssItem

        feed_id = self._make_feed()
        old_id = self._add_item(
            feed_id, 'ancient-item',
            published_at=datetime.utcnow() - timedelta(days=3650))

        _prune_old_items(self.app)

        with self.app.app_context():
            self.assertIsNotNone(RssItem.query.get(old_id),
                                 'a retention window of 0 must disable pruning entirely')

    def test_pruning_failure_is_caught_not_propagated(self):
        """The whole poll loop must never crash just because pruning
        hit a DB error -- matches every other best-effort cleanup in
        this codebase."""
        os.environ['RSS_ITEM_RETENTION_DAYS'] = '30'
        from anetbbs.rss.poller import _prune_old_items
        from anetbbs.models import db

        feed_id = self._make_feed()
        # A real stale item so pruning actually has work to do -- an
        # empty stale_ids list returns early, before ever reaching
        # commit(), which would make the mock below untested.
        self._add_item(feed_id, 'will-fail-to-prune',
                       published_at=datetime.utcnow() - timedelta(days=60))
        with mock.patch.object(db.session, 'commit',
                               side_effect=RuntimeError('simulated DB error')):
            try:
                _prune_old_items(self.app)
            except Exception as exc:  # pragma: no cover
                self.fail(f'_prune_old_items must not raise, got: {exc!r}')


if __name__ == '__main__':
    unittest.main()
