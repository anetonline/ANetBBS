"""Regression test for anetbbs/features/social_queue.py's orphaned-image
cleanup fix.

Real leak found in a security/performance audit: _queue() renders and
writes the PNG to data/social_posts/ BEFORE it ever commits the
SocialPost row. On the losing side of a race (two requests detecting
the same event -- e.g. two concurrent high scores landing on the same
game -- and both passing the initial "does this dedupe_key already
exist?" check before either has committed), the loser's db.session.
commit() hits the unique constraint on dedupe_key, gets rolled back,
and returns None -- but the image file it already wrote to disk was
never referenced by anything and, before this fix, was never cleaned
up either. Every lost race left one more orphaned PNG on disk forever
-- an unbounded, silent disk leak with no cap and no admin-visible
queue entry pointing at it.

This test simulates the race by pre-committing a real SocialPost row
under the target dedupe_key (the winner), then patching the dedupe
pre-check inside _queue() to report "no duplicate found" (so it
proceeds exactly as the losing request would have, right up to the
commit that then genuinely fails against the real unique constraint).
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class SocialQueueOrphanedImageCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent /
                          '.social_queue_orphan_test.db')
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
        # Isolate written PNGs under a tmp dir -- never the real repo's
        # data/social_posts/ -- so this test can't leave files behind.
        self._imgdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._imgdir.cleanup)
        self.app.config['INSTALL_DIR'] = self._imgdir.name

    def tearDown(self):
        with self.app.app_context():
            from anetbbs.models import db, SocialPost
            SocialPost.query.delete()
            db.session.commit()

    def _tiny_png_bytes(self):
        from io import BytesIO
        from PIL import Image
        buf = BytesIO()
        Image.new('RGB', (4, 4), (1, 2, 3)).save(buf, format='PNG')
        return buf.getvalue()

    def test_losing_a_dedupe_race_removes_the_orphaned_image(self):
        from anetbbs.features import social_queue
        from anetbbs.models import db, SocialPost

        with self.app.app_context():
            # The winner: a real row already committed under this key.
            db.session.add(SocialPost(
                trigger_kind='high_score', dedupe_key='race:dup',
                text='winner', status='pending'))
            db.session.commit()

            png = self._tiny_png_bytes()

            # Simulate the loser having already passed the initial
            # dedupe check (as it would have, moments before the
            # winner's own commit landed) by making that check report
            # "nothing found" regardless of the row above.
            with patch.object(social_queue.SocialPost, 'query') as mock_query:
                mock_query.filter_by.return_value.first.return_value = None
                result = social_queue._queue(
                    'race:dup', 'high_score', 'label', 'loser text', png)

            self.assertIsNone(result, 'a losing race must not return a row')

            # Only the winner's row exists.
            self.assertEqual(SocialPost.query.count(), 1)

            # No PNG the loser wrote is left behind: the images dir
            # should hold nothing (the winner never queued via _save_image
            # in this test, only the loser did).
            leftover = os.listdir(social_queue._images_dir())
            self.assertEqual(leftover, [],
                             f'orphaned image(s) left on disk: {leftover}')


if __name__ == '__main__':
    unittest.main()
