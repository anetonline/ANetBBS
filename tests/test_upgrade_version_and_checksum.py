"""Self-update: version comparison with or without a leading "v", and
releases are only advertised or installed with a valid sha256."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod

GOOD_SHA = 'ab' * 32


def _payload(**kw):
    p = {'version': 'v1.1.73', 'name': 'ANetBBS-v1.1.73.tar.gz',
         'url': 'https://bbs.example/downloads/ANetBBS-v1.1.73.tar.gz',
         'sha256': GOOD_SHA, 'size': 1234,
         'published_at': '2026-11-23T12:00:00Z'}
    p.update(kw)
    return p


class VersionParsingTests(unittest.TestCase):
    def setUp(self):
        from anetbbs.web import upgrades
        self.u = upgrades

    def test_optional_leading_v(self):
        u = self.u
        self.assertEqual(u._parse_version('1.1.72'), u._parse_version('v1.1.72'))
        self.assertIsNotNone(u._parse_version('1.1.72'))
        self.assertIsNone(u._parse_version('vv1.1.72'))
        self.assertIsNone(u._parse_version('1.1'))

    def test_newer_same_older(self):
        u = self.u
        self.assertTrue(u._version_newer('v1.1.73', '1.1.72'))
        self.assertTrue(u._version_newer('v1.1.73', 'v1.1.72'))
        self.assertFalse(u._version_newer('v1.1.73', '1.1.73'))
        self.assertFalse(u._version_newer('v1.1.72', 'v1.1.73'))
        self.assertTrue(u._same_version('v1.1.73', '1.1.73'))
        self.assertFalse(u._same_version('v1.1.73', 'v1.1.72'))
        self.assertTrue(u._version_newer('v1.1.0', 'v1.0b2.239'))

    def test_metadata_validation(self):
        p = self.u._release_metadata_problem
        self.assertIsNone(p(_payload()))
        self.assertIn('sha256', p(_payload(sha256=None)))
        self.assertIn('sha256', p(_payload(sha256='abc')))
        self.assertIn('version', p(_payload(version='nope')))
        self.assertIn('url', p(_payload(url='ftp://x/ANetBBS-v1.1.73.tar.gz')))
        self.assertIn('url', p(_payload(url='http://x/ANetBBS-v1.1.73.zip')))
        self.assertIn('size', p(_payload(size=None)))
        self.assertIsNotNone(p(['not', 'a', 'dict']))


class FeedTests(unittest.TestCase):
    """The feed never advertises a tarball without a valid sha256."""

    def setUp(self):
        from flask import Flask
        from anetbbs.web import upgrades
        self.u = upgrades
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for v in ('v1.1.72', 'v1.1.73'):
            Path(self.tmp.name, f'ANetBBS-{v}.tar.gz').write_bytes(b'x' * 10)
        self.app = Flask(__name__)
        self.app.config['DOWNLOADS_DIR'] = self.tmp.name

    def _latest(self, sha_for):
        with self.app.test_request_context('/api/releases/latest'), \
                patch.object(self.u, '_sha256_of', side_effect=sha_for), \
                patch('anetbbs.web.upgrades.url_for', return_value='/downloads/x'):
            from flask import request
            return self.u._latest_payload(request)

    def test_newest_with_valid_sha(self):
        self.assertEqual(self._latest(lambda p: GOOD_SHA)['version'], 'v1.1.73')

    def test_skips_newest_without_sha(self):
        p = self._latest(lambda p: None if 'v1.1.73' in p else GOOD_SHA)
        self.assertEqual(p['version'], 'v1.1.72')
        self.assertEqual(p['sha256'], GOOD_SHA)

    def test_nothing_advertised_without_any_sha(self):
        self.assertIsNone(self._latest(lambda p: None))


class UpstreamFetchTests(unittest.TestCase):
    def setUp(self):
        from anetbbs.web import upgrades
        self.u = upgrades
        self.u._upstream_cache.update({'ts': 0.0, 'url': '', 'data': None, 'error': None})

    def _fetch(self, body, status=200):
        resp = MagicMock(status_code=status)
        resp.json.return_value = body
        with patch('anetbbs.web.upgrades.requests.get', return_value=resp):
            return self.u._fetch_upstream({'REGISTRY_URL': 'http://localhost:5000/'}, force=True)

    def test_valid_body_accepted(self):
        data, err = self._fetch(_payload())
        self.assertIsNone(err)
        self.assertEqual(data['version'], 'v1.1.73')

    def test_body_without_sha_rejected(self):
        data, err = self._fetch(_payload(sha256=None))
        self.assertIsNone(data)
        self.assertIn('sha256', err)


class UpgradePageAndRoutesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._db = str(Path(__file__).resolve().parent / '.upgrade_version_checksum_test.db')
        for s in ('', '-wal', '-shm'):
            if os.path.exists(cls._db + s):
                os.remove(cls._db + s)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._db}'
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()
            u = User(username='upgadmin', email='upgadmin@example.invalid',
                     password_hash='x', is_admin=True, access_level=100)
            db.session.add(u)
            db.session.commit()
            cls.admin = u.id
        from anetbbs.web import upgrades
        cls.u = upgrades

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig
        for s in ('', '-wal', '-shm'):
            if os.path.exists(cls._db + s):
                os.remove(cls._db + s)

    def _client(self):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s['_user_id'] = str(self.admin)
            s['_fresh'] = True
        return c

    def _rows(self, *versions):
        return [{'version': v, 'name': f'ANetBBS-{v}.tar.gz', 'size': 10,
                 'mtime': 0, 'path': f'/nonexistent/ANetBBS-{v}.tar.gz',
                 'parsed': self.u._parse_version(v)} for v in versions]

    def test_page_offers_install_when_installed_version_lacks_v(self):
        with patch.object(self.u, 'VERSION', '1.1.72'), \
                patch.object(self.u, '_fetch_upstream', return_value=(_payload(), None)), \
                patch.object(self.u, '_scan_releases', return_value=self._rows('v1.1.73', 'v1.1.71')):
            r = self._client().get('/admin/upgrades/')
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn('Download &amp; install v1.1.73', html)
        # Web rollback is disabled (audit H-1: the wrapper only moves forward).
        self.assertNotIn('Roll back to v1.1.71', html)
        self.assertNotIn('previous releases on disk', html)

    def test_page_shows_same_and_no_install_when_current(self):
        with patch.object(self.u, 'VERSION', 'v1.1.73'), \
                patch.object(self.u, '_fetch_upstream', return_value=(_payload(), None)), \
                patch.object(self.u, '_scan_releases', return_value=[]):
            html = self._client().get('/admin/upgrades/').get_data(as_text=True)
        self.assertNotIn('Download &amp; install v1.1.73', html)
        self.assertIn('same', html)

    def test_page_renders_when_upstream_has_no_sha(self):
        with patch.object(self.u, 'VERSION', '1.1.72'), \
                patch.object(self.u, '_fetch_upstream', return_value=(_payload(sha256=None), None)), \
                patch.object(self.u, '_scan_releases', return_value=[]):
            r = self._client().get('/admin/upgrades/')
        self.assertEqual(r.status_code, 200)

    def test_install_runs_wrapper_for_newer_release(self):
        spawned = []
        with patch.object(self.u, 'VERSION', '1.1.72'), \
                patch.object(self.u, '_fetch_upstream', return_value=(_payload(), None)), \
                patch.object(self.u, '_RUNTIME', 'systemd'), \
                patch.object(self.u, '_wrapper_path', return_value=__file__), \
                patch.object(self.u, '_check_sudo_escalation', return_value={'status': 'ok'}), \
                patch.object(self.u, '_reset_upgrade_log'), \
                patch.object(self.u, '_spawn_upgrade',
                             side_effect=lambda *a, **k: spawned.append(a)):
            self.u._install_state['running'] = False
            r = self._client().post('/admin/upgrades/install', data={'version': 'v1.1.73'})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(spawned[0][1:], ('v1.1.73', _payload()['url'], GOOD_SHA))
        self.u._install_state['running'] = False

    def test_install_refused_without_valid_sha(self):
        with patch.object(self.u, 'VERSION', '1.1.72'), \
                patch.object(self.u, '_fetch_upstream', return_value=(_payload(sha256=None), None)), \
                patch.object(self.u, '_RUNTIME', 'systemd'), \
                patch.object(self.u, '_wrapper_path', return_value=__file__), \
                patch.object(self.u, '_check_sudo_escalation', return_value={'status': 'ok'}), \
                patch.object(self.u, '_spawn_upgrade') as spawn:
            r = self._client().post('/admin/upgrades/install', data={'version': 'v1.1.73'})
        self.assertNotEqual(r.status_code, 200)
        spawn.assert_not_called()

    def test_rollback_refuses_newer_and_unknown_current(self):
        rows = self._rows('v1.1.73', 'v1.1.71')
        with patch.object(self.u, '_scan_releases', return_value=rows), \
                patch.object(self.u, '_spawn_upgrade') as spawn:
            with patch.object(self.u, 'VERSION', '1.1.72'):
                r = self._client().post('/admin/upgrades/rollback', data={'version': 'v1.1.73'})
            self.assertEqual(r.status_code, 409)
            with patch.object(self.u, 'VERSION', 'garbage'):
                r = self._client().post('/admin/upgrades/rollback', data={'version': 'v1.1.71'})
            self.assertEqual(r.status_code, 409)
            spawn.assert_not_called()

    def test_rollback_route_always_refuses_and_never_spawns(self):
        rows = self._rows('v1.1.71')
        with patch.object(self.u, '_scan_releases', return_value=rows), \
                patch.object(self.u, 'VERSION', 'v1.1.74'), \
                patch.object(self.u, '_spawn_upgrade') as spawn:
            r = self._client().post('/admin/upgrades/rollback', data={'version': 'v1.1.71'})
        self.assertEqual(r.status_code, 409)
        self.assertIn('sudo bash update.sh', r.get_json()['error'])
        spawn.assert_not_called()

    def test_install_rejects_bad_sig_url_without_leaving_running_state(self):
        payload = _payload()
        payload['sig_url'] = 'http://evil.example/x.tar.gz.sig'
        with patch.object(self.u, 'VERSION', 'v1.1.72'), \
                patch.object(self.u, '_fetch_upstream', return_value=(payload, None)), \
                patch.object(self.u, '_RUNTIME', 'systemd'), \
                patch.object(self.u, '_wrapper_path', return_value=__file__), \
                patch.object(self.u, '_check_sudo_escalation', return_value={'status': 'ok'}), \
                patch.object(self.u, '_spawn_upgrade') as spawn:
            self.u._install_state['running'] = False
            r = self._client().post('/admin/upgrades/install', data={'version': 'v1.1.73'})
            self.assertEqual(r.status_code, 502)
            self.assertFalse(self.u._install_state['running'],
                             'a refused install must not leave "install already running" set')
            spawn.assert_not_called()

    def test_rollback_list_empty_when_current_unknown(self):
        with patch.object(self.u, 'VERSION', 'garbage'), \
                patch.object(self.u, '_scan_releases', return_value=self._rows('v1.1.71')):
            with self.app.app_context():
                self.assertEqual(self.u._local_releases(self.app.config), [])


if __name__ == '__main__':
    unittest.main()
