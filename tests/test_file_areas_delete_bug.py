"""Regression test: deleting a file area in the admin UI reported success
but never actually deleted the row.

Root cause: each area's row was a single <form> containing both a hidden
<input name="action" value="update"> (present unconditionally, near the
top of the form) and a Delete button also named "action" with
value="delete". Browsers submit ALL same-named fields, and
Werkzeug/Flask's MultiDict.get('action') returns the FIRST match in POST
body order -- the hidden field, always 'update' -- regardless of which
button was actually clicked. Clicking Delete silently re-ran the Save/
update branch (which flashes "Updated {tag}." -- easy to mistake for a
real confirmation) instead of the (otherwise perfectly correct)
db.session.delete() branch.

Fixed by removing the standalone hidden field and moving
name="action" value="update" onto the Save button itself, matching how
the Delete button already worked -- only whichever button was actually
clicked submits its own action value now.
"""
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod

TEMPLATE_PATH = (Path(__file__).resolve().parents[1] /
                 'anetbbs' / 'templates' / 'admin' / 'file_areas.html')


class FileAreaDeleteBugTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.file_areas_delete_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _admin_client(self):
        from anetbbs.models import db, User
        with self.app.app_context():
            admin = User.query.filter_by(username='fileareasdeltest').first()
            if not admin:
                admin = User(username='fileareasdeltest', is_admin=True,
                            access_level=255,
                            email='fileareasdeltest@example.com')
                admin.set_password('x')
                db.session.add(admin)
                db.session.commit()
            admin_id = admin.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(admin_id)
            sess['_fresh'] = True
        return client

    def test_template_never_emits_a_second_action_field(self):
        """Structural check for the actual root cause: the PER-AREA form
        (the one with both Save and Delete buttons) must not contain a
        standalone hidden 'action' input alongside those buttons' own
        name="action" values -- that combination is exactly what caused
        Delete to silently behave like Save. (The separate Create-area
        form at the top of the page legitimately has its own single
        hidden action="create" field with no competing button -- this
        check is scoped to the per-area block, not the whole file.)"""
        html = TEMPLATE_PATH.read_text()
        # Isolate the per-area form: starts at the loop's <form ...>, ends
        # at its closing </form> (the first one, since each area repeats
        # the same structure inside the {% for fa in areas %} block).
        start = html.index('{% for fa in areas %}')
        end = html.index('{% endfor %}', start)
        per_area_block = html[start:end]

        self.assertNotIn('type="hidden" name="action"', per_area_block,
                          'a hidden action field in the per-area form would always '
                          'be submitted alongside whichever button was clicked, '
                          'reintroducing the delete-does-not-delete bug')
        self.assertIn('name="action" value="update"', per_area_block)
        self.assertIn('name="action" value="delete"', per_area_block)

    def test_delete_action_actually_removes_the_row(self):
        from anetbbs.models import db, FileArea
        with self.app.app_context():
            fa = FileArea(tag='DELTEST', name='Delete Test Area',
                          is_active=True, is_subscribed=True)
            db.session.add(fa)
            db.session.commit()
            area_id = fa.id

        client = self._admin_client()
        resp = client.post('/admin/file-areas',
                           data={'action': 'delete', 'area_id': str(area_id)},
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            self.assertIsNone(FileArea.query.get(area_id),
                              'area should be gone after a delete POST')

    def test_update_action_still_saves_changes(self):
        """Guard against overcorrecting: Save must still work after moving
        its action value onto the button itself."""
        from anetbbs.models import db, FileArea
        with self.app.app_context():
            fa = FileArea(tag='UPDTEST', name='Original Name',
                          is_active=True, is_subscribed=True)
            db.session.add(fa)
            db.session.commit()
            area_id = fa.id

        client = self._admin_client()
        resp = client.post('/admin/file-areas',
                           data={'action': 'update', 'area_id': str(area_id),
                                 'name': 'Renamed Area',
                                 'upload_permission': 'users'},
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            fa = FileArea.query.get(area_id)
            self.assertIsNotNone(fa, 'update must not delete the row')
            self.assertEqual(fa.name, 'Renamed Area')


if __name__ == '__main__':
    unittest.main()
