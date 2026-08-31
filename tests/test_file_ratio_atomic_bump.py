"""Regression test for a real Medium-severity finding from a security/
performance audit (2026-08-31): anetbbs/web/file_areas.py's download()
and upload() routes each bumped a user's FileRatio byte/file counters
with a Python-side read-then-write (`r.bytes_x = (r.bytes_x or 0) +
delta`) -- the same class of lost-update race already found and fixed
in features/file_quota.py's consume_quota() (see
tests/test_file_quota.py's ConsumeQuotaAtomicIncrementTests for the
full writeup of why an empirical thread-timing test and a same-session
stale-value simulation BOTH failed to reliably discriminate pre/post
fix, and why a source-inspection test is the real deterministic guard
for this exact bug shape). Fixed by extracting both call sites into a
shared _bump_file_ratio() helper that issues a single atomic SQL
UPDATE per increment.
"""
import ast
import inspect
import os
import sys
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


def _body_source_without_docstring(func):
    raw = inspect.getsource(func)
    tree = ast.parse(textwrap.dedent(raw))
    func_node = tree.body[0]
    body_nodes = func_node.body
    if (body_nodes and isinstance(body_nodes[0], ast.Expr)
            and isinstance(getattr(body_nodes[0], 'value', None), ast.Constant)
            and isinstance(body_nodes[0].value.value, str)):
        body_nodes = body_nodes[1:]
    return '\n'.join(
        ast.get_source_segment(textwrap.dedent(raw), node) or ''
        for node in body_nodes)


class BumpFileRatioIsAtomicTests(unittest.TestCase):
    """Deterministic structural guard: _bump_file_ratio() must issue the
    increment via a single atomic SQL UPDATE, not a Python-side
    row.attr = row.attr + n assignment. See test_file_quota.py's
    matching test for why this is the real guard for this bug shape
    rather than a real-thread stress test."""

    def test_bump_file_ratio_uses_atomic_sql_update(self):
        from anetbbs.web import file_areas

        code = _body_source_without_docstring(file_areas._bump_file_ratio)
        self.assertIn(
            '.update(', code,
            '_bump_file_ratio() must issue the increment via a '
            'SQLAlchemy Query.update() (a single atomic SQL UPDATE '
            'statement), not a Python-side row.attr = row.attr + n '
            'assignment')
        self.assertIn(
            'bytes_col + bytes_delta', code,
            'the update expression must reference the column '
            'atomically (col = col + :n) rather than a Python-computed '
            'literal, so the database -- not this process -- reads the '
            'current value at increment time')
        self.assertNotRegex(
            code, r'\.(bytes_uploaded|bytes_downloaded)\s*=\s*\(',
            'must not contain a Python-side read-then-write assignment '
            '(e.g. `row.bytes_uploaded = (row.bytes_uploaded or 0) + '
            'delta`) -- that shape always has a lost-update race window '
            'no matter how it is timed')

    def test_download_and_upload_routes_call_the_shared_atomic_helper(self):
        from anetbbs.web import file_areas

        download_code = _body_source_without_docstring(file_areas.download)
        upload_code = _body_source_without_docstring(file_areas.upload)
        self.assertIn('_bump_file_ratio(', download_code,
                      'download() must route its ratio bump through the '
                      'shared atomic helper')
        self.assertIn('_bump_file_ratio(', upload_code,
                      'upload() must route its ratio bump through the '
                      'shared atomic helper')


class BumpFileRatioFunctionalTests(unittest.TestCase):
    """Confirms _bump_file_ratio() actually does the right thing
    end-to-end: creates the row on first use, accumulates correctly
    across repeated calls, and increments the matching files_x counter
    alongside the byte counter."""

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.file_ratio_bump_test.db')
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
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_creates_row_and_accumulates_across_calls(self):
        from anetbbs.models import db, User, FileRatio
        from anetbbs.web.file_areas import _bump_file_ratio

        with self.app.app_context():
            u = User(username='fileratiobumptester', email='frbt@example.com',
                    password_hash='x', access_level=10)
            db.session.add(u)
            db.session.commit()
            user_id = u.id

            self.assertIsNone(FileRatio.query.filter_by(user_id=user_id).first())

            _bump_file_ratio(user_id, bytes_col=FileRatio.bytes_downloaded,
                            files_col=FileRatio.files_downloaded,
                            bytes_delta=500)
            _bump_file_ratio(user_id, bytes_col=FileRatio.bytes_downloaded,
                            files_col=FileRatio.files_downloaded,
                            bytes_delta=300)

            row = FileRatio.query.filter_by(user_id=user_id).first()
            self.assertIsNotNone(row)
            self.assertEqual(row.bytes_downloaded, 800)
            self.assertEqual(row.files_downloaded, 2)
            self.assertEqual(row.bytes_uploaded or 0, 0)

    def test_upload_and_download_counters_are_independent(self):
        from anetbbs.models import db, User, FileRatio
        from anetbbs.web.file_areas import _bump_file_ratio

        with self.app.app_context():
            u = User(username='fileratioindeptester', email='frit@example.com',
                    password_hash='x', access_level=10)
            db.session.add(u)
            db.session.commit()
            user_id = u.id

            _bump_file_ratio(user_id, bytes_col=FileRatio.bytes_uploaded,
                            files_col=FileRatio.files_uploaded,
                            bytes_delta=1000)
            _bump_file_ratio(user_id, bytes_col=FileRatio.bytes_downloaded,
                            files_col=FileRatio.files_downloaded,
                            bytes_delta=250)

            row = FileRatio.query.filter_by(user_id=user_id).first()
            self.assertEqual(row.bytes_uploaded, 1000)
            self.assertEqual(row.files_uploaded, 1)
            self.assertEqual(row.bytes_downloaded, 250)
            self.assertEqual(row.files_downloaded, 1)


if __name__ == '__main__':
    unittest.main()
