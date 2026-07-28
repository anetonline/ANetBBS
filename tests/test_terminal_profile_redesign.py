"""Regression tests for the redesigned terminal profile editor
(anetbbs/features/bbs_ui.py: _edit_profile / _edit_profile_field),
added alongside the Node Monitor, broadened sysop menu, and MSP picker
in the same pass.

Same reasoning as the sibling test files in this batch: the lightbar
screens themselves aren't practically unit-testable without a full
session mock. What IS covered here: each field's round trip through the
User model (the same mutation _edit_profile_field's branches perform),
the theme_id 0-means-default convention matching web/profile.py, and
that password_hash is never among the summary-screen fields.
"""
import os
import sys
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_DATA_DIR = Path(__file__).resolve().parents[1] / 'data'


def _snapshot_data_dir():
    if not _DATA_DIR.is_dir():
        return set()
    return set(_DATA_DIR.iterdir())


def _fresh_app(db_path):
    import anetbbs.config as cfg_mod
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    os.environ['FLASK_ENV'] = 'testing'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    return app


class ProfileFieldListTests(unittest.TestCase):
    def test_password_hash_never_in_the_field_list(self):
        from anetbbs.features.bbs_ui import _PROFILE_TEXT_FIELDS
        attrs = [a for a, _l, _m in _PROFILE_TEXT_FIELDS]
        self.assertNotIn('password_hash', attrs)
        self.assertNotIn('password', attrs)

    def test_tagline_maxlen_matches_web_form(self):
        # web/profile.py's UpdateProfileForm caps tagline at Length(max=160).
        from anetbbs.features.bbs_ui import _PROFILE_TEXT_FIELDS
        by_attr = {a: m for a, _l, m in _PROFILE_TEXT_FIELDS}
        self.assertEqual(by_attr['tagline'], 160)

    def test_sixel_and_codepage_choice_values_match_model_columns(self):
        from anetbbs.features.bbs_ui import (_PROFILE_SIXEL_CHOICES,
                                              _PROFILE_CODEPAGE_CHOICES)
        sixel_values = {v for _l, v in _PROFILE_SIXEL_CHOICES}
        self.assertEqual(sixel_values, {'auto', 'forced_on', 'forced_off'})
        codepage_values = {v for _l, v in _PROFILE_CODEPAGE_CHOICES}
        self.assertEqual(codepage_values, {'cp437', 'utf8'})

    def test_cursor_choice_values_match_model_columns(self):
        from anetbbs.features.bbs_ui import _PROFILE_CURSOR_CHOICES
        cursor_values = {v for _l, v in _PROFILE_CURSOR_CHOICES}
        self.assertEqual(cursor_values, {'default', 'steady', 'spinning'})


class ProfileFieldRoundTripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._data_dir_before = _snapshot_data_dir()
        import anetbbs.config as cfg_mod
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._orig_flask_env = os.environ.get('FLASK_ENV')

    @classmethod
    def tearDownClass(cls):
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        if cls._orig_flask_env is None:
            os.environ.pop('FLASK_ENV', None)
        else:
            os.environ['FLASK_ENV'] = cls._orig_flask_env
        for entry in _snapshot_data_dir() - cls._data_dir_before:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _make_user(self, app):
        from anetbbs.models import db, User
        with app.app_context():
            u = User(username='alice', email='alice@example.com',
                     password_hash='x')
            db.session.add(u)
            db.session.commit()
            return u.id

    def test_text_field_round_trip(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'text.db'))
        uid = self._make_user(app)
        from anetbbs.models import db, User
        with app.app_context():
            u = User.query.get(uid)
            u.tagline = 'Sent from ANetBBS'[:160]
            db.session.commit()
            self.assertEqual(User.query.get(uid).tagline, 'Sent from ANetBBS')

    def test_bool_field_round_trip(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'bool.db'))
        uid = self._make_user(app)
        from anetbbs.models import db, User
        with app.app_context():
            u = User.query.get(uid)
            self.assertFalse(u.show_email)  # model default
            u.show_email = True
            db.session.commit()
            self.assertTrue(User.query.get(uid).show_email)

    def test_date_field_round_trip_and_clear(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'date.db'))
        uid = self._make_user(app)
        from anetbbs.models import db, User
        with app.app_context():
            u = User.query.get(uid)
            u.date_of_birth = date(1990, 1, 1)
            db.session.commit()
            self.assertEqual(User.query.get(uid).date_of_birth, date(1990, 1, 1))
            # Clearing (blank input) sets it back to None.
            u = User.query.get(uid)
            u.date_of_birth = None
            db.session.commit()
            self.assertIsNone(User.query.get(uid).date_of_birth)

    def test_theme_id_zero_means_default_matches_web_convention(self):
        """Same convention as web/profile.py: a 0/blank pick maps to
        theme_id=None ('Default'), not to a literal Theme row 0."""
        app = _fresh_app(str(Path(self._tmp.name) / 'theme.db'))
        uid = self._make_user(app)
        from anetbbs.models import db, User, Theme
        with app.app_context():
            theme = Theme(name='void', display_name='Void Signal',
                         css_variables='{}', is_active=True)
            db.session.add(theme)
            db.session.commit()
            tid = theme.id

            u = User.query.get(uid)
            u.theme_id = tid
            db.session.commit()
            self.assertEqual(User.query.get(uid).theme_id, tid)

            # Picking "Default (Classic Green)" -- _pick_choice returns
            # value=None for that row, and _edit_profile_field assigns
            # it straight through (u.theme_id = value).
            u = User.query.get(uid)
            u.theme_id = None
            db.session.commit()
            self.assertIsNone(User.query.get(uid).theme_id)

    def test_sixel_mode_round_trip(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'sixel.db'))
        uid = self._make_user(app)
        from anetbbs.models import db, User
        with app.app_context():
            u = User.query.get(uid)
            self.assertEqual(u.sixel_mode, 'auto')  # model default
            u.sixel_mode = 'forced_on'
            db.session.commit()
            self.assertEqual(User.query.get(uid).sixel_mode, 'forced_on')

    def test_cursor_style_round_trip(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'cursor.db'))
        uid = self._make_user(app)
        from anetbbs.models import db, User
        with app.app_context():
            u = User.query.get(uid)
            self.assertEqual(u.cursor_style, 'default')  # model default
            u.cursor_style = 'steady'
            db.session.commit()
            self.assertEqual(User.query.get(uid).cursor_style, 'steady')

    def test_codepage_and_language_round_trip(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'cplang.db'))
        uid = self._make_user(app)
        from anetbbs.models import db, User
        with app.app_context():
            u = User.query.get(uid)
            self.assertEqual(u.codepage, 'cp437')
            self.assertEqual(u.language, 'en')
            u.codepage = 'utf8'
            u.language = 'es'
            db.session.commit()
            refreshed = User.query.get(uid)
            self.assertEqual(refreshed.codepage, 'utf8')
            self.assertEqual(refreshed.language, 'es')


if __name__ == '__main__':
    unittest.main()
