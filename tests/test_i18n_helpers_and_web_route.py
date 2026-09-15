"""Regression test for the i18n follow-up work (gap-analysis round,
post-v1.0.77 Phase C): anetbbs/features/i18n.py's shared translation-
lookup helpers, and the new web-UI t() Jinja global + /admin/
translations CRUD route that consume them.

menu_engine.py's own pre-existing MenuTranslation behavior is already
covered by tests/test_menu_translation.py and is unchanged here except
for calling the new shared helper instead of its own inline query --
that test file passing unmodified is itself part of this change's
verification.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class I18nHelpersTests(unittest.TestCase):
    """Direct tests of anetbbs/features/i18n.py, no Flask request cycle."""

    @classmethod
    def setUpClass(cls):
        cls._tmp_db = str(Path(__file__).resolve().parent / '.i18n_helpers_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, MenuTranslation
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            db.session.query(MenuTranslation).delete()
            db.session.add(MenuTranslation(lang='es', key='nav.home', text='Inicio'))
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)

    def setUp(self):
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    def test_english_or_falsy_lang_is_a_pure_no_op(self):
        from anetbbs.features.i18n import get_translations, get_translation
        self.assertEqual(get_translations('en', ['nav.home']), {})
        self.assertEqual(get_translations(None, ['nav.home']), {})
        self.assertEqual(get_translations('', ['nav.home']), {})
        self.assertEqual(get_translation('en', 'nav.home', 'Home'), 'Home')
        self.assertEqual(get_translation(None, 'nav.home', 'Home'), 'Home')

    def test_found_key_returns_the_override(self):
        from anetbbs.features.i18n import get_translations, get_translation
        self.assertEqual(get_translations('es', ['nav.home']), {'nav.home': 'Inicio'})
        self.assertEqual(get_translation('es', 'nav.home', 'Home'), 'Inicio')

    def test_missing_key_falls_back_to_default(self):
        from anetbbs.features.i18n import get_translations, get_translation
        self.assertEqual(get_translations('es', ['no.such.key']), {})
        self.assertEqual(get_translation('es', 'no.such.key', 'Fallback'), 'Fallback')

    def test_batched_lookup_mixes_found_and_missing_keys(self):
        from anetbbs.features.i18n import get_translations
        result = get_translations('es', ['nav.home', 'no.such.key'])
        self.assertEqual(result, {'nav.home': 'Inicio'})

    def test_empty_keys_list_is_a_no_op(self):
        from anetbbs.features.i18n import get_translations
        self.assertEqual(get_translations('es', []), {})


class AdminTranslationsRouteTests(unittest.TestCase):
    """Real Flask test-client coverage of /admin/translations and the
    t() Jinja global end to end (board list page's translated nav
    item), same shape as the manual smoke test this was verified
    against before being committed."""

    @classmethod
    def setUpClass(cls):
        cls._tmp_db = str(Path(__file__).resolve().parent / '.i18n_route_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, MenuTranslation
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()
            if not User.query.filter_by(username='i18nadmin').first():
                admin = User(username='i18nadmin', email='i18nadmin@example.com',
                            is_admin=True, language='es')
                admin.set_password('testpass123')
                db.session.add(admin)
            db.session.query(MenuTranslation).filter_by(key='nav.home').delete()
            db.session.add(MenuTranslation(lang='es', key='nav.home', text='Inicio'))
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)

    def setUp(self):
        self.client = self.app.test_client()
        self.client.post('/auth/login',
                         data={'username': 'i18nadmin', 'password': 'testpass123'},
                         follow_redirects=True)

    def test_translations_admin_page_lists_seeded_row(self):
        resp = self.client.get('/admin/translations')
        self.assertEqual(resp.status_code, 200)
        body = resp.data.decode('utf-8')
        self.assertIn('nav.home', body)
        self.assertIn('Inicio', body)

    def test_board_list_page_shows_translated_nav_home(self):
        resp = self.client.get('/boards/')
        self.assertEqual(resp.status_code, 200)
        body = resp.data.decode('utf-8')
        self.assertIn('Inicio', body)

    def test_add_edit_delete_round_trip(self):
        from anetbbs.models import MenuTranslation
        with self.app.app_context():
            MenuTranslation.query.filter_by(lang='es', key='test.roundtrip').delete()
            import anetbbs.models as m
            m.db.session.commit()

        resp = self.client.post('/admin/translations', data={
            'action': 'add', 'lang': 'es', 'key': 'test.roundtrip', 'text': 'Uno',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            row = MenuTranslation.query.filter_by(lang='es', key='test.roundtrip').first()
            self.assertIsNotNone(row)
            self.assertEqual(row.text, 'Uno')
            row_id = row.id

        self.client.post('/admin/translations', data={
            'action': 'edit', 'row_id': row_id, 'text': 'Dos',
        })
        with self.app.app_context():
            self.assertEqual(MenuTranslation.query.get(row_id).text, 'Dos')

        self.client.post('/admin/translations', data={
            'action': 'delete', 'row_id': row_id,
        })
        with self.app.app_context():
            self.assertIsNone(MenuTranslation.query.get(row_id))

    def test_duplicate_lang_key_is_rejected_not_duplicated(self):
        resp = self.client.post('/admin/translations', data={
            'action': 'add', 'lang': 'es', 'key': 'nav.home', 'text': 'Should not land',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        from anetbbs.models import MenuTranslation
        with self.app.app_context():
            count = MenuTranslation.query.filter_by(lang='es', key='nav.home').count()
            self.assertEqual(count, 1)
            self.assertEqual(
                MenuTranslation.query.filter_by(lang='es', key='nav.home').first().text,
                'Inicio')


if __name__ == '__main__':
    unittest.main()
