"""Regression test for a real High-severity path-traversal finding
from a security/performance audit (2026-08-31):
web/personal_pages.py's serve_root_page() `/folder/...` branch used
the URL's first path segment (`head`) completely unvalidated, unlike
the sibling `~user` branch a few lines above it (which regex-validates
`username` first). A path of `../<rest>` makes
`target_base = base / '..'` resolve to data/personal_pages/'s PARENT
(data/ itself) -- `.is_dir()` confirms that trivially, and
_safe_resolve()'s own containment check then runs against that
ALREADY-ESCAPED target_base, so it passes too, serving anything under
data/ -- including data/admin_password.txt, the initial-admin
plaintext password written on first boot (see web_app.py's own boot
warning) -- to any request reaching this 404 fallback while
PERSONAL_PAGES_ENABLED is on.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class PersonalPagesPathTraversalTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_dir = Path(self._tmp.name)
        (self.data_dir / 'personal_pages').mkdir(parents=True)

        # A real sensitive file OUTSIDE personal_pages/, in DATA_DIR
        # itself -- matches the real admin_password.txt location.
        self.secret_path = self.data_dir / 'admin_password.txt'
        self.secret_path.write_text('super-secret-admin-password\n')

        from flask import Flask
        self.app = Flask(__name__)
        self.app.config['DATA_DIR'] = str(self.data_dir)
        self.app.config['PERSONAL_PAGES_ENABLED'] = True
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.addCleanup(self.ctx.pop)

    def test_dotdot_folder_segment_cannot_escape_personal_pages_root(self):
        from anetbbs.web.personal_pages import serve_root_page
        with self.app.test_request_context('/'):
            resp = serve_root_page('../admin_password.txt')
        self.assertIsNone(
            resp, 'a "../<file>" first path segment must not reach anything '
            'outside data/personal_pages/, even though a bare os.path.join '
            '+ .is_dir() check would have let it through')

    def test_dotdot_folder_segment_with_no_rest_also_rejected(self):
        from anetbbs.web.personal_pages import serve_root_page
        with self.app.test_request_context('/'):
            resp = serve_root_page('..')
        self.assertIsNone(resp)

    def test_legitimate_subfolder_still_works(self):
        legit = self.data_dir / 'personal_pages' / 'welcome'
        legit.mkdir()
        (legit / 'index.html').write_text('<h1>hi</h1>')

        from anetbbs.web.personal_pages import serve_root_page
        with self.app.test_request_context('/'):
            resp = serve_root_page('welcome')
        self.assertIsNotNone(resp,
                             'a real, non-traversal subfolder must still resolve')


if __name__ == '__main__':
    unittest.main()
