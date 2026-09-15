"""Regression test for a real N+1 query pattern in the file gallery,
found in a security/performance audit.

anetbbs/web/files.py's list_files() iterated FileUpload.query...all()
and then, for every row, accessed u.file_area (inside _visible_to()) and
f.uploader (in templates/files/list.html) -- both many-to-one lazy=True
relationships (models.py), so each access fired its own extra SELECT
against `file_areas`/`users` instead of being folded into the original
query via a JOIN. Cost scaled with the number of files in the gallery on
every single page view.

Fixed by adding `.options(db.joinedload(FileUpload.uploader),
db.joinedload(FileUpload.file_area))` to the query in list_files().

This test seeds several FileUpload rows (some file_area-scoped, some
not) each with a distinct uploader, calls list_files() directly through
a test client, and captures the real SQL sent to the DB via SQLAlchemy's
before_cursor_execute hook (same technique as
test_systat_build_response_n_plus_1.py) to confirm the number of
`users`/`file_areas` queries stays constant regardless of row count.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class FilesListNPlusOneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.files_list_nplus1_test.db')
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

    def _seed_uploads(self, n, tag, scope_every_other=True):
        from anetbbs.models import db, User, FileUpload, FileArea
        area = FileArea(tag=f'FILES_NP1_{tag}'.upper(),
                        name=f'files_np1_area_{tag}', description='',
                        is_active=True, min_access_level=0)
        db.session.add(area)
        db.session.flush()
        for i in range(n):
            uname = f'files_np1_{tag}_{i}'
            u = User(username=uname, email=f'{uname}@example.com')
            u.set_password('x')
            db.session.add(u)
            db.session.flush()
            scoped = scope_every_other and i % 2 == 0
            db.session.add(FileUpload(
                uploader_id=u.id,
                filename=f'{uname}.bin',
                original_filename=f'{uname}.bin',
                file_path=f'/tmp/{uname}.bin',
                file_size=1,
                is_public=True,
                file_area_id=area.id if scoped else None))
        db.session.commit()

    def tearDown(self):
        from anetbbs.models import db, User, FileUpload, FileArea
        with self.app.app_context():
            uploads = FileUpload.query.join(
                User, FileUpload.uploader_id == User.id
            ).filter(User.username.like('files_np1_%')).all()
            for f in uploads:
                db.session.delete(f)
            User.query.filter(User.username.like('files_np1_%')).delete(
                synchronize_session=False)
            FileArea.query.filter(
                FileArea.name.like('files_np1_area_%')).delete(
                synchronize_session=False)
            db.session.commit()

    def test_query_count_does_not_scale_with_file_count(self):
        """The actual regression guard: listing many gallery files must
        not issue one `users`/`file_areas` SELECT per row."""
        from sqlalchemy import event
        from anetbbs.models import db
        from anetbbs.web.files import list_files

        with self.app.test_request_context('/files/'):
            self._seed_uploads(10, tag='count')

            captured = []

            def _capture(conn, cursor, statement, parameters, context, executemany):
                captured.append(statement)

            event.listen(db.engine, 'before_cursor_execute', _capture)
            try:
                list_files()
            finally:
                event.remove(db.engine, 'before_cursor_execute', _capture)

            users_selects = [
                s for s in captured
                if 'SELECT' in s.upper() and 'FROM users' in s]
            areas_selects = [
                s for s in captured
                if 'SELECT' in s.upper() and 'FROM file_areas' in s]
            self.assertLessEqual(
                len(users_selects), 2,
                'expected list_files() to fetch uploader usernames for '
                'all rows via a JOIN (joinedload), not one extra SELECT '
                f'against users PER row -- got {len(users_selects)} '
                'separate `users` queries for 10 uploads:\n' +
                '\n'.join(users_selects))
            self.assertLessEqual(
                len(areas_selects), 2,
                'expected list_files() to fetch file_area rows via a '
                'JOIN (joinedload), not one extra SELECT against '
                f'file_areas PER row -- got {len(areas_selects)} separate '
                '`file_areas` queries for 10 uploads:\n' +
                '\n'.join(areas_selects))

    def test_uploads_still_render_correctly(self):
        """Functional correctness: joinedload must not change what's
        actually rendered, just how many queries it costs."""
        from anetbbs.web.files import list_files

        with self.app.test_request_context('/files/'):
            self._seed_uploads(3, tag='render')
            html = list_files()

        for i in range(3):
            self.assertIn(f'files_np1_render_{i}.bin', html)


if __name__ == '__main__':
    unittest.main()
