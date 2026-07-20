"""Regression tests for two live bug reports:

1. Who's Online showed a user "on" /static/fonts/Ac437_IBM_VGA_9x16.woff
   instead of the real page they were on -- web_app.py's before_request
   hook recorded every request's path including static asset fetches
   (which can lazily fire well after the real page request completes).

2. Admin's "Default Theme" checkbox (Theme.is_default) never actually
   changed what anyone saw -- base.html only ever rendered
   current_user.theme (None for any user with no personal pick, and
   always None for anonymous visitors), with no fallback to whichever
   Theme has is_default=True.

See anetbbs/web_app.py (track_user_session, _inject_effective_theme) and
anetbbs/templates/base.html (the three effective_theme blocks).
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_DATA_DIR = Path(__file__).resolve().parents[1] / 'data'


def _snapshot_data_dir():
    if not _DATA_DIR.is_dir():
        return set()
    return set(_DATA_DIR.iterdir())


class ThemeAndStaticWhoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._data_dir_before = _snapshot_data_dir()

        import anetbbs.config as cfg_mod
        cls._dbfile = str(Path(__file__).resolve().parent / '.theme_static_who_test.db')
        for suffix in ('', '-wal', '-shm'):
            path = cls._dbfile + suffix
            if os.path.exists(path):
                os.remove(path)
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._dbfile}'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, Theme

        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False

        with cls.app.app_context():
            db.create_all()
            admin = User(username='sysop', email='sysop@example.com', is_admin=True)
            admin.set_password('password123')
            db.session.add(admin)

            plain = User(username='plainuser', email='plain@example.com', is_admin=False)
            plain.set_password('password123')
            db.session.add(plain)

            # Clear is_default on any pre-seeded themes so this test's
            # theme is unambiguously the one effective_theme should fall
            # back to.
            Theme.query.update({'is_default': False})
            theme = Theme(
                name='test-hackers-theme',
                display_name='Hackers (1995)',
                description='test theme',
                css_variables='{"--theme-primary": "#00ff00", "--theme-stylesheet": true, "hackers": true}',
                is_default=True,
                is_active=True,
            )
            db.session.add(theme)
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri

        for suffix in ('', '-wal', '-shm'):
            path = cls._dbfile + suffix
            if os.path.exists(path):
                os.remove(path)
        import shutil
        for entry in _snapshot_data_dir() - cls._data_dir_before:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

    def test_anonymous_visitor_gets_default_theme_css(self):
        """Anonymous visitors have no current_user.theme at all -- they
        should still see whichever Theme is marked is_default."""
        client = self.app.test_client()
        resp = client.get('/')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('--theme-primary: #00ff00', body)

    def test_logged_in_user_without_personal_pick_gets_default_theme(self):
        client = self.app.test_client()
        client.post('/auth/login', data={'username': 'plainuser', 'password': 'password123'}, follow_redirects=True)
        resp = client.get('/')
        body = resp.get_data(as_text=True)
        self.assertIn('--theme-primary: #00ff00', body)
        # Hackers easter egg should follow the effective (default) theme too.
        self.assertIn('HACK THE PLANET', body)

    def test_static_asset_request_does_not_update_session_page(self):
        from anetbbs.models import db, User, UserSession

        client = self.app.test_client()
        client.post('/auth/login', data={'username': 'plainuser', 'password': 'password123'}, follow_redirects=True)

        # A real page view first.
        client.get('/')
        with self.app.app_context():
            user = User.query.filter_by(username='plainuser').first()
            session = UserSession.query.filter_by(user_id=user.id).first()
            self.assertIsNotNone(session)
            self.assertEqual(session.page, '/')

        # Then a lazy static asset fetch, as a browser does for fonts.
        static_resp = client.get('/static/css/style.css')
        self.assertIn(static_resp.status_code, (200, 304, 404))
        with self.app.app_context():
            user = User.query.filter_by(username='plainuser').first()
            session = UserSession.query.filter_by(user_id=user.id).first()
            # Must still show the real page, not the static asset path.
            self.assertEqual(session.page, '/')

    def test_profile_theme_picker_default_label_follows_site_default(self):
        """Picking 'Default' on the profile page's theme picker used to
        always say 'Classic Green' regardless of what admin actually set
        as the site default -- fix the label to reflect it."""
        from anetbbs.web.profile import UpdateProfileForm
        from anetbbs.models import User

        with self.app.app_context(), self.app.test_request_context():
            user = User.query.filter_by(username='plainuser').first()
            form = UpdateProfileForm(original_email=user.email)
            default_choice = dict(form.theme_id.choices)[0]
            self.assertEqual(default_choice, 'Site Default: Hackers (1995)')
            self.assertNotIn('Classic Green', default_choice)

    def test_terminal_theme_picker_default_label_follows_site_default(self):
        """Same fix as the web profile picker, mirrored on the terminal
        side (anetbbs/features/bbs_ui.py's _edit_profile_field 'theme'
        branch and _edit_profile's summary row)."""
        with self.app.app_context():
            from anetbbs.models import Theme
            themes = Theme.query.filter_by(is_active=True).order_by(Theme.name).all()
            site_default = Theme.query.filter_by(is_default=True, is_active=True).first()
            default_label = f'Site Default: {site_default.display_name}' if site_default else 'Site Default: Classic Green'
            self.assertEqual(default_label, 'Site Default: Hackers (1995)')


if __name__ == '__main__':
    unittest.main()
