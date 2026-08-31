"""Regression test for a real Critical finding from a security/
performance audit (2026-08-31): postcards.py's save() route stored a
client-supplied grid width/height straight into Postcard.grid_json
with NO clamping -- unlike create(), which clamps to [20,100]/[5,40].
features/ansi_png.py's render_grid_png() -- reached by the PUBLIC,
no-login /postcards/<slug>.png route -- reads width/height back OUT of
grid_json (not the Postcard.width/height columns) and allocates an
Image.new() sized width*_CELL_W by height*_CELL_H plus a width*height
per-cell Python loop. Any logged-in user (not admin-only) could save
an arbitrarily large grid once, then any anonymous visitor hitting the
.png link triggers the memory/CPU blowup -- same failure class as the
v1.0.54 OOM incident.

A first-draft fix that only clamped the Postcard.width/height DB
columns did NOT actually close this, since render_grid_png() never
reads those columns -- it reads grid_json directly. This test
specifically checks grid_json (not the columns) to guard against that
exact regression.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class PostcardDimensionClampTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.postcard_clamp_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, Postcard
        import json
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()
            u = User(username='postcardtester', email='pct@example.com',
                     is_active=True)
            u.set_password('testerpassword123')
            db.session.add(u)
            db.session.commit()
            cls.user_id = u.id

            grid = {'width': 20, 'height': 5,
                    'cells': [{'c': ' ', 'fg': 15, 'bg': 1} for _ in range(100)]}
            card = Postcard(name='Test Card', slug='test-card-clamp',
                            width=20, height=5, grid_json=json.dumps(grid),
                            ansi_text='', created_by_id=u.id)
            db.session.add(card)
            db.session.commit()
            cls.slug = card.slug

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def test_oversized_dimensions_are_clamped_in_grid_json_not_just_columns(self):
        import json
        from anetbbs.models import db, Postcard

        client = self._client_as(self.user_id)
        resp = client.post(
            f'/postcards/{self.slug}/save',
            json={'name': 'Huge', 'grid': {
                'width': 999999, 'height': 999999,
                'cells': [{'c': 'X', 'fg': 15, 'bg': 1}],
            }})
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            refreshed = db.session.get(Postcard, Postcard.query.filter_by(
                slug=self.slug).first().id)
            stored_grid = json.loads(refreshed.grid_json)
            self.assertLessEqual(stored_grid['width'], 100,
                                 'grid_json width must be clamped -- this is what '
                                 'render_grid_png() actually reads for the public '
                                 '.png route, not the Postcard.width column')
            self.assertLessEqual(stored_grid['height'], 40,
                                 'grid_json height must be clamped')
            self.assertLessEqual(refreshed.width, 100)
            self.assertLessEqual(refreshed.height, 40)

    def test_normal_sized_dimensions_pass_through_unchanged(self):
        import json
        from anetbbs.models import db, Postcard

        client = self._client_as(self.user_id)
        resp = client.post(
            f'/postcards/{self.slug}/save',
            json={'name': 'Normal', 'grid': {
                'width': 40, 'height': 15,
                'cells': [{'c': ' ', 'fg': 15, 'bg': 1} for _ in range(600)],
            }})
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            refreshed = db.session.get(Postcard, Postcard.query.filter_by(
                slug=self.slug).first().id)
            stored_grid = json.loads(refreshed.grid_json)
            self.assertEqual(stored_grid['width'], 40)
            self.assertEqual(stored_grid['height'], 15)

    def test_png_route_actually_stays_bounded_after_a_malicious_save(self):
        """End-to-end: save an oversized grid, then confirm the public
        no-login .png route (the actual attacker-triggerable step)
        renders a small, bounded image rather than attempting a huge
        allocation."""
        client = self._client_as(self.user_id)
        client.post(f'/postcards/{self.slug}/save',
                    json={'name': 'Huge', 'grid': {
                        'width': 50000, 'height': 50000,
                        'cells': [{'c': 'X', 'fg': 15, 'bg': 1}],
                    }})

        anon = self.app.test_client()
        resp = anon.get(f'/postcards/{self.slug}.png')
        self.assertEqual(resp.status_code, 200)
        # A clamped 100x40 grid at scale=2 is well under 100000 bytes;
        # an unclamped 50000x50000 render would be gigabytes and would
        # have timed out or OOM'd this test process long before
        # returning at all.
        self.assertLess(len(resp.data), 2 * 1024 * 1024)


if __name__ == '__main__':
    unittest.main()
