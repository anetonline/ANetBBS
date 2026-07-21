"""Regression tests for a new feature: notify a local user when an inbound
echomail (FidoNet) or QWK-network conference message arrives addressed TO
their handle in a public area -- the Synchronet-style "so-and-so replied
to you in FidoNet (area name)" notice.

Both network types share one message model/import pipeline
(anetbbs.models.EchomailMessage), so one helper
(anetbbs.echomail.notify_reply.maybe_notify_recipient) covers both, reusing
anetbbs.echomail.routing.resolve_user_by_name_or_address (extracted from
resolve_netmail_recipient, which keeps its own netmail-only
default-recipient fallback on top of it).

Three real inbound-import call sites are covered, matching the netmail
notification precedent (tests/test_netmail_notification.py):
  1. poller.py's _import_message() -- the BinkP-poll and QWK-REP unified path.
  2. binkp_server.py's _import_pkt_payload() -- the real-time BinkP listener.
  3. qwk_hub_ftp.py's process_rep_upload() -- a hub receiving a REP upload
     from a downstream node (a hub's own local users read the same shared
     areas, so this needs the same notice).

Also covers the terminal "while already online" delivery mechanism
(anetbbs.features.notify.check_new_notifications), which the login-time
banner (session.py's _show_notification_summary) already covers via the
generic Notification unread count/listing -- not retested here since it's
unchanged plumbing, just fed by a new notification kind.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _fresh_app(db_path):
    import anetbbs.config as cfg_mod
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    os.environ['FLASK_ENV'] = 'testing'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    return app


class _BaseTestCase(unittest.TestCase):
    def setUp(self):
        import anetbbs.config as cfg_mod
        self._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        self.addCleanup(
            lambda: setattr(cfg_mod.TestingConfig,
                            'SQLALCHEMY_DATABASE_URI', self._orig_db_uri))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _dbpath(self, name):
        return str(Path(self._tmp.name) / name)


class ResolveUserByNameOrAddressTests(_BaseTestCase):
    def test_matches_by_aka_address(self):
        app = _fresh_app(self._dbpath('aka.db'))
        from anetbbs.models import db, User, UserAka
        from anetbbs.echomail.routing import resolve_user_by_name_or_address
        with app.app_context():
            u = User(username='alice', email='alice@example.com', password_hash='x')
            db.session.add(u)
            db.session.commit()
            db.session.add(UserAka(user_id=u.id, address='1:1/2'))
            db.session.commit()
            found = resolve_user_by_name_or_address('someone else', '1:1/2')
            self.assertIsNotNone(found)
            self.assertEqual(found.id, u.id)

    def test_matches_by_username_case_insensitive(self):
        app = _fresh_app(self._dbpath('uname.db'))
        from anetbbs.models import db, User
        from anetbbs.echomail.routing import resolve_user_by_name_or_address
        with app.app_context():
            u = User(username='Bob', email='bob@example.com', password_hash='x')
            db.session.add(u)
            db.session.commit()
            found = resolve_user_by_name_or_address('bob', '')
            self.assertIsNotNone(found)
            self.assertEqual(found.id, u.id)

    def test_no_default_recipient_fallback(self):
        # Unlike resolve_netmail_recipient, this helper has NO network
        # catch-all fallback -- a public echomail/QWK message that
        # doesn't match a real user must resolve to None, not some
        # "default recipient" convention that only makes sense for
        # point-to-point netmail.
        app = _fresh_app(self._dbpath('nofallback.db'))
        from anetbbs.echomail.routing import resolve_user_by_name_or_address
        with app.app_context():
            self.assertIsNone(resolve_user_by_name_or_address('nobody', ''))


class MaybeNotifyRecipientTests(_BaseTestCase):
    def _fake_msg(self, to_name, from_name='Jane', to_address=''):
        class _M:
            pass
        m = _M()
        m.id = 42
        m.to_name = to_name
        m.to_address = to_address
        m.from_name = from_name
        return m

    def _fake_area(self):
        class _A:
            pass
        a = _A()
        a.id = 7
        a.name = 'General Chat'
        return a

    def _fake_network(self):
        class _N:
            pass
        n = _N()
        n.name = 'FidoNet'
        return n

    def test_notifies_matching_real_user(self):
        app = _fresh_app(self._dbpath('notify.db'))
        from anetbbs.models import db, User, Notification
        from anetbbs.echomail.notify_reply import maybe_notify_recipient
        with app.app_context():
            u = User(username='pete', email='pete@example.com', password_hash='x')
            db.session.add(u)
            db.session.commit()

            msg = self._fake_msg('pete', from_name='Jane')
            maybe_notify_recipient(msg, self._fake_area(), self._fake_network())

            notif = Notification.query.filter_by(user_id=u.id, kind='echomail_reply').first()
            self.assertIsNotNone(notif)
            self.assertEqual(notif.title, 'Jane wrote to you')
            self.assertEqual(notif.body, 'in FidoNet (General Chat)')
            self.assertEqual(notif.target_url, '/echomail/7/42')
            self.assertFalse(notif.is_read)

    def test_skips_all_alias(self):
        app = _fresh_app(self._dbpath('skipall.db'))
        from anetbbs.models import Notification
        from anetbbs.echomail.notify_reply import maybe_notify_recipient
        with app.app_context():
            for alias in ('All', 'ALL', 'everyone', 'Sysop'):
                msg = self._fake_msg(alias)
                maybe_notify_recipient(msg, self._fake_area(), self._fake_network())
            self.assertEqual(Notification.query.count(), 0)

    def test_skips_when_no_local_user_matches(self):
        app = _fresh_app(self._dbpath('skipnomatch.db'))
        from anetbbs.models import Notification
        from anetbbs.echomail.notify_reply import maybe_notify_recipient
        with app.app_context():
            msg = self._fake_msg('SomeRandomHandle')
            maybe_notify_recipient(msg, self._fake_area(), self._fake_network())
            self.assertEqual(Notification.query.count(), 0)


class PollerImportMessageNotificationTests(_BaseTestCase):
    def _make_network_and_area(self, app):
        from anetbbs.models import db, EchomailNetwork, EchoArea
        with app.app_context():
            net = EchomailNetwork(name='TestNet', network_type='binkp',
                                  our_address='1:1/1')
            db.session.add(net)
            db.session.flush()
            area = EchoArea(network_id=net.id, tag='TEST_AREA', name='Test Area',
                            is_subscribed=True, is_active=True)
            db.session.add(area)
            db.session.commit()
            return net.id

    def test_reply_addressed_to_real_user_creates_notification(self):
        app = _fresh_app(self._dbpath('poller_notify.db'))
        net_id = self._make_network_and_area(app)
        from anetbbs.models import db, User, EchomailNetwork, Notification, EchomailMessage
        from anetbbs.echomail.poller import _import_message
        with app.app_context():
            u = User(username='quinn', email='quinn@example.com', password_hash='x')
            db.session.add(u)
            db.session.commit()

            net = EchomailNetwork.query.get(net_id)
            rc = _import_message(net, {
                'area_tag': 'TEST_AREA', 'msg_id': 'poll1',
                'from_name': 'Rita', 'to_name': 'Quinn',
                'subject': 'Re: your question', 'body': 'here is the answer',
            })
            self.assertEqual(rc, 1)

            msg = EchomailMessage.query.filter_by(msg_id='poll1').first()
            self.assertIsNotNone(msg)
            notif = Notification.query.filter_by(user_id=u.id, kind='echomail_reply').first()
            self.assertIsNotNone(notif)
            self.assertIn('Rita', notif.title)
            self.assertIn('TestNet', notif.body)
            self.assertIn('Test Area', notif.body)
            self.assertEqual(notif.target_url, f'/echomail/{msg.area_id}/{msg.id}')

    def test_reply_addressed_to_all_creates_no_notification(self):
        app = _fresh_app(self._dbpath('poller_noall.db'))
        net_id = self._make_network_and_area(app)
        from anetbbs.models import EchomailNetwork, Notification
        from anetbbs.echomail.poller import _import_message
        with app.app_context():
            net = EchomailNetwork.query.get(net_id)
            rc = _import_message(net, {
                'area_tag': 'TEST_AREA', 'msg_id': 'poll2',
                'from_name': 'Sam', 'to_name': 'All',
                'subject': 'General announcement', 'body': 'hello area',
            })
            self.assertEqual(rc, 1)
            self.assertEqual(Notification.query.count(), 0)


class BinkpListenerImportNotificationTests(_BaseTestCase):
    def _make_network(self, app):
        from anetbbs.models import db, EchomailNetwork
        with app.app_context():
            net = EchomailNetwork(name='ListenerNet', network_type='binkp',
                                  our_address='1:114/30', hub_address='1:114/0')
            db.session.add(net)
            db.session.commit()
            return net.id

    def test_real_time_inbound_echomail_reply_creates_notification(self):
        app = _fresh_app(self._dbpath('binkp_listener_notify.db'))
        net_id = self._make_network(app)
        from anetbbs.models import db, User, Notification
        from anetbbs.echomail.binkp import _build_ftn_packet
        from anetbbs.echomail import binkp_server

        with app.app_context():
            u = User(username='terry', email='terry@example.com', password_hash='x')
            db.session.add(u)
            db.session.commit()
            user_id = u.id

        class _FakeMsg:
            def __init__(self):
                self.area = _FakeArea('LIVEAREA')
                self.from_name = 'Uma'
                self.to_name = 'Terry'
                self.subject = 'Live reply'
                self.body = 'hi there'
                self.tear_line = None
                self.origin_line = None
                self.kludges = None
                self.seenby = None
                self.path = None
                self.chrs = 'CP437 2'
                self.msg_id = '1:114/30@fidonet livemsg0001'
                self.reply_id = None
                self.to_address = ''
                self.from_address = ''

        class _FakeArea:
            def __init__(self, tag):
                self.tag = tag

        pkt = _build_ftn_packet([_FakeMsg()], '1:114/30', '1:114/0')

        with app.app_context():
            rc = binkp_server._import_pkt_payload(pkt, net_id, 'live.pkt')
            self.assertEqual(rc, 1)
            notif = Notification.query.filter_by(user_id=user_id, kind='echomail_reply').first()
            self.assertIsNotNone(notif, 'real-time BinkP listener path must notify too')
            self.assertIn('Uma', notif.title)


class QwkHubRepUploadNotificationTests(_BaseTestCase):
    def test_hub_side_rep_upload_reply_creates_notification(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea, QWKNode, QWKNodeLastSent, User
        from anetbbs.echomail.qwk import _build_rep_packet
        from anetbbs.echomail import qwk_hub_ftp

        app = _fresh_app(self._dbpath('qwk_hub_notify.db'))
        with app.app_context():
            net = EchomailNetwork(name='HubNet', network_type='qwk')
            db.session.add(net)
            db.session.flush()
            area = EchoArea(network_id=net.id, tag='55', name='Hub Area', is_active=True)
            db.session.add(area)
            node = QWKNode(packet_id='REPNODE', name='Rep Node', password='x', is_active=True)
            db.session.add(node)
            db.session.flush()
            sub = QWKNodeLastSent(node_id=node.id, echo_area_id=area.id, conf_number=55)
            db.session.add(sub)
            u = User(username='vic', email='vic@example.com', password_hash='x')
            db.session.add(u)
            db.session.commit()
            node_id = node.id
            user_id = u.id

        class _FakeMsg:
            def __init__(self, id_, subject, body, to_name, from_name, conf_num):
                self.id = id_
                self.subject = subject
                self.body = body
                self.to_name = to_name
                self.from_name = from_name
                self.direction = 'outbound'
                self._qwk_conf_num = conf_num
                self.msg_id = None

        rep_bytes = _build_rep_packet(
            [_FakeMsg(1, 'Reply for Vic', 'body text', 'Vic', 'Wendy', conf_num=55)],
            'REPNODE', hub_id='ANET')

        with tempfile.TemporaryDirectory() as tmpdir:
            rep_path = os.path.join(tmpdir, 'REPNODE.rep')
            with open(rep_path, 'wb') as f:
                f.write(rep_bytes)
            count = qwk_hub_ftp.process_rep_upload(node_id, rep_path, app)

        self.assertEqual(count, 1)
        with app.app_context():
            from anetbbs.models import Notification
            notif = Notification.query.filter_by(user_id=user_id, kind='echomail_reply').first()
            self.assertIsNotNone(notif, 'hub-side REP upload must notify its own local users too')
            self.assertIn('Wendy', notif.title)


if __name__ == '__main__':
    unittest.main()
