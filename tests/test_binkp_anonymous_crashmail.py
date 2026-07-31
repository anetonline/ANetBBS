"""Regression test for FTN nodelist crashmail compliance.

Real report (a net 123 nodelist coordinator, peer address 2:280/464,
2026-07-31): standard FTN nodelist policy requires a listed node --
unless flagged Hold or Pvt --
to accept crashmail (netmail) from ANY address, not just addresses it
already has configured as an upstream hub or downstream node.
ANetBBS's BinkP listener (anetbbs/echomail/binkp_server.py) previously
rejected every session from an unrecognized address with M_ERR "unknown
address", making it non-compliant.

Fixed by accepting the session (net_id AND downstream_node_id both stay
None) instead of rejecting it. `_import_pkt_payload()` is the actual
import path exercised here -- it now accepts network_id=None and:
  - imports netmail (point-to-point, e.g. crashmail addressed to a real
    local user) with network_id=None
  - drops any echomail in the same packet, since echo distribution still
    requires real network membership (EchoArea/EchomailMessage.network_id
    is NOT NULL; NetmailMessage.network_id is nullable)

The full-session accept/reject behavior (M_OK vs M_ERR) is covered by
test_binkp_multi_hub_identity.py's
test_unknown_address_now_accepted_as_anonymous_crashmail; this file
covers the actual DB-level import consequences, same pattern as
test_binkp_inbound_dedup.py.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _FakeMsg:
    def __init__(self, *, area=None, from_name='Tester', to_name='All',
                subject='Test', body='Hello', msg_id=None, reply_id=None,
                to_address='', from_address=''):
        self.area = area
        self.from_name = from_name
        self.to_name = to_name
        self.subject = subject
        self.body = body
        self.tear_line = None
        self.origin_line = None
        self.kludges = None
        self.seenby = None
        self.path = None
        self.chrs = 'CP437 2'
        self.msg_id = msg_id
        self.reply_id = reply_id
        self.to_address = to_address
        self.from_address = from_address


class _FakeArea:
    def __init__(self, tag):
        self.tag = tag


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


class AnonymousCrashmailTests(unittest.TestCase):
    def setUp(self):
        import anetbbs.config as cfg_mod
        self._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        self.addCleanup(
            lambda: setattr(cfg_mod.TestingConfig,
                            'SQLALCHEMY_DATABASE_URI', self._orig_db_uri))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'anon_crashmail.db'))

    def test_netmail_from_unrecognized_peer_is_imported_with_null_network(self):
        """The actual compliance fix: crashmail from a peer we've never
        configured must still reach a real local user's netmail inbox."""
        from anetbbs.echomail.binkp import _build_ftn_packet
        from anetbbs.echomail import binkp_server
        from anetbbs.models import db, User, NetmailMessage

        with self.app.app_context():
            user = User(username='stingray', email='stingray@example.test',
                       password_hash='x', access_level=100)
            db.session.add(user)
            db.session.commit()
            user_id = user.id

            msg = _FakeMsg(area=None, from_name='Remote Sysop',
                           to_name='stingray', subject='Fwd: nodelist report',
                           body='Not fixed yet: unknown address',
                           msg_id='2:280/464 6a6c6f2e',
                           to_address='1:123/3003', from_address='2:280/464')
            pkt = _build_ftn_packet([msg], '2:280/464', '1:123/3003')

            imported = binkp_server._import_pkt_payload(pkt, None, 'anon.pkt')
            self.assertEqual(imported, 1,
                            'netmail from an unrecognized peer must still import')

            rows = NetmailMessage.query.filter_by(
                msgid='2:280/464 6a6c6f2e').all()
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertIsNone(row.network_id,
                             'anonymous/unrecognized-peer netmail has no '
                             'EchomailNetwork to attach to')
            self.assertEqual(row.to_user_id, user_id,
                            'must still resolve to the real local recipient '
                            'so they actually get notified -- this is the '
                            'whole point of accepting the session at all')

    def test_origin_ip_is_captured_so_a_reply_can_dial_back(self):
        """v1.0.4: the peer's real socket IP gets stored on the imported
        netmail (network_id=None only) so poller.py's
        send_netmail_direct_now() can dial them back directly for a
        reply -- there's no hub in this relationship to route one
        through otherwise."""
        from anetbbs.echomail.binkp import _build_ftn_packet
        from anetbbs.echomail import binkp_server
        from anetbbs.models import NetmailMessage

        with self.app.app_context():
            msg = _FakeMsg(area=None, from_name='Remote Sysop',
                           to_name='stingray', subject='hi',
                           msg_id='2:280/464 origintest',
                           to_address='1:123/3003', from_address='2:280/464')
            pkt = _build_ftn_packet([msg], '2:280/464', '1:123/3003')

            binkp_server._import_pkt_payload(
                pkt, None, 'anon.pkt', origin_ip='203.0.113.7')

            row = NetmailMessage.query.filter_by(
                msgid='2:280/464 origintest').first()
            self.assertEqual(row.origin_ip, '203.0.113.7')

    def test_origin_ip_not_stored_when_network_is_known(self):
        """A session that DOES resolve to a real network already has a
        real hub_address to route a reply through -- origin_ip is
        pointless (and potentially misleading) there, so it must stay
        NULL even if a caller somehow passed one."""
        from anetbbs.echomail.binkp import _build_ftn_packet
        from anetbbs.echomail import binkp_server
        from anetbbs.models import db, EchomailNetwork, NetmailMessage

        with self.app.app_context():
            net = EchomailNetwork(name='TestNet', network_type='binkp',
                                  our_address='1:114/30',
                                  hub_address='1:114/0')
            db.session.add(net)
            db.session.commit()
            net_id = net.id

            msg = _FakeMsg(area=None, from_name='Bob', to_name='Alice',
                           subject='hi', msg_id='1:114/30@fidonet knowntest',
                           to_address='1:114/0', from_address='1:114/30')
            pkt = _build_ftn_packet([msg], '1:114/30', '1:114/0')

            binkp_server._import_pkt_payload(
                pkt, net_id, 'known.pkt', origin_ip='203.0.113.9')

            row = NetmailMessage.query.filter_by(
                msgid='1:114/30@fidonet knowntest').first()
            self.assertIsNone(row.origin_ip)

    def test_echomail_from_unrecognized_peer_is_dropped_not_imported(self):
        """Crashmail compliance is about netmail deliverability only --
        an anonymous/unrecognized peer must NOT be able to inject
        messages into a real echo area (that still requires actual
        network membership/subscription)."""
        from anetbbs.echomail.binkp import _build_ftn_packet
        from anetbbs.echomail import binkp_server
        from anetbbs.models import EchomailMessage

        with self.app.app_context():
            msg = _FakeMsg(area=_FakeArea('GENERAL'), from_name='Rando',
                           to_name='All', subject='Spoofed echo post',
                           msg_id='9:999/999 deadbeef')
            pkt = _build_ftn_packet([msg], '9:999/999', '1:123/3003')

            imported = binkp_server._import_pkt_payload(pkt, None, 'anon.pkt')
            self.assertEqual(imported, 0,
                            'echomail from an unrecognized peer must be '
                            'dropped, not silently imported into a real area')

            rows = EchomailMessage.query.filter_by(
                msg_id='9:999/999 deadbeef').all()
            self.assertEqual(len(rows), 0)

    def test_areafix_command_from_unrecognized_peer_does_not_crash_or_apply(self):
        """An anonymous peer emailing 'areafix' must not be able to
        change subscriptions -- process_request(network=None, ...)
        already fails closed ("Network not configured"); this just
        confirms the whole inbound-listener path reaches that safely
        with network_id=None instead of raising."""
        from anetbbs.echomail.binkp import _build_ftn_packet
        from anetbbs.echomail import binkp_server
        from anetbbs.models import AreafixLog

        with self.app.app_context():
            msg = _FakeMsg(area=None, from_name='Rando', to_name='areafix',
                           subject='pw', body='+SOME_TAG',
                           msg_id='9:999/999 aaaa0001',
                           to_address='1:123/3003', from_address='9:999/999')
            pkt = _build_ftn_packet([msg], '9:999/999', '1:123/3003')

            imported = binkp_server._import_pkt_payload(pkt, None, 'anon.pkt')
            self.assertEqual(imported, 1,
                            'the netmail itself still imports/logs even '
                            'though the command is rejected')

            log = AreafixLog.query.filter_by(from_address='9:999/999').first()
            self.assertIsNotNone(log)
            self.assertFalse(log.success)


if __name__ == '__main__':
    unittest.main()
