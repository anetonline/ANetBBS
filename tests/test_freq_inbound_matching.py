"""Regression tests for echomail/freq.py's process_inbound_req() -- the
DB-touching half of WaZOO FREQ support (matching a parsed .REQ file's
wanted lines against freq-enabled FileArea/FileUpload rows and queuing
HatchQueue entries for the requester). Pure-function parsing/building
tests live separately in test_freq_parsing.py (no app/DB needed there).
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class ProcessInboundReqTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.freq_inbound_test.db')
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

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.app_ctx = self.app.app_context()
        self.app_ctx.push()
        from anetbbs.models import db, User, FileArea, FileUpload, HatchQueue
        db.session.query(HatchQueue).delete()
        db.session.query(FileUpload).delete()
        db.session.query(FileArea).delete()
        db.session.query(User).delete()
        db.session.commit()
        user = User(username='freqtestuploader', email='freq@example.test',
                   password_hash='x', access_level=10)
        db.session.add(user)
        db.session.commit()
        self.user_id = user.id

    def tearDown(self):
        self.app_ctx.pop()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_area(self, tag, freq_enabled=True, freq_password=None):
        from anetbbs.models import db, FileArea
        area = FileArea(tag=tag, name=tag, storage_path=self.tmpdir,
                        is_active=True, freq_enabled=freq_enabled,
                        freq_password=freq_password)
        db.session.add(area)
        db.session.commit()
        return area

    def _make_upload(self, area, filename, content=b'x'):
        from anetbbs.models import db, FileUpload
        path = os.path.join(self.tmpdir, filename)
        with open(path, 'wb') as f:
            f.write(content)
        upload = FileUpload(uploader_id=self.user_id, filename=filename,
                            original_filename=filename, file_path=path,
                            file_size=len(content), file_area_id=area.id)
        db.session.add(upload)
        db.session.commit()
        return upload

    def test_exact_filename_match_queues_a_hatch_row(self):
        from anetbbs.echomail.freq import process_inbound_req
        from anetbbs.models import HatchQueue
        area = self._make_area('FREQTEST')
        self._make_upload(area, 'this.arc')
        queued = process_inbound_req(b'this.arc\r\n', '1:12/2')
        self.assertEqual(len(queued), 1)
        row = HatchQueue.query.get(queued[0])
        self.assertEqual(row.filename, 'this.arc')
        self.assertEqual(row.peer_address, '1:12/2')
        self.assertEqual(row.status, 'pending')

    def test_wildcard_match_queues_multiple_files(self):
        from anetbbs.echomail.freq import process_inbound_req
        area = self._make_area('FREQTEST2')
        self._make_upload(area, 'nodelist.001')
        self._make_upload(area, 'nodelist.002')
        self._make_upload(area, 'unrelated.txt')
        queued = process_inbound_req(b'nodelist.*\r\n', '1:12/2')
        self.assertEqual(len(queued), 2)

    def test_area_not_freq_enabled_matches_nothing(self):
        from anetbbs.echomail.freq import process_inbound_req
        area = self._make_area('FREQOFF', freq_enabled=False)
        self._make_upload(area, 'secret.zip')
        queued = process_inbound_req(b'secret.zip\r\n', '1:12/2')
        self.assertEqual(queued, [])

    def test_area_password_required_and_enforced(self):
        from anetbbs.echomail.freq import process_inbound_req
        area = self._make_area('FREQPW', freq_password='hunter2')
        self._make_upload(area, 'gated.zip')
        # Wrong/missing password -- no match.
        self.assertEqual(process_inbound_req(b'gated.zip\r\n', '1:12/2'), [])
        # Correct password, per FTS-0006's own "filename !password" syntax.
        queued = process_inbound_req(b'gated.zip !hunter2\r\n', '1:12/2')
        self.assertEqual(len(queued), 1)

    def test_no_match_returns_empty_list_no_error(self):
        from anetbbs.echomail.freq import process_inbound_req
        self._make_area('FREQEMPTY')
        queued = process_inbound_req(b'nothing-here.zip\r\n', '1:12/2')
        self.assertEqual(queued, [])

    def test_garbage_req_content_does_not_raise(self):
        from anetbbs.echomail.freq import process_inbound_req
        # Not valid action-line syntax at all -- must be swallowed, not
        # crash the inbound-file dispatch path that calls this.
        queued = process_inbound_req(b'\x00\x01\x02 not text really', '1:12/2')
        self.assertEqual(queued, [])

    def test_matches_from_two_different_freq_areas(self):
        from anetbbs.echomail.freq import process_inbound_req
        area1 = self._make_area('FREQA')
        area2 = self._make_area('FREQB')
        self._make_upload(area1, 'from-a.zip')
        self._make_upload(area2, 'from-b.zip')
        queued = process_inbound_req(b'from-a.zip\r\nfrom-b.zip\r\n', '1:12/2')
        self.assertEqual(len(queued), 2)

    def test_upload_query_count_does_not_scale_with_req_line_count(self):
        # Real gap found in a security/performance audit: process_inbound_req()
        # used to re-run the FileUpload query for the SAME area once per
        # req_line -- an N (lines) x M (areas) DB-query pattern. A .REQ full
        # of names that never match anything (so MAX_FILES_PER_REQUEST's
        # match-count cap never trips) could drive an unbounded number of
        # queries. Fixed by caching each area's candidate uploads the first
        # time it's needed, so the query count is capped at the number of
        # freq-enabled areas regardless of how many lines the .REQ has.
        from anetbbs.echomail.freq import process_inbound_req
        from anetbbs.models import db
        from sqlalchemy import event

        area1 = self._make_area('FREQQC1')
        area2 = self._make_area('FREQQC2')
        self._make_upload(area1, 'real-file.zip')

        # 50 garbage lines that will never match anything in either area,
        # plus one real line -- none of these trip MAX_FILES_PER_REQUEST.
        lines = [f'nomatch{i}.bin' for i in range(50)] + ['real-file.zip']
        content = ('\r\n'.join(lines) + '\r\n').encode('ascii')

        select_count = {'n': 0}

        def _count_selects(conn, cursor, statement, *a, **kw):
            if 'file_uploads' in statement and statement.strip().upper().startswith('SELECT'):
                select_count['n'] += 1

        engine = db.engine
        event.listen(engine, 'before_cursor_execute', _count_selects)
        try:
            queued = process_inbound_req(content, '1:12/2')
        finally:
            event.remove(engine, 'before_cursor_execute', _count_selects)

        self.assertEqual(len(queued), 1)
        # At most one FileUpload SELECT per freq-enabled area (2 areas here)
        # -- NOT once per (line x area) pair, which would be 51 * 2 = 102
        # with the old N+1 behavior.
        self.assertLessEqual(select_count['n'], 2)


if __name__ == '__main__':
    unittest.main()
