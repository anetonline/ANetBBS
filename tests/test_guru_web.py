"""Regression tests for the Ask Anet guru door's web UI
(anetbbs/web/guru.py), added 2026-07-10.

Covers: the disclosure text always renders, a real search returns a link
to the matching wiki page, and a no-match question shows the fallback copy.
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
import sys

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
    app.config['WTF_CSRF_ENABLED'] = False
    return app


class GuruWebTests(unittest.TestCase):
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

    def _app(self, name):
        return _fresh_app(str(Path(self._tmp.name) / name))

    def test_guru_index_shows_disclosure_text(self):
        app = self._app('a.db')
        from markupsafe import escape
        from anetbbs.guru.personality import DISCLOSURE
        client = app.test_client()
        resp = client.get('/guru/')
        self.assertEqual(resp.status_code, 200)
        # Jinja2 autoescapes the apostrophes in DISCLOSURE (isn't -> isn&#39;t)
        # -- compare against the escaped form, since raw text never appears.
        self.assertIn(str(escape(DISCLOSURE)).encode(), resp.data)

    def test_guru_search_returns_netmail_link(self):
        app = self._app('b.db')
        client = app.test_client()
        resp = client.get('/guru/?q=netmail')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'/wiki/netmail', resp.data)

    def test_guru_search_no_match_shows_not_found_copy(self):
        app = self._app('c.db')
        from anetbbs.guru.personality import NOT_FOUND
        client = app.test_client()
        resp = client.get('/guru/?q=zzqqxxyynonexistentgibberish')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(NOT_FOUND.encode(), resp.data)


if __name__ == '__main__':
    unittest.main()
