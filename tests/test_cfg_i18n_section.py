"""Unit test for the new anetbbs-cfg Translations section
(anetbbs/cfg/sections/i18n.py), added in the gap-analysis follow-up
round (post-v1.0.77 Phase C) so console/SSH sysops get the same
MenuTranslation CRUD the web admin's /admin/translations page offers.
Same curses-free data-layer testing pattern as
test_cfg_sections_data_v2.py -- exercises the plain functions the
curses UI calls, not the curses UI itself.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class CfgI18nSectionTests(unittest.TestCase):
    def setUp(self):
        from anetbbs.web_app import create_app
        self.app = create_app('testing')
        self.ctx = self.app.app_context()
        self.ctx.push()
        from anetbbs.models import db, MenuTranslation
        db.session.query(MenuTranslation).filter(
            MenuTranslation.key.like('zz.%')).delete(synchronize_session=False)
        db.session.commit()

    def tearDown(self):
        from anetbbs.models import db
        db.session.remove()
        self.ctx.pop()

    def test_crud_round_trip(self):
        from anetbbs.cfg.sections import i18n as i18n_section

        row = i18n_section.create_translation(
            {'lang': 'es', 'key': 'zz.test.one', 'text': 'Uno'})
        self.assertIn(row.id, {r.id for r in i18n_section.list_translations()})

        i18n_section.update_translation(row, {'text': 'Dos'})
        self.assertEqual(row.text, 'Dos')

        i18n_section.delete_translation(row)
        self.assertNotIn(row.id, {r.id for r in i18n_section.list_translations()})

    def test_values_from_round_trips_into_run_form_shape(self):
        from anetbbs.cfg.sections import i18n as i18n_section
        row = i18n_section.create_translation(
            {'lang': 'fr', 'key': 'zz.test.two', 'text': 'Deux'})
        self.assertEqual(i18n_section.values_from(row),
                         {'lang': 'fr', 'key': 'zz.test.two', 'text': 'Deux'})
        i18n_section.delete_translation(row)


if __name__ == '__main__':
    unittest.main()
