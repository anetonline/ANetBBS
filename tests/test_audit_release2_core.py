"""Release 2 audit fixes outside the web request path.

N-7 REP MSGID pre-planting, N-10 rlogin caps, N-11 atomic cfg .env write,
F-4 permanent SMTP failure for a vanished recipient, I-4 sanitizer class
and style limits, L-7 web-terminal port block, L-16 and I-2 template fixes.
"""
import asyncio
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import anetbbs.config as cfg_mod

REPO = Path(__file__).resolve().parent.parent


class _DbCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp = tempfile.mkdtemp(prefix='r2core-')
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp}/t.db'
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_uri


class RepMsgidPrePlanting(_DbCase):
    def test_rep_row_does_not_suppress_a_different_message(self):
        from anetbbs.models import db, EchoArea, EchomailMessage, EchomailNetwork
        from anetbbs.echomail.dedupe import is_duplicate_echomail
        with self.app.app_context():
            net = EchomailNetwork(name='r2net', network_type='qwk')
            db.session.add(net)
            db.session.commit()
            area = EchoArea(tag='R2TEST', name='r2', network_id=net.id)
            db.session.add(area)
            db.session.commit()
            db.session.add(EchomailMessage(area_id=area.id, network_id=net.id, from_name='n', to_name='All', msg_id='1:1/1 abc',
                                           subject='planted', body='x',
                                           from_address='QWK:EVIL'))
            db.session.commit()
            self.assertFalse(is_duplicate_echomail('1:1/1 abc', area.id, 'real', 'y'))
            self.assertTrue(is_duplicate_echomail('1:1/1 abc', area.id, 'planted', 'x'))
            db.session.add(EchomailMessage(area_id=area.id, network_id=net.id, from_name='n', to_name='All', msg_id='1:1/1 ftn',
                                           subject='a', body='b', from_address='1:1/1'))
            db.session.commit()
            self.assertTrue(is_duplicate_echomail('1:1/1 ftn', area.id, 'other', 'z'))
            self.assertFalse(is_duplicate_echomail('', area.id))


class SmtpVanishedRecipient(_DbCase):
    def test_550_not_451(self):
        from anetbbs.mail import limits, smtp_server
        with self.app.app_context():
            with mock.patch.object(smtp_server.limits, 'lock_mailboxes'):
                with self.assertRaises(limits.DeliveryRefused) as cm:
                    smtp_server._persist('a@b.test', ['gone@x.test'],
                                         b'Subject: hi\r\n\r\nbody\r\n',
                                         recipient_ids=[(987654, 'gone@x.test')])
        self.assertTrue(cm.exception.reply.startswith('550'))


class RloginCaps(unittest.IsolatedAsyncioTestCase):
    async def test_per_ip_cap(self):
        from anetbbs.core import rlogin_server as rs
        srv = rs.RloginServer.__new__(rs.RloginServer)
        srv.active_connections = set()

        def writer(ip):
            w = mock.Mock()
            w.get_extra_info.return_value = (ip, 1234)
            return w
        for _ in range(rs._MAX_PER_IP):
            srv.active_connections.add(writer('198.51.100.7'))
        w = writer('198.51.100.7')
        await srv.handle_connection(mock.Mock(), w)
        w.close.assert_called()
        self.assertNotIn(w, srv.active_connections)


class CfgEnvWrite(unittest.TestCase):
    def test_atomic_owner_only_and_rejects_newlines(self):
        from anetbbs.cfg.sections import system
        d = tempfile.mkdtemp(prefix='cfgenv-')
        p = Path(d) / '.env'
        system.write_env([('kv', 'A', '1'), ('raw', '# c')], p)
        self.assertEqual(p.read_text(), 'A=1\n# c\n')
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        with self.assertRaises(ValueError):
            system.write_env([('kv', 'A', '1\nADMIN=1')], p)
        self.assertEqual(p.read_text(), 'A=1\n# c\n')
        self.assertEqual(os.listdir(d), ['.env'])


class Sanitizers(unittest.TestCase):
    def test_rss_drops_class_and_style(self):
        from anetbbs.web.rss import _ALLOWED_ATTRS
        self.assertNotIn('*', _ALLOWED_ATTRS)
        self.assertNotIn('style', _ALLOWED_ATTRS['img'])

    def test_wiki_class_allowlist(self):
        from anetbbs.wiki import render
        html = str(render.render(
            '<div class="position-fixed top-0 modal">x</div>\n\n```python\nx=1\n```'))
        self.assertNotIn('position-fixed', html)
        self.assertNotIn('modal', html)


class WebTerminalPorts(unittest.TestCase):
    def test_default_and_override(self):
        from anetbbs.web import web_terminal as wt
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('WEB_TERMINAL_BLOCKED_PORTS', None)
            self.assertIn(25, wt._blocked_ports())
            self.assertNotIn(23, wt._blocked_ports())
        with mock.patch.dict(os.environ, {'WEB_TERMINAL_BLOCKED_PORTS': '25, 2323,x'}):
            self.assertEqual(wt._blocked_ports(), {25, 2323})


class PersonalPageLimitsConfigurable(unittest.TestCase):
    """The documented PERSONAL_PAGES_MAX_* settings used to be read only
    from app config, where nothing set them, so .env values were ignored."""

    def test_limits_come_from_the_environment(self):
        from anetbbs import config
        self.assertEqual(config.Config.PERSONAL_PAGES_MAX_FILES, 100)
        with mock.patch.dict(os.environ, {'X_R2_INT': '7'}):
            self.assertEqual(config._env_int('X_R2_INT', 1), 7)
        with mock.patch.dict(os.environ, {'X_R2_INT': 'lots'}):
            self.assertEqual(config._env_int('X_R2_INT', 1), 1)


class TemplateFixes(unittest.TestCase):
    def test_broadcast_toast_escaped(self):
        s = (REPO / 'anetbbs/templates/base.html').read_text()
        self.assertIn('${escapeToastText(d.sender)}', s)
        self.assertIn('${escapeToastText(d.text)}', s)
        self.assertNotIn('${d.text}', s)

    def test_drafts_per_account_and_cleared_on_logout(self):
        for t in ('boards/new_post.html', 'mail/compose.html', 'pm/compose.html'):
            s = (REPO / 'anetbbs/templates' / t).read_text()
            self.assertIn("'draft:{{ current_user.id }}:'", s, t)
        base = (REPO / 'anetbbs/templates/base.html').read_text()
        self.assertEqual(base.count('data-logout-form'), 3)   # 2 forms + the selector
        self.assertIn('anetClearLocalUserData', base)


if __name__ == '__main__':
    unittest.main()
