"""M-4: unauthenticated BinkP sessions are non-secure and get no peer
privileges.

Before the fix, a configured link with NO session password accepted any
caller that claimed its address (M_PWD '-' or an empty-key CRAM digest):
the session got M_OK "secure", the link's identity, its outbound queue,
and could inject echomail as the hub. Anonymous sessions could also reach
the AreaFix/FileFix robots (a node-password guessing oracle), deliver
netmail to non-local addresses, pre-plant MSGIDs that suppressed later
genuine netmail, and drop arbitrary files into the inbound directory.
"""
import tempfile
import unittest
from pathlib import Path

from tests.test_binkp_multi_hub_identity import (
    _BinkpHandlerHarness, _FakeBinkPNode, _FakeEchomailNetwork,
    _decode_sent_commands)
from tests.test_binkp_anonymous_crashmail import _FakeMsg, _FakeArea, _fresh_app


def _ok_text(writer):
    from anetbbs.echomail.binkp_server import CMD_OK
    return [t for c, t in _decode_sent_commands(writer.sent) if c == CMD_OK]


class SessionTrust(unittest.TestCase, _BinkpHandlerHarness):
    def test_passwordless_network_is_non_secure_and_gets_nothing(self):
        net = _FakeEchomailNetwork(id=1, hub_address='2:2/2', network_type='binkp',
                                   binkp_password=None, our_address='2:2/9')
        pending = [_FakeMsg(area=_FakeArea('GEN'), subject='queued for hub')]
        for pwd in ('-', '', 'anything'):
            with self.subTest(pwd=pwd):
                writer, captured = self._run(networks=[net], nodes=[],
                                             remote_addr='2:2/2', remote_pwd=pwd,
                                             outbound_messages=pending)
                self.assertEqual(_ok_text(writer), ['non-secure'])
                self.assertNotIn('sent_pkt', captured, 'no outbound queue pickup')

    def test_passwordless_downstream_node_is_non_secure(self):
        node = _FakeBinkPNode(id=1, ftn_address='1:200/100', password='')
        writer, captured = self._run(networks=[], nodes=[node],
                                     remote_addr='1:200/100', remote_pwd='-',
                                     outbound_messages=[_FakeMsg(area=_FakeArea('GEN'))])
        self.assertEqual(_ok_text(writer), ['non-secure'])
        self.assertNotIn('sent_pkt', captured)

    def test_password_protected_network_unchanged(self):
        net = _FakeEchomailNetwork(id=1, hub_address='2:2/2', network_type='binkp',
                                   binkp_password='hubpw', our_address='2:2/9')
        writer, _captured = self._run(networks=[net], nodes=[],
                                      remote_addr='2:2/2', remote_pwd='hubpw')
        self.assertEqual(_ok_text(writer), ['secure'])

    def test_unknown_caller_is_non_secure(self):
        writer, _ = self._run(networks=[], nodes=[], remote_addr='9:999/999',
                              remote_pwd='whatever')
        self.assertEqual(_ok_text(writer), ['non-secure'])

    def test_non_secure_session_never_stores_non_packet_files(self):
        src = (Path(__file__).resolve().parent.parent / 'anetbbs' / 'echomail'
               / 'binkp_server.py').read_text()
        self.assertIn("elif net_id is None and downstream_node_id is None:", src)
        self.assertIn('dropped file %s from non-secure session', src)


class NonSecureNetmailImport(unittest.TestCase):
    def setUp(self):
        import anetbbs.config as cfg_mod
        self._orig = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        self.addCleanup(lambda: setattr(cfg_mod.TestingConfig,
                                        'SQLALCHEMY_DATABASE_URI', self._orig))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'm4.db'))
        from anetbbs.models import db, EchomailNetwork, User, UserAka
        with self.app.app_context():
            net = EchomailNetwork(name='Local', network_type='binkp',
                                  our_address='1:123/3003', hub_address='1:123/0',
                                  binkp_password='pw', is_active=True)
            db.session.add(net)
            alice = User(username='alice', email='a@x.test', password_hash='x')
            sysop = User(username='sysop', email='s@x.test', password_hash='x',
                         is_admin=True)
            db.session.add_all([alice, sysop])
            db.session.commit()
            db.session.add(UserAka(user_id=sysop.id, address='1:123/3003.1'))
            db.session.commit()
            self.net_id = net.id

    def _import(self, msg, network_id=None):
        from anetbbs.echomail.binkp import _build_ftn_packet
        from anetbbs.echomail import binkp_server
        pkt = _build_ftn_packet([msg], msg.from_address or '9:999/999', '1:123/3003')
        return binkp_server._import_pkt_payload(pkt, network_id, 't.pkt')

    def _rows(self, msgid):
        from anetbbs.models import NetmailMessage
        return NetmailMessage.query.filter_by(msgid=msgid).all()

    def test_local_user_delivery_is_marked_insecure(self):
        with self.app.app_context():
            n = self._import(_FakeMsg(to_name='alice', msg_id='9:999/999 m1',
                                      to_address='1:123/3003', from_address='9:999/999'))
            self.assertEqual(n, 1)
            (row,) = self._rows('9:999/999 m1')
            self.assertEqual(row.trust, 'insecure')
            self.assertIsNone(row.network_id)

    def test_robot_netmail_dropped_without_reply(self):
        from anetbbs.models import AreafixLog, NetmailMessage
        with self.app.app_context():
            for robot in ('areafix', 'AreaMgr', 'filefix'):
                n = self._import(_FakeMsg(to_name=robot, subject='pw', body='%LIST',
                                          msg_id=f'9:999/999 {robot}',
                                          to_address='1:123/3003', from_address='9:999/999'))
                self.assertEqual(n, 0, robot)
            self.assertEqual(AreafixLog.query.count(), 0)
            self.assertEqual(NetmailMessage.query.filter_by(direction='outbound').count(), 0)

    def test_non_local_destination_dropped(self):
        with self.app.app_context():
            n = self._import(_FakeMsg(to_name='alice', msg_id='9:999/999 m2',
                                      to_address='2:5020/1', from_address='9:999/999'))
            self.assertEqual(n, 0)
            self.assertEqual(self._rows('9:999/999 m2'), [])

    def test_unknown_local_user_dropped(self):
        with self.app.app_context():
            self.assertEqual(self._import(_FakeMsg(
                to_name='nobody', msg_id='9:999/999 m3',
                to_address='1:123/3003', from_address='9:999/999')), 0)

    def test_admin_not_reachable_by_name_only(self):
        with self.app.app_context():
            self.assertEqual(self._import(_FakeMsg(
                to_name='sysop', msg_id='9:999/999 m4',
                to_address='1:123/3003', from_address='9:999/999')), 0)
            # ...but an admin's own AKA is a real address for them.
            self.assertEqual(self._import(_FakeMsg(
                to_name='sysop', msg_id='9:999/999 m5',
                to_address='1:123/3003.1', from_address='9:999/999')), 1)

    def test_insecure_msgid_cannot_suppress_secure_message(self):
        with self.app.app_context():
            planted = _FakeMsg(to_name='alice', msg_id='1:123/0 abcd1234',
                               to_address='1:123/3003', from_address='1:123/0')
            self.assertEqual(self._import(planted), 1)
            genuine = _FakeMsg(to_name='alice', msg_id='1:123/0 abcd1234',
                               subject='real', to_address='1:123/3003',
                               from_address='1:123/0')
            self.assertEqual(self._import(genuine, network_id=self.net_id), 1)
            rows = self._rows('1:123/0 abcd1234')
            self.assertEqual(sorted(r.trust or 'secure' for r in rows),
                             ['insecure', 'secure'])
            # Insecure duplicates of insecure mail are still suppressed.
            self.assertEqual(self._import(planted), 0)

    def test_network_none_defaults_to_insecure(self):
        """Fail-safe default: a caller that passes no network identity and
        no explicit flag gets the non-secure rules."""
        with self.app.app_context():
            self.assertEqual(self._import(_FakeMsg(
                to_name='areafix', msg_id='9:999/999 m6',
                to_address='1:123/3003', from_address='9:999/999')), 0)


class RobotCommandsNeedTheNodesOwnSession(unittest.TestCase):
    """Review finding N-2: AreaFix %PASSWORD (which is also the node's
    BinkP session password) over netmail that did not come from that
    node's own authenticated session -- a relay, an outbound poll, or a
    different node claiming its address -- must be ignored."""

    def setUp(self):
        import anetbbs.config as cfg_mod
        self._orig = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        self.addCleanup(lambda: setattr(cfg_mod.TestingConfig,
                                        'SQLALCHEMY_DATABASE_URI', self._orig))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'n2.db'))
        from anetbbs.models import db, BinkPNode, EchomailNetwork, NetmailMessage
        with self.app.app_context():
            net = EchomailNetwork(name='N2', network_type='binkp', our_address='6:6/1',
                                  binkp_password='hubpw', is_active=True)
            db.session.add(net)
            victim = BinkPNode(name='Victim', ftn_address='6:6/2', password='oldpw',
                               is_active=True)
            other = BinkPNode(name='Other', ftn_address='6:6/3', password='otherpw',
                              is_active=True)
            db.session.add_all([victim, other])
            db.session.flush()
            nm = NetmailMessage(network_id=net.id, from_address='6:6/2', to_address='6:6/1',
                                from_name='x', to_name='areafix', subject='oldpw',
                                body='%PASSWORD newpw\n', direction='inbound',
                                status='received')
            db.session.add(nm)
            db.session.commit()
            self.nm_id, self.victim_id, self.other_id = nm.id, victim.id, other.id

    def _pw(self):
        from anetbbs.models import db, BinkPNode
        return db.session.get(BinkPNode, self.victim_id).password

    def test_other_nodes_session_or_no_session_is_ignored(self):
        from anetbbs.echomail.areafix import handle_areafix_netmail
        with self.app.app_context():
            self.assertIsNone(handle_areafix_netmail(self.nm_id))
            self.assertIsNone(handle_areafix_netmail(self.nm_id, session_node_id=self.other_id))
            self.assertEqual(self._pw(), 'oldpw')

    def test_nodes_own_session_is_honoured(self):
        from anetbbs.echomail.areafix import handle_areafix_netmail
        with self.app.app_context():
            self.assertIsNotNone(handle_areafix_netmail(self.nm_id,
                                                        session_node_id=self.victim_id))
            self.assertEqual(self._pw(), 'newpw')


if __name__ == '__main__':
    unittest.main()
