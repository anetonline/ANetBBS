"""M-5: text that arrives from other BBSes must not carry terminal escape or
control sequences into our callers' terminals.

Before the fix, fsxNet IBLC (last-caller) rows and ANET_LASTCALLERS rows
were stored and rendered raw: alias / BBS name / location / OS / address /
origin straight to session.write().
"""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path

EVIL = '\x1b[2J\x1b]0;pwned\x07\x1b[31mEVIL\x07'


def _make_app(tmp):
    import anetbbs.config as cfg_mod
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{tmp}/m5.db'
    os.environ['FLASK_ENV'] = 'testing'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    with app.app_context():
        from anetbbs.models import db
        db.create_all()
    return app


def _no_foreign_escapes(test, value):
    test.assertNotIn('\x1b', value or '')
    test.assertNotIn('\x07', value or '')


class InboundIngestIsSanitized(unittest.TestCase):
    def setUp(self):
        import anetbbs.config as cfg_mod
        self._orig = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        self.addCleanup(lambda: setattr(cfg_mod.TestingConfig,
                                        'SQLALCHEMY_DATABASE_URI', self._orig))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_app(self._tmp.name)

    def _net_area(self, tag):
        from anetbbs.models import db, EchomailNetwork, EchoArea
        net = EchomailNetwork(name=f'N-{tag}', network_type='binkp', our_address='21:4/999')
        db.session.add(net)
        db.session.commit()
        area = EchoArea(network_id=net.id, tag=tag, name=tag, is_active=True,
                        is_subscribed=True, is_sysop_only=True)
        db.session.add(area)
        db.session.commit()
        return net, area

    def test_fsxnet_iblc_fields_stripped(self):
        from anetbbs.echomail.fsxnet_sync import (
            sync_fsxnet_inbound, rot47, IBLC_MSG_FROM, IBLC_MSG_TO,
            IBLC_MSG_SUBJECT, IBLC_BEGIN, IBLC_END)
        from anetbbs.models import db, EchomailMessage, FsxnetLastCaller
        with self.app.app_context():
            self.app.config['FSXNET_IBLC_ENABLED'] = True
            net, area = self._net_area('FSX_DAT')
            self.app.config['FSXNET_NETWORK_ID'] = net.id
            fields = [EVIL + 'alias', 'BBS' + EVIL, '10/07/26', '08:00a',
                      'Loc' + EVIL, 'Lin' + EVIL, 'host.example' + EVIL]
            body = IBLC_BEGIN + '\n' + '\n'.join(rot47(f) for f in fields) + '\n' + IBLC_END
            db.session.add(EchomailMessage(
                area_id=area.id, network_id=net.id, msg_id='LC-EVIL',
                from_name=IBLC_MSG_FROM, to_name=IBLC_MSG_TO, subject=IBLC_MSG_SUBJECT,
                body=body, direction='inbound'))
            db.session.commit()
            ok, out = sync_fsxnet_inbound(self.app, {})
            self.assertTrue(ok, out)
            (row,) = FsxnetLastCaller.query.all()
            for v in (row.alias, row.bbs_name, row.location, row.os, row.address):
                _no_foreign_escapes(self, v)
            self.assertIn('alias', row.alias)

    def test_fsxnet_oneliner_fields_stripped(self):
        from anetbbs.echomail.fsxnet_sync import (
            sync_fsxnet_inbound, IBOL_MSG_TO, IBOL_MSG_SUBJECT)
        from anetbbs.models import db, EchomailMessage, FsxnetOneliner
        with self.app.app_context():
            self.app.config['FSXNET_IBOL_ENABLED'] = True
            net, area = self._net_area('FSX_DAT')
            self.app.config['FSXNET_NETWORK_ID'] = net.id
            db.session.add(EchomailMessage(
                area_id=area.id, network_id=net.id, msg_id='OL-EVIL',
                from_name='x', to_name=IBOL_MSG_TO, subject=IBOL_MSG_SUBJECT,
                body=f'Author: A{EVIL}\nSource: S{EVIL}\nOneliner: hi{EVIL}',
                direction='inbound'))
            db.session.commit()
            ok, out = sync_fsxnet_inbound(self.app, {})
            self.assertTrue(ok, out)
            (row,) = FsxnetOneliner.query.all()
            for v in (row.author, row.source_bbs, row.body):
                _no_foreign_escapes(self, v)

    def test_anet_lastcallers_fields_stripped(self):
        from anetbbs.echomail.interbbs_sync import sync_lastcallers_inbound
        from anetbbs.models import db, EchomailMessage, CallerLog
        with self.app.app_context():
            net, area = self._net_area('ANET_LASTCALLERS')
            self.app.config['LASTCALLERS_INTERBBS_NETWORK_ID'] = net.id
            db.session.add(EchomailMessage(
                area_id=area.id, network_id=net.id, msg_id='LCA-EVIL',
                from_name='bob' + EVIL, to_name='All', subject='x',
                body='tel' + EVIL + 'net\n', origin_line='Evil BBS' + EVIL,
                direction='inbound'))
            db.session.commit()
            ok, out = sync_lastcallers_inbound(self.app, {})
            self.assertTrue(ok, out)
            row = CallerLog.query.filter_by(remote_msg_id='LCA-EVIL').first()
            for v in (row.username, row.service, row.origin_bbs):
                _no_foreign_escapes(self, v)

    def test_anet_wall_fields_stripped(self):
        from anetbbs.echomail.interbbs_sync import sync_wall_inbound
        from anetbbs.models import db, EchomailMessage, WallPost
        with self.app.app_context():
            net, area = self._net_area('ANET_WALL')
            self.app.config['WALL_INTERBBS_NETWORK_ID'] = net.id
            db.session.add(EchomailMessage(
                area_id=area.id, network_id=net.id, msg_id='W-EVIL',
                from_name='wally' + EVIL, to_name='All', subject='x',
                body='line one' + EVIL + '\nline two' + EVIL,
                origin_line='Evil' + EVIL, direction='inbound'))
            db.session.commit()
            ok, out = sync_wall_inbound(self.app, {})
            self.assertTrue(ok, out)
            row = WallPost.query.filter_by(remote_msg_id='W-EVIL').first()
            for v in (row.username, row.display_name, row.line1, row.line2, row.origin_bbs):
                _no_foreign_escapes(self, v)


class _Session:
    def __init__(self):
        self.out = []
        self.term_mode = 'ansi'
        self.term_width = 80
        self.term_height = 24

    async def write(self, s):
        self.out.append(s)

    async def read_line(self, *a, **k):
        return ''


class RenderPathsStripLegacyRows(unittest.TestCase):
    """Rows already stored before the fix must still render safely."""

    def test_iblc_aggregate_and_helper(self):
        from anetbbs.features import fsxnet_iblc

        class R:
            bbs_name = 'BBS' + EVIL
            address = 'h' + EVIL
            os = 'o' + EVIL
            created_at = None
        out = fsxnet_iblc._aggregate_bbses([R()])
        for v in (out[0]['bbs_name'], out[0]['address'], out[0]['os']):
            _no_foreign_escapes(self, v)

    def test_lastcallers_screen(self):
        """Real render path: show_last_callers() over a legacy row stored
        with raw escapes, output captured from the pager."""
        import anetbbs.config as cfg_mod
        from unittest import mock
        from anetbbs.features import lastcallers
        orig = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        tmp = tempfile.mkdtemp(prefix='m5r-')
        try:
            app = _make_app(tmp)
            from anetbbs.models import db, CallerLog
            from datetime import datetime
            with app.app_context():
                db.session.add(CallerLog(username='bob' + EVIL, service='tel' + EVIL,
                                         started_at=datetime.utcnow(),
                                         origin_bbs='Evil' + EVIL))
                db.session.commit()
            captured = []

            async def _fake_pager(session, lines, **k):
                captured.extend(lines)
            sess = _Session()
            with mock.patch.object(lastcallers, '_app', return_value=app), \
                    mock.patch.object(lastcallers, '_page_lines', _fake_pager):
                asyncio.run(lastcallers.show_last_callers(sess))
            body = ''.join(captured)
            self.assertIn('bob', body)
            # Only our own SGR colour codes may remain.
            import re
            stripped = re.sub(r'\x1b\[[0-9;]*m', '', body)
            _no_foreign_escapes(self, stripped)
        finally:
            cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = orig

    def test_dialout_name_is_stripped(self):
        src = (Path(__file__).resolve().parent.parent / 'anetbbs' / 'features'
               / 'dialout.py').read_text()
        self.assertIn('name, host = _su(name), _su(host)', src)


class SmtpFromAndCcControls(unittest.TestCase):
    def test_control_chars_removed(self):
        from anetbbs.mail import smtp_server
        src = Path(smtp_server.__file__).read_text()
        self.assertIn("from_addr = _CONTROL_CHARS_RE.sub('', from_addr or mail_from or '')", src)
        self.assertIn("cc_list = [_CONTROL_CHARS_RE.sub('', c or '') for c in cc_list]", src)


if __name__ == '__main__':
    unittest.main()
