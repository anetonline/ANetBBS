"""Tests for fsxNet IBOL/IBLC (anetbbs/echomail/fsxnet_sync.py) -- real
wire-format InterBBS Oneliners + Last Callers over fsxNet's shared
FSX_DAT echo area, deliberately separate from ANetBBS's own private
InterBBS Wall/Last Callers sync (interbbs_sync.py).

Covers:
  - rot47 round-trips exactly (ported from iblc.js).
  - Outbound composes the real wire shape (to/from/subject) and
    immediately materializes a local row for instant visibility.
  - Inbound parses real-shaped fixtures (copied from the actual .js
    output format) into FsxnetOneliner/FsxnetLastCaller rows.
  - Global dedup (same msg_id across two areas only imports once).
  - NULL msg_id rows are skipped, not materialized.
  - Outbound (direction='outbound') rows are never re-scanned by the
    inbound sync -- no double-insert of our own posts.
  - An unrecognized to/from/subject shape in FSX_DAT is skipped, not
    mis-parsed as one of our two known formats.
  - FSXNET_HIDE_SYSOP gates the IBLC outbound relay the same way
    LASTCALLERS_HIDE_SYSOP already gates the ANET_LASTCALLERS one.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _make_app(db_path):
    import anetbbs.config as cfg_mod
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    os.environ['FLASK_ENV'] = 'testing'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    return app


class Rot47Tests(unittest.TestCase):
    def test_round_trips(self):
        from anetbbs.echomail.fsxnet_sync import rot47
        for s in ('StingRay', 'A-Net Online', 'bbs.a-net.fyi:2233',
                  'New York, NY', '12/31/26', '11:59p'):
            self.assertEqual(rot47(rot47(s)), s)

    def test_known_values_match_the_real_cipher(self):
        """Pinned against the real rot47 definition (shift printable
        ASCII 33-126 by 47 mod 94) -- 'A' (65) -> 65+14=79 mod94=79,
        +33... verified by direct computation, not just round-trip,
        so a self-consistent-but-wrong implementation can't pass."""
        from anetbbs.echomail.fsxnet_sync import rot47
        self.assertEqual(rot47('A'), 'p')
        self.assertEqual(rot47('p'), 'A')
        self.assertEqual(rot47(' '), ' ')  # space (32) is outside 33-126, untouched
        self.assertEqual(rot47('~'), 'O')


class ParseOnelinerTests(unittest.TestCase):
    def test_parses_real_shaped_body(self):
        from anetbbs.echomail.fsxnet_sync import _parse_oneliner
        body = 'Author: Codefenix\nSource: ConstructiveChaos BBS\nOneliner: Hello fsxNet!'
        result = _parse_oneliner(body)
        self.assertIsNotNone(result)
        author, source_bbs, lines = result
        self.assertEqual(author, 'Codefenix')
        self.assertEqual(source_bbs, 'ConstructiveChaos BBS')
        self.assertEqual(lines, ['Hello fsxNet!'])

    def test_joins_multiple_oneliner_lines_in_order(self):
        from anetbbs.echomail.fsxnet_sync import _parse_oneliner
        body = 'Author: X\nSource: Y\nOneliner: line one\nOneliner: line two'
        _, _, lines = _parse_oneliner(body)
        self.assertEqual(lines, ['line one', 'line two'])

    def test_missing_author_is_rejected(self):
        """Matches the real ibol.js (0.250322): posts must not be shown
        unless BOTH Author and Source are populated -- a malformed post
        from a misbehaving peer must not crash or half-import."""
        from anetbbs.echomail.fsxnet_sync import _parse_oneliner
        self.assertIsNone(_parse_oneliner('Source: Y\nOneliner: hi'))

    def test_missing_source_is_rejected(self):
        from anetbbs.echomail.fsxnet_sync import _parse_oneliner
        self.assertIsNone(_parse_oneliner('Author: X\nOneliner: hi'))

    def test_unrelated_body_is_rejected(self):
        from anetbbs.echomail.fsxnet_sync import _parse_oneliner
        self.assertIsNone(_parse_oneliner('this is not an oneliner at all'))


class ParseLastCallTests(unittest.TestCase):
    def test_parses_real_shaped_rot47_body(self):
        from anetbbs.echomail.fsxnet_sync import rot47, _parse_lastcall, IBLC_BEGIN, IBLC_END
        fields = ['StingRay', 'A-Net Online', '10/07/26', '08:00a',
                 'New York, NY', 'Linux', 'bbs.a-net.fyi:2233']
        body = IBLC_BEGIN + '\n' + '\n'.join(rot47(f) for f in fields) + '\n' + IBLC_END
        result = _parse_lastcall(body)
        self.assertIsNotNone(result)
        self.assertEqual(result['alias'], 'StingRay')
        self.assertEqual(result['bbs_name'], 'A-Net Online')
        self.assertEqual(result['remote_date_str'], '10/07/26')
        self.assertEqual(result['remote_time_str'], '08:00a')
        self.assertEqual(result['location'], 'New York, NY')
        self.assertEqual(result['os'], 'Linux')
        self.assertEqual(result['address'], 'bbs.a-net.fyi:2233')

    def test_missing_begin_marker_is_rejected(self):
        from anetbbs.echomail.fsxnet_sync import _parse_lastcall
        self.assertIsNone(_parse_lastcall('not a last-caller post at all'))

    def test_truncated_fields_are_rejected(self):
        from anetbbs.echomail.fsxnet_sync import _parse_lastcall, IBLC_BEGIN
        self.assertIsNone(_parse_lastcall(IBLC_BEGIN + '\nonly\ntwo\nfields'))


class FsxnetOutboundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import anetbbs.config as cfg_mod
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI

    @classmethod
    def tearDownClass(cls):
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_app(str(Path(self._tmp.name) / 'a.db'))
        with self.app.app_context():
            from anetbbs.models import db
            db.create_all()

    def _network(self, network_type='binkp'):
        from anetbbs.models import db, EchomailNetwork
        net = EchomailNetwork(name='fsxNet', network_type=network_type,
                              our_address='21:4/999')
        db.session.add(net)
        db.session.commit()
        return net

    def test_post_oneliner_composes_real_wire_shape_and_materializes_locally(self):
        from anetbbs.echomail.fsxnet_sync import post_oneliner_to_fsxnet, IBOL_MSG_TO, IBOL_MSG_SUBJECT
        from anetbbs.models import EchomailMessage, FsxnetOneliner
        with self.app.app_context():
            self.app.config['FSXNET_IBOL_ENABLED'] = True
            net = self._network()
            self.app.config['FSXNET_NETWORK_ID'] = net.id

            post_oneliner_to_fsxnet(['hello fsxNet'], 'jerry')

            msgs = EchomailMessage.query.filter_by(direction='outbound').all()
            self.assertEqual(len(msgs), 1)
            self.assertEqual(msgs[0].to_name, IBOL_MSG_TO)
            self.assertEqual(msgs[0].subject, IBOL_MSG_SUBJECT)
            self.assertIn('Author: jerry', msgs[0].body)
            self.assertIn('Oneliner: hello fsxNet', msgs[0].body)

            local = FsxnetOneliner.query.all()
            self.assertEqual(len(local), 1,
                             'the poster must see their own post immediately, '
                             'matching the real ibol.js re-reading the base '
                             'right after save_msg()')
            self.assertEqual(local[0].author, 'jerry')
            self.assertEqual(local[0].body, 'hello fsxNet')

    def test_post_oneliner_disabled_posts_nothing(self):
        from anetbbs.echomail.fsxnet_sync import post_oneliner_to_fsxnet
        from anetbbs.models import EchomailMessage, FsxnetOneliner
        with self.app.app_context():
            self.app.config['FSXNET_IBOL_ENABLED'] = False
            net = self._network()
            self.app.config['FSXNET_NETWORK_ID'] = net.id

            post_oneliner_to_fsxnet(['hello'], 'jerry')

            self.assertEqual(EchomailMessage.query.count(), 0)
            self.assertEqual(FsxnetOneliner.query.count(), 0)

    def test_post_oneliner_qwk_network_never_posts(self):
        """Same reasoning as ANET_WALL: a symbolic area tag can never
        receive real QWK traffic."""
        from anetbbs.echomail.fsxnet_sync import post_oneliner_to_fsxnet
        from anetbbs.models import EchomailMessage
        with self.app.app_context():
            self.app.config['FSXNET_IBOL_ENABLED'] = True
            net = self._network(network_type='qwk')
            self.app.config['FSXNET_NETWORK_ID'] = net.id

            post_oneliner_to_fsxnet(['hello'], 'jerry')

            self.assertEqual(EchomailMessage.query.count(), 0)

    def test_post_lastcall_composes_rot47_body_and_materializes_locally(self):
        from anetbbs.echomail.fsxnet_sync import (
            post_lastcall_to_fsxnet, rot47, IBLC_MSG_FROM, IBLC_MSG_TO, IBLC_MSG_SUBJECT)
        from anetbbs.models import db, User, EchomailMessage, FsxnetLastCaller
        with self.app.app_context():
            self.app.config['FSXNET_IBLC_ENABLED'] = True
            net = self._network()
            self.app.config['FSXNET_NETWORK_ID'] = net.id

            user = User(username='jerry', email='j@example.com',
                       password_hash='x', location='New York, NY')
            db.session.add(user)
            db.session.commit()

            post_lastcall_to_fsxnet(user, 'telnet')

            msgs = EchomailMessage.query.filter_by(direction='outbound').all()
            self.assertEqual(len(msgs), 1)
            self.assertEqual(msgs[0].from_name, IBLC_MSG_FROM)
            self.assertEqual(msgs[0].to_name, IBLC_MSG_TO)
            self.assertEqual(msgs[0].subject, IBLC_MSG_SUBJECT)
            # Body is rot47-encoded -- the plain alias/location must NOT
            # appear verbatim (unless rot47 of itself happens to be a
            # no-op for that string, not the case for 'jerry').
            self.assertNotIn('jerry', msgs[0].body)
            self.assertIn(rot47('jerry'), msgs[0].body)

            local = FsxnetLastCaller.query.all()
            self.assertEqual(len(local), 1)
            self.assertEqual(local[0].alias, 'jerry')
            self.assertEqual(local[0].location, 'New York, NY')

    def test_post_lastcall_hide_sysop_blocks_admin(self):
        from anetbbs.echomail.fsxnet_sync import post_lastcall_to_fsxnet
        from anetbbs.models import db, User, EchomailMessage
        with self.app.app_context():
            self.app.config['FSXNET_IBLC_ENABLED'] = True
            self.app.config['FSXNET_HIDE_SYSOP'] = True
            net = self._network()
            self.app.config['FSXNET_NETWORK_ID'] = net.id

            sysop = User(username='stingray', email='s@example.com',
                        password_hash='x', is_admin=True)
            db.session.add(sysop)
            db.session.commit()

            post_lastcall_to_fsxnet(sysop, 'telnet')

            self.assertEqual(EchomailMessage.query.count(), 0)

    def test_post_lastcall_hide_sysop_does_not_block_regular_user(self):
        from anetbbs.echomail.fsxnet_sync import post_lastcall_to_fsxnet
        from anetbbs.models import db, User, EchomailMessage
        with self.app.app_context():
            self.app.config['FSXNET_IBLC_ENABLED'] = True
            self.app.config['FSXNET_HIDE_SYSOP'] = True
            net = self._network()
            self.app.config['FSXNET_NETWORK_ID'] = net.id

            regular = User(username='normaluser', email='n@example.com',
                           password_hash='x', is_admin=False)
            db.session.add(regular)
            db.session.commit()

            post_lastcall_to_fsxnet(regular, 'telnet')

            self.assertEqual(EchomailMessage.query.count(), 1)


class FsxnetInboundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import anetbbs.config as cfg_mod
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI

    @classmethod
    def tearDownClass(cls):
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_app(str(Path(self._tmp.name) / 'b.db'))
        with self.app.app_context():
            from anetbbs.models import db
            db.create_all()

    def _network(self):
        from anetbbs.models import db, EchomailNetwork
        net = EchomailNetwork(name='fsxNet', network_type='binkp',
                              our_address='21:4/999')
        db.session.add(net)
        db.session.commit()
        return net

    def _area(self, net, tag='FSX_DAT'):
        from anetbbs.models import db, EchoArea
        area = EchoArea(network_id=net.id, tag=tag, name=tag,
                        is_active=True, is_subscribed=True, is_sysop_only=True)
        db.session.add(area)
        db.session.commit()
        return area

    def test_inbound_oneliner_materializes(self):
        from anetbbs.echomail.fsxnet_sync import (
            sync_fsxnet_inbound, IBOL_MSG_TO, IBOL_MSG_SUBJECT)
        from anetbbs.models import db, EchomailMessage, FsxnetOneliner
        with self.app.app_context():
            self.app.config['FSXNET_IBOL_ENABLED'] = True
            net = self._network()
            self.app.config['FSXNET_NETWORK_ID'] = net.id
            area = self._area(net)
            db.session.add(EchomailMessage(
                area_id=area.id, network_id=net.id, msg_id='OL-1',
                from_name='Codefenix', to_name=IBOL_MSG_TO, subject=IBOL_MSG_SUBJECT,
                body='Author: Codefenix\nSource: ConstructiveChaos BBS\nOneliner: hi there',
                direction='inbound',
            ))
            db.session.commit()

            ok, out = sync_fsxnet_inbound(self.app, {})
            self.assertTrue(ok, out)
            rows = FsxnetOneliner.query.all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].author, 'Codefenix')
            self.assertEqual(rows[0].source_bbs, 'ConstructiveChaos BBS')
            self.assertEqual(rows[0].body, 'hi there')
            self.assertEqual(rows[0].remote_msg_id, 'OL-1')

    def test_inbound_lastcall_materializes(self):
        from anetbbs.echomail.fsxnet_sync import (
            sync_fsxnet_inbound, rot47, IBLC_MSG_FROM, IBLC_MSG_TO,
            IBLC_MSG_SUBJECT, IBLC_BEGIN, IBLC_END)
        from anetbbs.models import db, EchomailMessage, FsxnetLastCaller
        with self.app.app_context():
            self.app.config['FSXNET_IBLC_ENABLED'] = True
            net = self._network()
            self.app.config['FSXNET_NETWORK_ID'] = net.id
            area = self._area(net)
            fields = ['Codefenix', 'ConstructiveChaos BBS', '10/07/26',
                     '08:00a', 'Somewhere, ON', 'Linux', 'conchaos.synchro.net']
            body = IBLC_BEGIN + '\n' + '\n'.join(rot47(f) for f in fields) + '\n' + IBLC_END
            db.session.add(EchomailMessage(
                area_id=area.id, network_id=net.id, msg_id='LC-1',
                from_name=IBLC_MSG_FROM, to_name=IBLC_MSG_TO, subject=IBLC_MSG_SUBJECT,
                body=body, direction='inbound',
            ))
            db.session.commit()

            ok, out = sync_fsxnet_inbound(self.app, {})
            self.assertTrue(ok, out)
            rows = FsxnetLastCaller.query.all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].alias, 'Codefenix')
            self.assertEqual(rows[0].bbs_name, 'ConstructiveChaos BBS')
            self.assertEqual(rows[0].address, 'conchaos.synchro.net')
            self.assertEqual(rows[0].remote_msg_id, 'LC-1')

    def test_inbound_lastcall_matches_regardless_of_header_casing(self):
        """Real bug found against live fsxNet traffic (2026-10-07): a
        real chunk of inbound IBLC posts arrive as
        "IBBSLastCall"/"IBBSLastCall-Data" rather than the lowercase
        "ibbslastcall"/"ibbslastcall-data" iblc.js itself posts with --
        some other software on the network evidently speaks the same
        wire format with different header casing. These were silently
        falling into skipped_unrecognized before the case-insensitive
        match was added."""
        from anetbbs.echomail.fsxnet_sync import (
            sync_fsxnet_inbound, rot47, IBLC_MSG_TO, IBLC_BEGIN, IBLC_END)
        from anetbbs.models import db, EchomailMessage, FsxnetLastCaller
        with self.app.app_context():
            self.app.config['FSXNET_IBLC_ENABLED'] = True
            net = self._network()
            self.app.config['FSXNET_NETWORK_ID'] = net.id
            area = self._area(net)
            fields = ['Jahmas', 'Some Other BBS', '10/07/26',
                     '09:00a', 'Cape Cod, MA', 'Linux', 'otherbbs.example:23']
            body = IBLC_BEGIN + '\n' + '\n'.join(rot47(f) for f in fields) + '\n' + IBLC_END
            db.session.add(EchomailMessage(
                area_id=area.id, network_id=net.id, msg_id='LC-CASE-1',
                from_name='IBBSLastCall', to_name=IBLC_MSG_TO,
                subject='IBBSLastCall-Data',
                body=body, direction='inbound',
            ))
            db.session.commit()

            ok, out = sync_fsxnet_inbound(self.app, {})
            self.assertTrue(ok, out)
            rows = FsxnetLastCaller.query.all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].alias, 'Jahmas')
            self.assertEqual(rows[0].bbs_name, 'Some Other BBS')
            self.assertEqual(rows[0].remote_msg_id, 'LC-CASE-1')

    def test_unrecognized_message_shape_is_skipped(self):
        """FSX_DAT carries more than one message shape in the real
        network -- something that matches neither IBOL nor IBLC's
        to/from/subject must be silently skipped, not mis-parsed."""
        from anetbbs.echomail.fsxnet_sync import sync_fsxnet_inbound
        from anetbbs.models import db, EchomailMessage, FsxnetOneliner, FsxnetLastCaller
        with self.app.app_context():
            self.app.config['FSXNET_IBOL_ENABLED'] = True
            self.app.config['FSXNET_IBLC_ENABLED'] = True
            net = self._network()
            self.app.config['FSXNET_NETWORK_ID'] = net.id
            area = self._area(net)
            db.session.add(EchomailMessage(
                area_id=area.id, network_id=net.id, msg_id='OTHER-1',
                from_name='SomeOtherBot', to_name='Nodelist', subject='nodelist-data',
                body='not ours at all', direction='inbound',
            ))
            db.session.commit()

            ok, out = sync_fsxnet_inbound(self.app, {})
            self.assertTrue(ok, out)
            self.assertEqual(FsxnetOneliner.query.count(), 0)
            self.assertEqual(FsxnetLastCaller.query.count(), 0)
            self.assertIn('unrecognized', out)

    def test_outbound_rows_are_never_rescanned(self):
        """Our own posts are already materialized at post-time
        (FsxnetOutboundTests above) -- the inbound sync must never
        touch direction='outbound' rows, or a post would double-insert
        locally."""
        from anetbbs.echomail.fsxnet_sync import (
            sync_fsxnet_inbound, IBOL_MSG_TO, IBOL_MSG_SUBJECT)
        from anetbbs.models import db, EchomailMessage, FsxnetOneliner
        with self.app.app_context():
            self.app.config['FSXNET_IBOL_ENABLED'] = True
            net = self._network()
            self.app.config['FSXNET_NETWORK_ID'] = net.id
            area = self._area(net)
            db.session.add(EchomailMessage(
                area_id=area.id, network_id=net.id, msg_id='OUT-1',
                from_name='jerry', to_name=IBOL_MSG_TO, subject=IBOL_MSG_SUBJECT,
                body='Author: jerry\nSource: ANetBBS\nOneliner: my own post',
                direction='outbound',
            ))
            db.session.commit()

            ok, out = sync_fsxnet_inbound(self.app, {})
            self.assertTrue(ok, out)
            self.assertEqual(FsxnetOneliner.query.count(), 0)

    def test_dedup_is_global_across_areas(self):
        from anetbbs.echomail.fsxnet_sync import (
            sync_fsxnet_inbound, IBOL_MSG_TO, IBOL_MSG_SUBJECT)
        from anetbbs.models import db, EchomailMessage, FsxnetOneliner
        with self.app.app_context():
            self.app.config['FSXNET_IBOL_ENABLED'] = True
            net = self._network()
            self.app.config['FSXNET_NETWORK_ID'] = net.id
            area1 = self._area(net, tag='FSX_DAT')
            area2 = self._area(net, tag='FSX_DAT_DUP')
            for area in (area1, area2):
                db.session.add(EchomailMessage(
                    area_id=area.id, network_id=net.id, msg_id='SAME-ID',
                    from_name='X', to_name=IBOL_MSG_TO, subject=IBOL_MSG_SUBJECT,
                    body='Author: X\nSource: Y\nOneliner: dup test',
                    direction='inbound',
                ))
            db.session.commit()
            area2.tag = 'FSX_DAT'
            db.session.commit()

            ok, out = sync_fsxnet_inbound(self.app, {})
            self.assertTrue(ok, out)
            self.assertEqual(FsxnetOneliner.query.count(), 1,
                             'the same msg_id across two areas must only import once')

    def test_null_msg_id_is_skipped(self):
        from anetbbs.echomail.fsxnet_sync import (
            sync_fsxnet_inbound, IBOL_MSG_TO, IBOL_MSG_SUBJECT)
        from anetbbs.models import db, EchomailMessage, FsxnetOneliner
        with self.app.app_context():
            self.app.config['FSXNET_IBOL_ENABLED'] = True
            net = self._network()
            self.app.config['FSXNET_NETWORK_ID'] = net.id
            area = self._area(net)
            db.session.add(EchomailMessage(
                area_id=area.id, network_id=net.id, msg_id=None,
                from_name='X', to_name=IBOL_MSG_TO, subject=IBOL_MSG_SUBJECT,
                body='Author: X\nSource: Y\nOneliner: no msgid',
                direction='inbound',
            ))
            db.session.commit()

            ok, out = sync_fsxnet_inbound(self.app, {})
            self.assertTrue(ok, out)
            self.assertEqual(FsxnetOneliner.query.count(), 0)
            self.assertIn('skipped 1', out)

    def test_does_not_reimport_already_known_message(self):
        from anetbbs.echomail.fsxnet_sync import (
            sync_fsxnet_inbound, IBOL_MSG_TO, IBOL_MSG_SUBJECT)
        from anetbbs.models import db, EchomailMessage, FsxnetOneliner
        with self.app.app_context():
            self.app.config['FSXNET_IBOL_ENABLED'] = True
            net = self._network()
            self.app.config['FSXNET_NETWORK_ID'] = net.id
            area = self._area(net)
            db.session.add(EchomailMessage(
                area_id=area.id, network_id=net.id, msg_id='REPEAT-1',
                from_name='X', to_name=IBOL_MSG_TO, subject=IBOL_MSG_SUBJECT,
                body='Author: X\nSource: Y\nOneliner: once please',
                direction='inbound',
            ))
            db.session.commit()

            sync_fsxnet_inbound(self.app, {})
            sync_fsxnet_inbound(self.app, {})
            self.assertEqual(FsxnetOneliner.query.count(), 1)

    def test_oneliner_and_lastcall_disabled_independently(self):
        """IBOL and IBLC are independent toggles -- a message matching
        IBOL's shape must not be imported when only IBLC is enabled,
        and vice versa."""
        from anetbbs.echomail.fsxnet_sync import (
            sync_fsxnet_inbound, IBOL_MSG_TO, IBOL_MSG_SUBJECT)
        from anetbbs.models import db, EchomailMessage, FsxnetOneliner
        with self.app.app_context():
            self.app.config['FSXNET_IBOL_ENABLED'] = False
            self.app.config['FSXNET_IBLC_ENABLED'] = True
            net = self._network()
            self.app.config['FSXNET_NETWORK_ID'] = net.id
            area = self._area(net)
            db.session.add(EchomailMessage(
                area_id=area.id, network_id=net.id, msg_id='OL-OFF-1',
                from_name='X', to_name=IBOL_MSG_TO, subject=IBOL_MSG_SUBJECT,
                body='Author: X\nSource: Y\nOneliner: should not import',
                direction='inbound',
            ))
            db.session.commit()

            ok, out = sync_fsxnet_inbound(self.app, {})
            self.assertTrue(ok, out)
            self.assertEqual(FsxnetOneliner.query.count(), 0,
                             'IBOL is disabled -- an oneliner-shaped message must not import')


if __name__ == '__main__':
    unittest.main()
