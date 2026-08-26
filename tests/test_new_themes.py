"""Regression tests for the three new themes added alongside the MSP/
federation work: Graphite Teal and Ivory Editorial (two "professional"
themes -- distinct dark and light options with real typography, not
just palette swaps) and Retro Web '99 (a full Windows-95/GeoCities-era
pastiche via the same "--theme-stylesheet" escape hatch "enhanced" and
"hackers" already use).

Covers: the themes seed correctly, their css_variables are valid JSON,
each one's declared colors actually pass WCAG AA contrast (matching
this project's own stated bar -- "Audited for readability: every theme
passes WCAG AA on body text" -- verified programmatically, not just by
eye), and picking one as a user's theme loads the correct override
stylesheet.
"""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod

NEW_THEME_NAMES = ('graphite-teal', 'ivory-editorial', 'retro-web')

# Small, dependency-free WCAG 2.1 contrast-ratio implementation --
# matches the formula in the spec exactly (relative luminance +
# (L1+0.05)/(L2+0.05)), no external color library needed.
def _luminance(hex_color):
    hex_color = hex_color.lstrip('#')
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4))

    def chan(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = chan(r), chan(g), chan(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(fg, bg):
    l1, l2 = _luminance(fg), _luminance(bg)
    l1, l2 = max(l1, l2), min(l1, l2)
    return (l1 + 0.05) / (l2 + 0.05)


class NewThemesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.new_themes_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import User, Theme, db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            # Pull out plain dicts (not live ORM objects) so tests can
            # use them freely after this app_context/session closes.
            cls.themes = {}
            for name in NEW_THEME_NAMES:
                t = Theme.query.filter_by(name=name).first()
                cls.themes[name] = {'id': t.id, 'css_variables': t.css_variables}
            user = User(username='themeuser', email='themeuser@example.com',
                       is_active=True)
            user.set_password('password12345')
            db.session.add(user)
            db.session.commit()
            cls.user_id = user.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client_with_theme(self, theme_id):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = db.session.get(User, self.user_id)
            u.theme_id = theme_id
            db.session.commit()
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.user_id)
            sess['_fresh'] = True
        return client

    def test_all_three_themes_seeded(self):
        for name in NEW_THEME_NAMES:
            self.assertIsNotNone(self.themes[name]['id'], f'{name} was not seeded')

    def test_css_variables_are_valid_json_with_full_schema(self):
        required_keys = {
            '--theme-bg', '--theme-bg-dark', '--theme-primary',
            '--theme-primary-dark', '--theme-text', '--theme-text-muted',
            '--theme-card-bg', '--theme-input-bg', '--theme-input-focus',
            '--theme-border', '--theme-stylesheet',
        }
        for name, theme in self.themes.items():
            data = json.loads(theme['css_variables'])
            missing = required_keys - data.keys()
            self.assertFalse(missing, f'{name} missing keys: {missing}')

    def test_body_text_and_muted_text_pass_wcag_aa(self):
        """AA requires >= 4.5:1 for normal body text against the page
        background -- checked against both --theme-bg and
        --theme-card-bg, since real content sits on both surfaces."""
        for name, theme in self.themes.items():
            data = json.loads(theme['css_variables'])
            for surface in ('--theme-bg', '--theme-card-bg'):
                ratio = _contrast(data['--theme-text'], data[surface])
                self.assertGreaterEqual(
                    ratio, 4.5,
                    f'{name}: --theme-text on {surface} only {ratio:.2f}:1')
            # Muted text is allowed to run a little lower (it's
            # explicitly secondary), but still must clear AA.
            ratio = _contrast(data['--theme-text-muted'], data['--theme-bg'])
            self.assertGreaterEqual(
                ratio, 4.5,
                f'{name}: --theme-text-muted on --theme-bg only {ratio:.2f}:1')

    def test_primary_accent_readable_against_background(self):
        for name, theme in self.themes.items():
            data = json.loads(theme['css_variables'])
            ratio = _contrast(data['--theme-primary'], data['--theme-bg'])
            self.assertGreaterEqual(
                ratio, 3.0,
                f'{name}: --theme-primary on --theme-bg only {ratio:.2f}:1')

    def test_each_theme_loads_its_own_stylesheet_and_none_other(self):
        stylesheets = {
            'graphite-teal': 'graphite_teal_theme.css',
            'ivory-editorial': 'ivory_editorial_theme.css',
            'retro-web': 'retro_web_theme.css',
        }
        for name, css_file in stylesheets.items():
            body = self._client_with_theme(self.themes[name]['id']).get('/').data.decode()
            self.assertIn(css_file, body, f'{name} did not load {css_file}')
            others = set(stylesheets.values()) - {css_file}
            for other in others:
                self.assertNotIn(other, body,
                                 f'{name} incorrectly also loaded {other}')

    def test_retro_theme_stylesheet_file_has_period_correct_touches(self):
        """Sanity check the actual CSS file, not just the DB row --
        confirms the escape-hatch file matches what the theme claims."""
        css_path = (Path(__file__).resolve().parent.parent /
                   'anetbbs' / 'static' / 'css' / 'retro_web_theme.css')
        css = css_path.read_text()
        self.assertIn('Times New Roman', css)
        self.assertIn('#0000ee', css.lower())  # classic hyperlink blue
        self.assertIn(':visited', css)


if __name__ == '__main__':
    unittest.main()
