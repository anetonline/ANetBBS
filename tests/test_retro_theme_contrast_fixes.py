"""Regression tests for two real legibility bugs Jerry found live in
the Retro Web '99 theme (screenshots against a real running instance):

1. `main/index.html` hardcoded `style="background-color: #0a0a0a;
   border-color: #00ff00; color: #00ff00;"` directly on the Recent
   Posts / Message Boards list-group rows (and a matching green <hr>
   in Bulletins) -- a pre-existing SITE bug, not specific to this
   theme: it ignores the theme system entirely, and only happened to
   look intentional under a green/black-flavored theme. Retro Web's
   own `:visited` link color (dark purple, `!important`) beat the
   inline green text but nothing beat the inline near-black
   background, leaving illegible dark-purple-on-near-black. Fixed by
   removing the inline styles so these rows inherit from
   base.html's own already-theme-aware `.list-group-item` CSS
   (confirmed already correct via var(--theme-card-bg) etc.) like
   every other list-group row on the site already does.

2. The Admin dropdown menu was genuinely hard to read under Retro
   Web specifically: base.html sets `.dropdown-menu`/`.dropdown-item`
   colors via inline style from --theme-bg-dark/--theme-primary, and
   this theme's own values for those two (navy / blue) are too close
   in value for that pattern -- unlike the other themes, whose
   bg-dark is near-black against a bright accent. Fixed by giving
   Retro Web its own light, beveled dropdown-menu treatment instead
   of trying to retune two variables used everywhere else in the site.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]


class NoHardcodedInlineColorsOnIndexTests(unittest.TestCase):
    def test_index_template_has_no_hardcoded_green_on_black_inline_style(self):
        html = (ROOT / 'anetbbs/templates/main/index.html').read_text()
        self.assertNotIn('#0a0a0a', html)
        self.assertNotIn('background-color: #0a0a0a', html)

    def test_list_group_rows_use_only_bootstrap_classes(self):
        """The rows must still carry list-group-item(-action) so they
        pick up base.html's theme-aware styling -- just with no
        competing inline color override."""
        html = (ROOT / 'anetbbs/templates/main/index.html').read_text()
        self.assertIn('class="list-group-item list-group-item-action"', html)


class RetroThemeDropdownAndListHoverTests(unittest.TestCase):
    def setUp(self):
        self.css = (ROOT / 'anetbbs/static/css/retro_web_theme.css').read_text()

    def test_dropdown_menu_has_an_explicit_light_override(self):
        self.assertIn('.dropdown-menu', self.css)
        self.assertIn('.dropdown-item', self.css)
        # Must use !important -- base.html sets dropdown colors via
        # inline style="", which only a more-specific !important rule
        # in this stylesheet can beat.
        self.assertIn('important', self.css.split('.dropdown-menu')[1][:200])

    def test_list_group_hover_no_longer_falls_back_to_the_dark_navy_default(self):
        self.assertIn('.list-group-item-action:hover', self.css)
        # The generic dark-theme fallback color from base.html that
        # this override exists specifically to avoid inheriting.
        hover_block = self.css.split('.list-group-item-action:hover')[1][:200]
        self.assertNotIn('#1a1a2e', hover_block)


if __name__ == '__main__':
    unittest.main()
