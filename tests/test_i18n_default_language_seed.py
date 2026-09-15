"""Regression test for anetbbs/features/i18n.py's
seed_default_translations() -- the Spanish/German/Portuguese default
language packs Jerry asked to be seeded, covering the keys wired into
templates in this round (base.html's nav bar + Message Boards
heading). Wired into web_app.py's _create_default_data(), same
convention as achievements.ensure_seeded() and the default-boards/MOTD
seeding it runs alongside.

Covers: a fresh app seeds all 21 rows (3 languages x 7 keys); running
the seeder again is a no-op that doesn't duplicate rows or touch a
sysop's own edit.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod

from anetbbs.features.i18n import DEFAULT_TRANSLATIONS, seed_default_translations  # noqa: E402


class DefaultLanguageSeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp_db = str(Path(__file__).resolve().parent / '.i18n_default_seed_test.db')
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
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)

    def setUp(self):
        self.ctx = self.app.app_context()
        self.ctx.push()
        from anetbbs.models import db, MenuTranslation
        db.session.query(MenuTranslation).delete()
        db.session.commit()

    def tearDown(self):
        self.ctx.pop()

    def test_covers_exactly_spanish_german_portuguese(self):
        self.assertEqual(set(DEFAULT_TRANSLATIONS.keys()), {'es', 'de', 'pt'})

    def test_seeds_all_rows_on_a_fresh_database(self):
        from anetbbs.models import MenuTranslation
        seed_default_translations()
        expected = sum(len(keys) for keys in DEFAULT_TRANSLATIONS.values())
        self.assertEqual(MenuTranslation.query.count(), expected)
        for lang, keys in DEFAULT_TRANSLATIONS.items():
            for key, text in keys.items():
                row = MenuTranslation.query.filter_by(lang=lang, key=key).first()
                self.assertIsNotNone(row, f'missing seeded row for {lang}/{key}')
                self.assertEqual(row.text, text)

    def test_reseeding_does_not_duplicate_rows(self):
        from anetbbs.models import MenuTranslation
        seed_default_translations()
        count_after_first = MenuTranslation.query.count()
        seed_default_translations()
        self.assertEqual(MenuTranslation.query.count(), count_after_first)

    def test_reseeding_never_overwrites_a_sysop_edit(self):
        from anetbbs.models import db, MenuTranslation
        seed_default_translations()
        row = MenuTranslation.query.filter_by(lang='es', key='nav.home').first()
        row.text = 'Sysop Custom Value'
        db.session.commit()

        seed_default_translations()

        row = MenuTranslation.query.filter_by(lang='es', key='nav.home').first()
        self.assertEqual(row.text, 'Sysop Custom Value')


if __name__ == '__main__':
    unittest.main()
