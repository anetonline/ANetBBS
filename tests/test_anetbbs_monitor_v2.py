"""Regression tests for anetbbs-monitor v2's new dashboard functionality
(anetbbs/monitor/app.py) -- the Today/Total stats panel, the seek-from-
end log tail helper, theme persistence, and mouse-click hotkey hit
testing -- plus the real gap the stats panel work uncovered: the two
web file-download routes (web/files.py, web/file_areas.py) never
actually logged a UserActivity(activity_type='file_download') row
despite that being a documented UserActivity convention (the terminal
ZMODEM download path's half of this same fix is covered directly in
tests/test_bbs_ui_file_quota.py, alongside its existing quota tests).

The curses screen itself isn't practically unit-testable without a
real terminal (same reasoning as tests/test_node_monitor_cli.py's own
docstring) -- what's covered here is the pure data/logic layer
underneath it.
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


def _fresh_app(db_path):
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    os.environ['FLASK_ENV'] = 'testing'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    return app


class FetchStatsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._orig_flask_env = os.environ.get('FLASK_ENV')

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        if cls._orig_flask_env is None:
            os.environ.pop('FLASK_ENV', None)
        else:
            os.environ['FLASK_ENV'] = cls._orig_flask_env

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_today_vs_total_counts_are_correct(self):
        from anetbbs.monitor.app import fetch_stats
        app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        from anetbbs.models import db, User, CallerLog
        with app.app_context():
            u = User(username='statsuser', email='s@example.com', password_hash='x')
            db.session.add(u)
            db.session.commit()
            now = datetime.utcnow()
            today_start = datetime.combine(now.date(), datetime.min.time())
            db.session.add_all([
                CallerLog(user_id=u.id, username='statsuser', service='ssh',
                         started_at=now, duration_seconds=60),
                CallerLog(user_id=u.id, username='statsuser', service='ssh',
                         started_at=today_start - timedelta(days=1),
                         duration_seconds=120),
            ])
            db.session.commit()

            stats = fetch_stats()
            self.assertEqual(stats['logons_today'], 1)
            self.assertEqual(stats['logons_total'], 2)
            self.assertEqual(stats['time_today'], 60)
            self.assertEqual(stats['time_total'], 180)

    def test_posts_excludes_inbound_echomail_only_counts_outbound(self):
        """Real design decision: EchomailMessage.created_at also covers
        messages this BBS merely *received* from the network (imported
        FTN traffic), which isn't this BBS's own posting activity and
        would inflate "Posts" for any install with heavy inbound
        echomail -- only direction='outbound' rows should count."""
        from anetbbs.monitor.app import fetch_stats
        app = _fresh_app(str(Path(self._tmp.name) / 'b.db'))
        from anetbbs.models import (db, User, EchoArea, EchomailNetwork,
                                    EchomailMessage)
        with app.app_context():
            u = User(username='poststest', email='p@example.com', password_hash='x')
            net = EchomailNetwork(name='TestNet', network_type='binkp', is_active=True)
            db.session.add_all([u, net])
            db.session.commit()
            area = EchoArea(network_id=net.id, tag='TEST.AREA', name='Test',
                            is_active=True, is_subscribed=True, min_access_level=0)
            db.session.add(area)
            db.session.commit()
            now = datetime.utcnow()
            db.session.add_all([
                EchomailMessage(area_id=area.id, network_id=net.id,
                               from_name='local', to_name='All', subject='out',
                               body='b', direction='outbound', created_at=now),
                EchomailMessage(area_id=area.id, network_id=net.id,
                               from_name='remote', to_name='All', subject='in',
                               body='b', direction='inbound', created_at=now),
            ])
            db.session.commit()

            stats = fetch_stats()
            self.assertEqual(stats['posts_today'], 1)
            self.assertEqual(stats['posts_total'], 1)

    def test_uploads_today_counts_files_and_sums_bytes(self):
        from anetbbs.monitor.app import fetch_stats
        app = _fresh_app(str(Path(self._tmp.name) / 'c.db'))
        from anetbbs.models import db, User, FileUpload
        with app.app_context():
            u = User(username='uploadtest', email='u@example.com', password_hash='x')
            db.session.add(u)
            db.session.commit()
            now = datetime.utcnow()
            db.session.add_all([
                FileUpload(uploader_id=u.id, filename='a.zip',
                          original_filename='a.zip', file_path='/tmp/a.zip',
                          file_size=100, created_at=now),
                FileUpload(uploader_id=u.id, filename='b.zip',
                          original_filename='b.zip', file_path='/tmp/b.zip',
                          file_size=200, created_at=now - timedelta(days=2)),
            ])
            db.session.commit()

            stats = fetch_stats()
            self.assertEqual(stats['uploads_today_files'], 1)
            self.assertEqual(stats['uploads_today_bytes'], 100)

    def test_downloads_today_counts_file_download_activity(self):
        from anetbbs.monitor.app import fetch_stats
        app = _fresh_app(str(Path(self._tmp.name) / 'd.db'))
        from anetbbs.models import db, User, UserActivity
        with app.app_context():
            u = User(username='dltest', email='d@example.com', password_hash='x')
            db.session.add(u)
            db.session.commit()
            now = datetime.utcnow()
            db.session.add_all([
                UserActivity(user_id=u.id, activity_type='file_download',
                            created_at=now),
                UserActivity(user_id=u.id, activity_type='file_download',
                            created_at=now - timedelta(days=1)),
                UserActivity(user_id=u.id, activity_type='login', created_at=now),
            ])
            db.session.commit()

            stats = fetch_stats()
            self.assertEqual(stats['downloads_today'], 1)


class TailLinesTests(unittest.TestCase):
    """Pure-function tests for the seek-from-end log reader -- the real
    lesson already learned once in this codebase (v1.0.54): an
    unbounded readlines() on a multi-GB bbs.log is a genuine OOM risk,
    not theoretical."""

    def test_returns_lines_in_original_order_most_recent_last(self):
        from anetbbs.monitor.app import tail_lines
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.log') as f:
            for i in range(10):
                f.write(f'line {i}\n')
            path = f.name
        self.addCleanup(os.remove, path)

        lines = tail_lines(path, target_lines=100)
        self.assertEqual(lines, [f'line {i}' for i in range(10)])

    def test_never_reads_more_than_needed_stays_bounded_on_a_huge_file(self):
        """The whole point: seek from the end in chunks, not read the
        entire file into memory. Build a file much larger than a
        single chunk and confirm it still returns just the tail."""
        from anetbbs.monitor.app import tail_lines
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.log') as f:
            for i in range(5000):
                f.write(f'line {i}\n')
            path = f.name
        self.addCleanup(os.remove, path)

        # Small chunk_size forces multiple seek-backward iterations --
        # exercises the loop, not just a single-read shortcut.
        lines = tail_lines(path, target_lines=50, chunk_size=256)
        self.assertGreaterEqual(len(lines), 50)
        self.assertEqual(lines[-1], 'line 4999',
                         'must end on the real last line of the file')

    def test_missing_file_raises_oserror_for_the_caller_to_handle(self):
        from anetbbs.monitor.app import tail_lines
        with self.assertRaises(OSError):
            tail_lines('/nonexistent/path/to/bbs.log')


class FormatHelpersTests(unittest.TestCase):
    def test_fmt_bytes_scales_units(self):
        from anetbbs.monitor.app import _fmt_bytes
        self.assertEqual(_fmt_bytes(0), '0 bytes')
        self.assertEqual(_fmt_bytes(500), '500 bytes')
        self.assertEqual(_fmt_bytes(2048), '2.0 KB')
        self.assertEqual(_fmt_bytes(5 * 1024 * 1024), '5.0 MB')


class ThemePersistenceTests(unittest.TestCase):
    def test_round_trips_through_a_real_file(self):
        from anetbbs.monitor.app import _load_theme, _save_theme
        with tempfile.TemporaryDirectory() as d:
            class _FakeApp:
                config = {'DATA_DIR': d}
            app = _FakeApp()
            self.assertEqual(_load_theme(app), 'dark',
                             'no saved theme yet must default to dark')
            _save_theme(app, 'light')
            self.assertEqual(_load_theme(app), 'light')
            _save_theme(app, 'dark')
            self.assertEqual(_load_theme(app), 'dark')

    def test_missing_data_dir_is_safe_not_a_crash(self):
        from anetbbs.monitor.app import _load_theme, _save_theme

        class _FakeApp:
            config = {'DATA_DIR': '/nonexistent/theme/state/dir'}
        app = _FakeApp()
        self.assertEqual(_load_theme(app), 'dark')
        _save_theme(app, 'light')  # must not raise


class HotkeyHitTestTests(unittest.TestCase):
    """The menu hint line and mouse click hit-testing are built from the
    same MENU_HOTKEYS list -- a click must always resolve to the exact
    key its own label names."""

    def test_click_on_each_label_resolves_to_its_own_key(self):
        from anetbbs.monitor.app import (
            MENU_HOTKEYS, _menu_hint_line, _hotkey_hit_test)
        line = _menu_hint_line()
        pos = 0
        for key, label in MENU_HOTKEYS:
            token = f"[{key}]{label}"
            # Click in the middle of this token.
            click_x = pos + len(token) // 2
            self.assertEqual(_hotkey_hit_test(click_x, line), key,
                             f'click on {token!r} must resolve to {key!r}')
            pos += len(token) + 2

    def test_click_in_the_gap_between_labels_resolves_to_none(self):
        from anetbbs.monitor.app import _menu_hint_line, _hotkey_hit_test
        line = _menu_hint_line()
        self.assertIsNone(_hotkey_hit_test(len(line) + 10, line))


class WebDownloadActivityLoggingTests(unittest.TestCase):
    """The two web download routes' half of the file_download logging
    fix -- see this file's own module docstring for the full picture."""

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.monitor_v2_web_dl_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()
            u = User(username='webdltest', email='wdl@example.com',
                    password_hash='x', is_admin=False, access_level=100)
            db.session.add(u)
            db.session.commit()
            cls.user_id = u.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def setUp(self):
        # Both tests in this class query UserActivity.filter_by(
        # activity_type='file_download').first() against the same
        # shared DB -- without clearing between tests, whichever test
        # runs second picks up the first test's row instead of its own.
        from anetbbs.models import db, UserActivity
        with self.app.app_context():
            UserActivity.query.delete()
            db.session.commit()

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def test_files_download_route_logs_file_download_activity(self):
        from anetbbs.models import db, FileUpload, UserActivity
        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: None)
        fpath_dir = tmp_dir
        with self.app.app_context():
            self.app.config['UPLOADS_DIR'] = fpath_dir
            with open(os.path.join(fpath_dir, 'stored.bin'), 'wb') as f:
                f.write(b'hello world')
            upload = FileUpload(
                uploader_id=self.user_id, filename='stored.bin',
                original_filename='stored.bin',
                file_path=os.path.join(fpath_dir, 'stored.bin'),
                file_size=11, is_public=True)
            db.session.add(upload)
            db.session.commit()
            upload_id = upload.id

        client = self._client_as(self.user_id)
        resp = client.get(f'/files/download/{upload_id}')
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            activity = UserActivity.query.filter_by(
                activity_type='file_download').first()
            self.assertIsNotNone(
                activity, 'the web file download route must log a '
                'file_download UserActivity row')
            self.assertEqual(activity.user_id, self.user_id)

    def test_shared_link_download_logs_file_download_activity_anonymously(self):
        """user_id must be None here -- the downloader on this route is
        always anonymous by design (quota is charged to the share's
        creator instead, a pre-existing and unrelated mechanism)."""
        from anetbbs.models import db, FileArea, SharedFileLink, UserActivity
        with tempfile.TemporaryDirectory() as storage_dir:
            with open(os.path.join(storage_dir, 'shared.zip'), 'wb') as f:
                f.write(b'shared contents')
            with self.app.app_context():
                area = FileArea(name='ShareArea', tag='SHAREAREA',
                                storage_path=storage_dir, is_active=True,
                                min_access_level=0)
                db.session.add(area)
                db.session.commit()
                link = SharedFileLink(token='testtoken1234567890abcdef012345',
                                      created_by_id=self.user_id,
                                      file_area_id=area.id,
                                      filename='shared.zip')
                db.session.add(link)
                db.session.commit()
                token = link.token

            client = self.app.test_client()
            resp = client.get(f'/file-areas/shared/{token}')
            self.assertEqual(resp.status_code, 200)

            with self.app.app_context():
                activity = UserActivity.query.filter_by(
                    activity_type='file_download').first()
                self.assertIsNotNone(activity)
                self.assertIsNone(activity.user_id,
                                 'shared-link downloader is always anonymous')


if __name__ == '__main__':
    unittest.main()
