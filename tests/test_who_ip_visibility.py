"""Regression test for a real Medium-severity finding from a security/
performance audit (2026-08-31): web/who.py's index() route populated
every UserSession row's real IP address into the template context
unconditionally, for ANY logged-in user (not admin-gated at the route
level) -- unlike web/watch.py's public no-login activity ticker, which
deliberately never leaks IPs. templates/who/index.html already happens
to only RENDER the IP column for admins (confirmed: the rendered HTML
response is identical before and after this fix, since the template's
own `{% if current_user.is_admin %}` guard already prevented it from
reaching the page) -- but the ROUTE was still handing the raw IP data
into the response context regardless of who was asking. This is
defense in depth, not a fix for something reachable through the web
UI today: server-side data scoping shouldn't depend solely on a
template guard that could be removed, or on the data never being
exposed a different way (a JSON API variant, a debug endpoint, a
future template change). Tests the underlying data passed to
render_template() directly (via app.jinja_env's own context, captured
through a template-render hook), since the rendered HTML doesn't
discriminate before/after this fix.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class WhoIpVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.who_ip_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, UserSession
        from datetime import datetime
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()
            admin = User(username='whoiptestadmin', email='wia@example.com',
                        is_admin=True, is_active=True)
            admin.set_password('adminpassword123')
            regular = User(username='whoiptestregular', email='wir@example.com',
                           is_active=True)
            regular.set_password('regularpassword123')
            target = User(username='whoiptesttarget', email='wit@example.com',
                          is_active=True)
            target.set_password('targetpassword123')
            db.session.add_all([admin, regular, target])
            db.session.commit()
            cls.admin_id = admin.id
            cls.regular_id = regular.id

            sess = UserSession(user_id=target.id, session_key='real-session-token',
                               ip_address='203.0.113.42', page='[web]',
                               last_seen=datetime.utcnow())
            db.session.add(sess)
            db.session.commit()

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

    def _rows_passed_to_template(self, user_id):
        """Captures the actual `rows` context render_template() was
        called with, via Flask's template_rendered signal -- proves
        what data REACHES the template layer, independent of whatever
        that template chooses to render from it."""
        from flask import template_rendered
        captured = {}

        def _record(sender, template, context, **extra):
            captured['rows'] = context.get('rows')

        client = self._client_as(user_id)
        with template_rendered.connected_to(_record, self.app):
            resp = client.get('/who/')
        self.assertEqual(resp.status_code, 200)
        return captured.get('rows')

    def test_regular_user_never_receives_the_real_ip_in_template_context(self):
        rows = self._rows_passed_to_template(self.regular_id)
        self.assertIsNotNone(rows)
        ips = {r['ip'] for r in rows}
        self.assertNotIn('203.0.113.42', ips,
                         'a non-admin must never receive another user\'s real '
                         'IP address, even in data the template ultimately '
                         'chooses not to render')

    def test_admin_still_receives_the_real_ip_in_template_context(self):
        rows = self._rows_passed_to_template(self.admin_id)
        self.assertIsNotNone(rows)
        ips = {r['ip'] for r in rows}
        self.assertIn('203.0.113.42', ips,
                      'an admin must still receive real IPs')


if __name__ == '__main__':
    unittest.main()
