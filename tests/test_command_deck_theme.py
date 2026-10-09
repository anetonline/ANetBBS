"""Regression tests for the Command Deck theme -- the first theme that
changes the page SHAPE (a fixed icon rail instead of a top navbar, a
mobile slide-in drawer, and a phone bottom tab bar) rather than just
recoloring the same Bootstrap navbar/card skeleton every other theme
reskins. See docs/08-themes.md and anetbbs/static/css/
command_deck_theme.css's own header comment for the full design.

Follows tests/test_new_themes.py's own established pattern (seeding,
JSON schema, WCAG AA contrast, correct-stylesheet-loads) and adds
coverage specific to this theme's two real markup dependencies:
`data-bs-display="static"` on the dropdown toggles (needed so
Bootstrap's Popper positioning doesn't fight the CSS flyout placement)
and the new, additive, theme-gated #cdMobileTabbar block -- both live
in base.html, not in the CSS file, so they need their own checks.
"""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


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


class CommandDeckThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.command_deck_theme_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import User, Theme, db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            t = Theme.query.filter_by(name='command-deck').first()
            cls.theme = {'id': t.id if t else None,
                        'css_variables': t.css_variables if t else None}
            user = User(username='cddeckuser', email='cddeckuser@example.com',
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

    def test_theme_seeded(self):
        self.assertIsNotNone(self.theme['id'], 'command-deck was not seeded')

    def test_css_variables_valid_json_with_full_schema(self):
        required_keys = {
            '--theme-bg', '--theme-bg-dark', '--theme-primary',
            '--theme-primary-dark', '--theme-text', '--theme-text-muted',
            '--theme-card-bg', '--theme-input-bg', '--theme-input-focus',
            '--theme-border', '--theme-stylesheet',
        }
        data = json.loads(self.theme['css_variables'])
        missing = required_keys - data.keys()
        self.assertFalse(missing, f'missing keys: {missing}')
        self.assertEqual(data['--theme-stylesheet'], 'command-deck')

    def test_body_and_muted_text_pass_wcag_aa(self):
        data = json.loads(self.theme['css_variables'])
        for surface in ('--theme-bg', '--theme-card-bg'):
            ratio = _contrast(data['--theme-text'], data[surface])
            self.assertGreaterEqual(ratio, 4.5,
                f'--theme-text on {surface} only {ratio:.2f}:1')
        ratio = _contrast(data['--theme-text-muted'], data['--theme-bg'])
        self.assertGreaterEqual(ratio, 4.5,
            f'--theme-text-muted on --theme-bg only {ratio:.2f}:1')

    def test_primary_accent_readable(self):
        data = json.loads(self.theme['css_variables'])
        ratio = _contrast(data['--theme-primary'], data['--theme-bg'])
        self.assertGreaterEqual(ratio, 3.0,
            f'--theme-primary on --theme-bg only {ratio:.2f}:1')

    def test_loads_its_own_stylesheet_and_no_other_themes_stylesheet(self):
        body = self._client_with_theme(self.theme['id']).get('/').data.decode()
        self.assertIn('command_deck_theme.css', body)
        for other in ('hackers_theme.css', 'enhanced_theme.css',
                     'graphite_teal_theme.css', 'ivory_editorial_theme.css',
                     'retro_web_theme.css'):
            self.assertNotIn(other, body, f'incorrectly also loaded {other}')

    def test_page_renders_the_mobile_tabbar_only_for_this_theme(self):
        """The additive #cdMobileTabbar block must appear when Command
        Deck is active and be completely absent for every other theme
        -- it's meant to be zero bytes for everyone else."""
        with_cd = self._client_with_theme(self.theme['id']).get('/').data.decode()
        self.assertIn('id="cdMobileTabbar"', with_cd)

        from anetbbs.models import Theme
        with self.app.app_context():
            default_theme = Theme.query.filter_by(is_default=True).first()
        without_cd = self._client_with_theme(default_theme.id).get('/').data.decode()
        self.assertNotIn('id="cdMobileTabbar"', without_cd)

    def test_dropdown_toggles_have_static_display_for_the_flyout_css(self):
        """data-bs-display="static" makes Bootstrap skip Popper's own
        inline-transform positioning, which would otherwise fight this
        theme's CSS flyout placement -- harmless for every other theme,
        but required for this one. Checked against the real rendered
        page, not just the source template, so a future template
        refactor that silently drops it gets caught here."""
        body = self._client_with_theme(self.theme['id']).get('/').data.decode()
        toggle_count = body.count('data-bs-toggle="dropdown"')
        static_count = body.count('data-bs-display="static"')
        self.assertGreater(toggle_count, 0)
        self.assertEqual(static_count, toggle_count,
            f'{toggle_count} dropdown toggles but only {static_count} '
            'have data-bs-display="static"')

    def test_stylesheet_file_has_the_real_design_markers(self):
        """Sanity check the actual CSS file, not just the DB row."""
        css_path = (Path(__file__).resolve().parent.parent /
                   'anetbbs' / 'static' / 'css' / 'command_deck_theme.css')
        css = css_path.read_text()
        self.assertIn('Sora', css)
        self.assertIn('IBM+Plex+Mono', css)
        self.assertIn('#cdMobileTabbar', css)
        self.assertIn('data-bs-display', css)  # documented in the header comment
        # The flyout-positioning rule this theme's whole mechanism
        # depends on -- a regression here would silently break every
        # dropdown's placement.
        self.assertIn('left: 100% !important', css)

    def test_nav_position_defaults_to_left(self):
        """A fresh user, not the shared cls.user_id -- other tests in
        this class mutate that one's nav_position, and since unittest
        runs methods in alphabetical order, a test checking the
        pristine default must not depend on running before them."""
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User(username='cddeckdefaultuser',
                     email='cddeckdefaultuser@example.com', is_active=True)
            u.set_password('password12345')
            db.session.add(u)
            db.session.commit()
            self.assertEqual(u.nav_position, 'left')

    def test_body_renders_nav_position_attribute(self):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = db.session.get(User, self.user_id)
            u.nav_position = 'bottom'
            db.session.commit()
        body = self._client_with_theme(self.theme['id']).get('/').data.decode()
        self.assertIn('data-nav-position="bottom"', body)

    def test_guest_gets_left_regardless_of_any_saved_preference(self):
        """An anonymous (not-logged-in) request has no current_user row
        to read a preference from -- must fall back to 'left' cleanly,
        not raise on a missing attribute."""
        client = self.app.test_client()
        body = client.get('/').data.decode()
        self.assertIn('data-nav-position="left"', body)

    def test_profile_edit_saves_nav_position(self):
        """Real end-to-end POST through /profile/edit, same path a
        user's browser actually takes -- not just setting the column
        directly, so a form-wiring regression (missing field, wrong
        name attribute) would be caught here."""
        client = self._client_with_theme(self.theme['id'])
        resp = client.post('/profile/edit', data={
            'email': 'cddeckuser@example.com',
            'theme_id': str(self.theme['id']),
            'nav_position': 'top',
            'sixel_mode': 'auto',
            'cursor_style': 'default',
            'echomail_name_pref': 'handle',
            'msg_scan_pref': 'ask',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        from anetbbs.models import db, User
        with self.app.app_context():
            u = db.session.get(User, self.user_id)
            self.assertEqual(u.nav_position, 'top')

    def test_edit_page_ships_js_to_hide_nav_position_for_other_themes(self):
        """Real confusion found live (Pi3 screenshot): the field used
        to be shown unconditionally with only a small caption, which
        looked like a working setting for whatever theme happened to
        be selected (e.g. Graphite Teal) even though it only affects
        Command Deck's own layout. Checks the real rendered page for
        the hide-by-default field plus the sync script that reveals it
        only when Command Deck's own theme id is selected -- not just
        that the field exists somewhere in the markup."""
        body = self._client_with_theme(self.theme['id']).get('/profile/edit').data.decode()
        self.assertIn('id="navPositionField" style="display:none;"', body)
        self.assertIn('COMMAND_DECK_THEME_ID', body)
        self.assertIn(f'var COMMAND_DECK_THEME_ID = {self.theme["id"]};', body)

    def test_all_four_positions_have_distinct_flyout_direction_css(self):
        """Each position must open its dropdowns in the direction that
        actually has room -- right/top default to opening away from
        their own edge, left/bottom-pinned-group flip. A copy-paste
        mistake between blocks would silently point a flyout off-
        screen again, the exact bug class already found live once."""
        css_path = (Path(__file__).resolve().parent.parent /
                   'anetbbs' / 'static' / 'css' / 'command_deck_theme.css')
        css = css_path.read_text()
        self.assertIn('body[data-nav-position="right"] .navbar .dropdown-menu {', css)
        self.assertIn('right: 100% !important;', css)
        self.assertIn('body[data-nav-position="top"] .navbar .dropdown-menu {', css)
        self.assertIn('top: 100% !important; bottom: auto !important;', css)
        self.assertIn('body[data-nav-position="bottom"] .navbar .dropdown-menu {', css)
        self.assertIn('bottom: 100% !important; top: auto !important;', css)

    def test_account_group_flyout_has_its_own_top_bottom_override(self):
        """Real sysop screenshot (2026-10-09): with nav position set to
        top, every item in the Account dropdown except the bottommost
        ("Logout") rendered off-screen above the viewport -- only
        logout was visible. Root cause: the vertical rail's own
        bottom-pinned-account-group rule
        (`.navbar .navbar-collapse > ul.navbar-nav:last-child
        .dropdown-menu { top: auto !important; bottom: 0 !important; }`,
        needed there since that group sits at the bottom of a vertical
        list) has 5 class-level selectors, beating the generic
        `body[data-nav-position="top"] .navbar .dropdown-menu`
        override's 3 -- !important only breaks ties on EQUAL
        specificity, so the vertical-rail rule kept winning even in
        top/bottom mode and anchored the menu to grow upward from a
        bottom:0 point near the top of a 64px-tall bar, pushing almost
        everything off-screen. Fixed with a same-selector-shape
        override scoped to each position, matching or exceeding that
        specificity. Verified with a real headless-Chrome render
        before/after (not just this text check) -- see the fix's own
        commit/changelog entry."""
        css_path = (Path(__file__).resolve().parent.parent /
                   'anetbbs' / 'static' / 'css' / 'command_deck_theme.css')
        css = css_path.read_text()
        self.assertIn(
            'body[data-nav-position="top"] .navbar .navbar-collapse > '
            'ul.navbar-nav:last-child .dropdown-menu {', css)
        self.assertIn(
            'body[data-nav-position="bottom"] .navbar .navbar-collapse > '
            'ul.navbar-nav:last-child .dropdown-menu {', css)

    def test_top_and_bottom_use_a_horizontal_bar_not_a_vertical_rail(self):
        css_path = (Path(__file__).resolve().parent.parent /
                   'anetbbs' / 'static' / 'css' / 'command_deck_theme.css')
        css = css_path.read_text()
        self.assertIn('flex-direction: row;', css)
        self.assertIn('height: 64px;', css)


if __name__ == '__main__':
    unittest.main()
