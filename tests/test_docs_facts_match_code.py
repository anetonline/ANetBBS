"""Regression tests for a handful of docs/*.md factual claims that
drifted stale after real feature/config changes landed without the
prose being updated to match -- a real audit finding (2026-10-06):

- docs/27-mrc-chat.md's native umrc-client support banner still said
  "not yet merged to main or shipped in a numbered release" long after
  it actually shipped in v1.1.0 (follow-up in v1.1.2).
- docs/36-ansi-editor.md and docs/38-addons.md both said
  TDF_FONTS_PACK_URL/ENHANCED_CLIENT_ADDON_URL were "empty by default"
  after config.py grew real default Release-asset URLs for both.
- docs/00-overview.md said "12 built-in" themes after a 13th
  (hackers) was added to web_app.py's default_themes seed list.

These check the doc TEXT against the actual CODE-level truth, so a
future change to either side that isn't mirrored on the other gets
caught here instead of silently drifting again.
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO_ROOT = Path(__file__).resolve().parent.parent


class DocsFactsMatchCodeTests(unittest.TestCase):
    def test_overview_theme_count_matches_real_seed_list(self):
        import anetbbs.web_app as web_app_mod
        src = (REPO_ROOT / 'anetbbs' / 'web_app.py').read_text()
        m = re.search(r'default_themes = \[(.*?)\n    \]', src, re.DOTALL)
        self.assertIsNotNone(m, "couldn't find default_themes in web_app.py")
        real_count = len(re.findall(r"'name':", m.group(1)))

        overview = (REPO_ROOT / 'docs' / '00-overview.md').read_text()
        doc_m = re.search(r'pick from (\d+) built-in', overview)
        self.assertIsNotNone(doc_m, "couldn't find the theme-count claim in docs/00-overview.md")
        self.assertEqual(int(doc_m.group(1)), real_count,
                         'docs/00-overview.md\'s stated theme count must '
                         'match web_app.py\'s real default_themes seed list')

    def test_mrc_chat_doc_does_not_claim_umrc_support_is_unmerged(self):
        doc = (REPO_ROOT / 'docs' / '27-mrc-chat.md').read_text()
        self.assertNotIn('not yet', doc[doc.index('umrc-client` support'):][:600])
        self.assertIn('v1.1.0', doc)

    def test_ansi_editor_doc_does_not_claim_tdf_url_is_empty_by_default(self):
        from anetbbs.config import Config
        self.assertTrue(Config.TDF_FONTS_PACK_URL,
                        'this test assumes config.py keeps a real default -- '
                        'if that default was intentionally removed, the docs '
                        'text this checks should go back to saying "empty"')
        doc = (REPO_ROOT / 'docs' / '36-ansi-editor.md').read_text()
        self.assertNotIn('empty by default', doc)

    def test_addons_doc_does_not_tell_sysop_to_configure_already_defaulted_urls(self):
        from anetbbs.config import Config
        self.assertTrue(Config.TDF_FONTS_PACK_URL)
        self.assertTrue(Config.ENHANCED_CLIENT_ADDON_URL)
        doc = (REPO_ROOT / 'docs' / '38-addons.md').read_text()
        self.assertNotIn('Set `ENHANCED_CLIENT_ADDON_URL` to a direct-download URL', doc)
        self.assertNotIn('Set `TDF_FONTS_PACK_URL` the same way', doc)


if __name__ == '__main__':
    unittest.main()
