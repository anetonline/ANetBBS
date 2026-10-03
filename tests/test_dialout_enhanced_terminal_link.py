"""Regression test for Jerry's own suggestion (2026-10-02 Pi3 test
pass, in response to the Enhanced Client's dial-out not working at
all): reuse the already-working web/web_terminal.py (real xterm.js
browser terminal, raw TCP bridge to any host:port) instead of trying
to proxy arbitrary remote-BBS ANSI through the Enhanced Client's own
JSON-only protocol.

Two pieces:
  1. web/web_terminal.py's index() route now accepts ?host=&port= to
     pre-fill the connect form (previously only read env-var defaults).
  2. dialout.py's DialoutMenu._enhanced_terminal_link()/_connect()
     builds that link and shows it instead of just refusing.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class WebTerminalQueryParamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent
                          / '.dialout_enhanced_link_test.db')
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
            u = User(username='dialoutlinktest', email='dlt@example.com',
                    is_active=True)
            u.set_password('correcthorsebatterystaple')
            db.session.add(u)
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _logged_in_client(self):
        client = self.app.test_client()
        client.post('/auth/login', data={
            'username': 'dialoutlinktest',
            'password': 'correcthorsebatterystaple',
        }, follow_redirects=False)
        return client

    def test_host_and_port_query_params_prefill_the_form(self):
        client = self._logged_in_client()
        resp = client.get('/terminal/?host=bbs.a-net.online&port=1337')
        html = resp.get_data(as_text=True)
        self.assertIn('value="bbs.a-net.online"', html)
        self.assertIn('value="1337"', html)

    def test_missing_query_params_fall_back_to_env_defaults(self):
        client = self._logged_in_client()
        resp = client.get('/terminal/')
        html = resp.get_data(as_text=True)
        self.assertIn('value="2233"', html)  # default TELNET_PORT

    def test_invalid_port_falls_back_to_default_instead_of_erroring(self):
        client = self._logged_in_client()
        resp = client.get('/terminal/?host=example.com&port=notanumber')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('value="example.com"', html)
        self.assertIn('value="2233"', html)


class DialoutEnhancedTerminalLinkTests(unittest.TestCase):
    class _FakeSession:
        term_mode = 'enhanced'

        def __init__(self):
            self.written = []

        async def write(self, text):
            self.written.append(text)

        async def read_line(self, prompt=''):
            return ''

    def test_connect_message_includes_a_real_prefilled_link(self):
        import asyncio
        from anetbbs.features.dialout import DialoutMenu

        session = self._FakeSession()
        menu = DialoutMenu(session)
        asyncio.run(menu._connect('A-Net Online', 'bbs.a-net.online', 1337, 'telnet'))
        combined = ''.join(session.written)
        self.assertIn('/terminal/', combined)
        self.assertIn('host=bbs.a-net.online', combined)
        self.assertIn('port=1337', combined)


if __name__ == '__main__':
    unittest.main()
