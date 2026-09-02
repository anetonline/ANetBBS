"""Regression tests for the FileFix bot (anetbbs/echomail/filefix.py) --
the file-echo counterpart to the existing AreaFix bot
(anetbbs/echomail/areafix.py). Before this, inbound netmail addressed to
"filefix" fell through completely unhandled in binkp_server.py's dispatch
-- the underlying subscription data model (FileArea.is_subscribed,
FileEchoSubscription) and the outbound half (send_areafix_request(...,
robot_name='FileFix')) already existed, only the netmail-robot front-end
was missing.

Covers: parse_request reuse (grammar is already generic, not retested
here -- see areafix's own coverage), process_request (leaf side, on
FileArea.is_subscribed), _process_node_request (hub side, on
FileEchoSubscription -- keyed by peer_address string, NOT a BinkPNode FK
like AreaFix's EchoAreaNode, a real structural difference), and
handle_filefix_netmail end-to-end for both the leaf and hub paths via a
simulated netmail exchange (no live BinkP session -- that's a manual-QA
step, not something this suite can cover).
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class FilefixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.filefix_test.db')
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

    def test_leaf_side_subscribe_and_unsubscribe(self):
        from anetbbs.models import db, EchomailNetwork, FileArea, AreafixLog
        from anetbbs.echomail.filefix import process_request

        with self.app.app_context():
            net = EchomailNetwork(name='FilefixLeafNet', network_type='binkp',
                                  our_address='1:1/1', areafix_password='leafpw123')
            db.session.add(net)
            db.session.flush()
            a1 = FileArea(tag='FF.SUBBED', name='Subbed', network_id=net.id,
                          is_active=True, is_subscribed=True)
            a2 = FileArea(tag='FF.NOTSUBBED', name='Not subbed', network_id=net.id,
                          is_active=True, is_subscribed=False)
            db.session.add_all([a1, a2])
            db.session.commit()

            response, log_kwargs = process_request(
                net, '1:1/2', 'leafpw123', '-FF.SUBBED\n+FF.NOTSUBBED\n')

            self.assertIn('-FF.SUBBED : unsubscribed', response)
            self.assertIn('+FF.NOTSUBBED : subscribed', response)
            self.assertEqual(log_kwargs['bot'], 'filefix')
            self.assertTrue(log_kwargs['success'])

            refreshed_a1 = FileArea.query.filter_by(tag='FF.SUBBED').first()
            refreshed_a2 = FileArea.query.filter_by(tag='FF.NOTSUBBED').first()
            self.assertFalse(refreshed_a1.is_subscribed)
            self.assertTrue(refreshed_a2.is_subscribed)

    def test_leaf_side_no_password_configured_rejects_not_bypasses(self):
        """SECURITY: same fix as areafix.process_request() -- a network
        with no areafix_password/binkp_password configured must fail
        CLOSED, not apply subscription changes to anyone who can get
        netmail addressed to "filefix" relayed to this system."""
        from anetbbs.models import db, EchomailNetwork, FileArea
        from anetbbs.echomail.filefix import process_request

        with self.app.app_context():
            net = EchomailNetwork(name='FilefixNoPwNet', network_type='binkp',
                                  our_address='1:1/1')
            db.session.add(net)
            db.session.flush()
            area = FileArea(tag='FF.NOPW', name='No Pw', network_id=net.id,
                            is_active=True, is_subscribed=False)
            db.session.add(area)
            db.session.commit()

            response, log_kwargs = process_request(
                net, '1:1/2', '', '+FF.NOPW\n')

            self.assertFalse(log_kwargs['success'])
            self.assertEqual(log_kwargs['request_type'], 'badpw')
            refreshed = FileArea.query.filter_by(tag='FF.NOPW').first()
            self.assertFalse(refreshed.is_subscribed,
                             'no changes must be applied without a real password')

    def test_leaf_side_percent_list(self):
        from anetbbs.models import db, EchomailNetwork, FileArea
        from anetbbs.echomail.filefix import process_request

        with self.app.app_context():
            net = EchomailNetwork(name='FilefixListNet', network_type='binkp',
                                  our_address='1:1/1', areafix_password='listpw123')
            db.session.add(net)
            db.session.flush()
            db.session.add(FileArea(tag='FF.LISTED', name='Listed', network_id=net.id,
                                    is_active=True, is_subscribed=True))
            db.session.commit()

            response, _ = process_request(net, '1:1/2', 'listpw123', '%LIST\n')
            self.assertIn('FF.LISTED', response)

    def test_leaf_side_plain_list_request_logs_as_query_not_unsubscribe(self):
        """Same fix as areafix.py: a plain %LIST/%HELP/%QUERY request
        (no +/-) must not be mislabeled 'unsubscribe' in the log."""
        from anetbbs.models import db, EchomailNetwork
        from anetbbs.echomail.filefix import process_request

        with self.app.app_context():
            net = EchomailNetwork(name='FilefixQueryLogNet', network_type='binkp',
                                  our_address='6:7/1', areafix_password='querypw123')
            db.session.add(net)
            db.session.commit()

            response, log_kwargs = process_request(net, '6:7/2', 'querypw123', '%LIST\n')
            self.assertEqual(log_kwargs['request_type'], 'query')

    def test_hub_side_subscribe_creates_file_echo_subscription_not_echoareanode(self):
        from anetbbs.models import db, EchomailNetwork, FileArea, FileEchoSubscription, EchoAreaNode
        from anetbbs.echomail.filefix import _process_node_request

        with self.app.app_context():
            net = EchomailNetwork(name='FilefixHubTestNet', network_type='binkp',
                                  our_address='2:2/1')
            db.session.add(net)
            db.session.flush()
            area = FileArea(tag='FF.HUBTEST', name='Hub Test', network_id=net.id,
                            is_active=True, is_subscribed=True)
            db.session.add(area)
            db.session.commit()

            response, log_kwargs = _process_node_request(
                '2:2/2', '2:2/2', 'testpw123', '+FF.HUBTEST\n', 'testpw123', net.id)

            self.assertIn('+FF.HUBTEST : subscribed', response)
            sub = FileEchoSubscription.query.filter_by(
                file_area_id=area.id, peer_address='2:2/2').first()
            self.assertIsNotNone(sub)
            self.assertTrue(sub.is_active)
            # Confirm this went through FileEchoSubscription, not
            # EchoAreaNode (the message-echo table AreaFix's hub side uses).
            self.assertEqual(EchoAreaNode.query.count(), 0)

    def test_hub_side_unsubscribe_removes_row(self):
        from anetbbs.models import db, EchomailNetwork, FileArea, FileEchoSubscription
        from anetbbs.echomail.filefix import _process_node_request

        with self.app.app_context():
            net = EchomailNetwork(name='FilefixHubUnsubNet', network_type='binkp',
                                  our_address='3:3/1')
            db.session.add(net)
            db.session.flush()
            area = FileArea(tag='FF.HUBUNSUB', name='Hub Unsub', network_id=net.id,
                            is_active=True, is_subscribed=True)
            db.session.add(area)
            db.session.flush()
            db.session.add(FileEchoSubscription(
                file_area_id=area.id, peer_address='3:3/3', is_active=True))
            db.session.commit()

            response, _ = _process_node_request(
                '3:3/3', '3:3/3', 'testpw123', '-FF.HUBUNSUB\n', 'testpw123', net.id)
            self.assertIn('-FF.HUBUNSUB : unsubscribed', response)
            self.assertIsNone(FileEchoSubscription.query.filter_by(
                file_area_id=area.id, peer_address='3:3/3').first())

    def test_handle_filefix_netmail_leaf_path_end_to_end(self):
        from anetbbs.models import db, EchomailNetwork, FileArea, NetmailMessage
        from anetbbs.echomail.filefix import handle_filefix_netmail

        with self.app.app_context():
            net = EchomailNetwork(name='FilefixE2ELeaf', network_type='binkp',
                                  our_address='1:1/1', hub_address='1:1/2',
                                  areafix_password='e2eleaffilefixpw')
            db.session.add(net)
            db.session.flush()
            area = FileArea(tag='FF.E2ELEAF', name='E2E Leaf', network_id=net.id,
                            is_active=True, is_subscribed=False)
            db.session.add(area)
            db.session.flush()

            inbound = NetmailMessage(
                network_id=net.id, from_address='1:1/2', to_address='1:1/1',
                from_name='Hub', to_name='filefix', subject='e2eleaffilefixpw',
                body='+FF.E2ELEAF\n', direction='inbound', status='received')
            db.session.add(inbound)
            db.session.commit()
            nm_id = inbound.id

            response = handle_filefix_netmail(nm_id)
            self.assertIsNotNone(response)
            self.assertIn('+FF.E2ELEAF : subscribed', response)

            refreshed_area = FileArea.query.filter_by(tag='FF.E2ELEAF').first()
            self.assertTrue(refreshed_area.is_subscribed)

            reply = (NetmailMessage.query
                    .filter_by(direction='outbound', from_name='FileFix')
                    .order_by(NetmailMessage.id.desc()).first())
            self.assertIsNotNone(reply, 'expected a queued FileFix reply netmail')
            self.assertEqual(reply.to_address, '1:1/2')
            self.assertEqual(reply.status, 'queued')

    def test_handle_filefix_netmail_with_no_resolvable_network_still_queues_a_reply(self):
        """Real Low finding from a security/performance audit
        (2026-09-02) -- identical gap to areafix.py's
        handle_areafix_netmail(): when neither a real network nor a
        known downstream node resolves, process_request(None, ...)'s
        real, informative response was silently discarded with no
        matching branch to queue it."""
        from anetbbs.models import db, NetmailMessage
        from anetbbs.echomail.filefix import handle_filefix_netmail

        with self.app.app_context():
            inbound = NetmailMessage(
                network_id=None, from_address='9:9/8', to_address='1:1/1',
                from_name='Stranger', to_name='filefix', subject='whatever',
                body='+SOME.AREA\n', direction='inbound', status='received')
            db.session.add(inbound)
            db.session.commit()
            nm_id = inbound.id

            response = handle_filefix_netmail(nm_id)
            self.assertIn('not configured', response.lower())

            reply = (NetmailMessage.query
                    .filter_by(direction='outbound', from_name='FileFix',
                               to_address='9:9/8')
                    .order_by(NetmailMessage.id.desc()).first())
            self.assertIsNotNone(
                reply, 'the "network not configured" reply must still be '
                       'queued, not silently discarded')
            self.assertEqual(reply.status, 'queued')
            self.assertIn('not configured', reply.body.lower())

    def test_handle_filefix_netmail_hub_path_end_to_end(self):
        from anetbbs.models import (db, EchomailNetwork, FileArea, NetmailMessage,
                                    BinkPNode, FileEchoSubscription)
        from anetbbs.echomail.filefix import handle_filefix_netmail

        with self.app.app_context():
            net = EchomailNetwork(name='FilefixE2EHub', network_type='binkp',
                                  our_address='4:4/1')
            db.session.add(net)
            db.session.flush()
            node = BinkPNode(name='Downstream', ftn_address='4:4/2',
                             password='x', is_active=True, network_id=net.id)
            db.session.add(node)
            area = FileArea(tag='FF.E2EHUB', name='E2E Hub', network_id=net.id,
                            is_active=True, is_subscribed=True)
            db.session.add(area)
            db.session.flush()

            inbound = NetmailMessage(
                network_id=net.id, from_address='4:4/2', to_address='4:4/1',
                from_name='Downstream', to_name='filefix', subject='x',
                body='+FF.E2EHUB\n', direction='inbound', status='received')
            db.session.add(inbound)
            db.session.commit()
            nm_id = inbound.id

            response = handle_filefix_netmail(nm_id)
            self.assertIn('+FF.E2EHUB : subscribed', response)

            sub = FileEchoSubscription.query.filter_by(
                file_area_id=area.id, peer_address='4:4/2').first()
            self.assertIsNotNone(sub, 'hub-side dispatch should have created a '
                                 'FileEchoSubscription row for the downstream node')

            reply = (NetmailMessage.query
                    .filter_by(direction='outbound', from_name='FileFix')
                    .order_by(NetmailMessage.id.desc()).first())
            self.assertIsNotNone(reply)
            self.assertEqual(reply.to_address, '4:4/2')

    def test_binkp_server_dispatches_filefix_by_to_name(self):
        """Confirm the binkp_server.py dispatch table actually recognizes
        the filefix robot names -- the one previously-missing piece."""
        import inspect
        from anetbbs.echomail import binkp_server
        src = inspect.getsource(binkp_server._import_pkt_payload)
        self.assertIn("'filefix', 'file fix', 'filemgr'", src)
        self.assertIn('handle_filefix_netmail', src)

    def test_leaf_side_wrong_password_rejects_without_applying_changes(self):
        """Regression test for a real security gap found in a full
        echomail-subsystem audit: process_request()/_process_node_request()
        used to apply subscription changes from ANY inbound netmail
        addressed to filefix/areafix, with no password verification at
        all -- despite send_areafix_request() carefully placing the
        password in the Subject line on the way out. Confirms the fix:
        a wrong (or missing) password now rejects the request and makes
        NO changes, matching Synchronet sbbsecho.c's own behaviour."""
        from anetbbs.models import db, EchomailNetwork, FileArea
        from anetbbs.echomail.filefix import process_request

        with self.app.app_context():
            net = EchomailNetwork(name='FilefixPwNet', network_type='binkp',
                                  our_address='1:1/1',
                                  areafix_password='correcthorse')
            db.session.add(net)
            db.session.flush()
            area = FileArea(tag='FF.PWTEST', name='Pw Test', network_id=net.id,
                            is_active=True, is_subscribed=False)
            db.session.add(area)
            db.session.commit()

            response, log_kwargs = process_request(
                net, '1:1/2', 'wrongpassword', '+FF.PWTEST\n')

            self.assertFalse(log_kwargs['success'])
            self.assertEqual(log_kwargs['request_type'], 'badpw')
            self.assertIn('password', response.lower())

            refreshed = FileArea.query.filter_by(tag='FF.PWTEST').first()
            self.assertFalse(refreshed.is_subscribed,
                             'a bad password must not apply any changes')

    def test_leaf_side_correct_password_still_applies_changes(self):
        from anetbbs.models import db, EchomailNetwork, FileArea
        from anetbbs.echomail.filefix import process_request

        with self.app.app_context():
            net = EchomailNetwork(name='FilefixPwOkNet', network_type='binkp',
                                  our_address='1:1/1',
                                  areafix_password='correcthorse')
            db.session.add(net)
            db.session.flush()
            area = FileArea(tag='FF.PWOK', name='Pw Ok', network_id=net.id,
                            is_active=True, is_subscribed=False)
            db.session.add(area)
            db.session.commit()

            response, log_kwargs = process_request(
                net, '1:1/2', 'correcthorse', '+FF.PWOK\n')

            self.assertTrue(log_kwargs['success'])
            refreshed = FileArea.query.filter_by(tag='FF.PWOK').first()
            self.assertTrue(refreshed.is_subscribed)

    def test_hub_side_wrong_node_password_rejects_without_applying_changes(self):
        from anetbbs.models import db, FileArea, FileEchoSubscription
        from anetbbs.echomail.filefix import _process_node_request

        with self.app.app_context():
            area = FileArea(tag='FF.HUBPW', name='Hub Pw',
                            is_active=True, is_subscribed=True)
            db.session.add(area)
            db.session.commit()

            response, log_kwargs = _process_node_request(
                '5:5/5', '5:5/5', 'wrongpassword', '+FF.HUBPW\n', 'realpassword', None)

            self.assertFalse(log_kwargs['success'])
            self.assertEqual(log_kwargs['request_type'], 'badpw')
            self.assertIsNone(FileEchoSubscription.query.filter_by(
                file_area_id=area.id, peer_address='5:5/5').first())

    def test_handle_filefix_netmail_hub_path_wrong_password_makes_no_changes(self):
        """End-to-end: a forged From: address matching a real downstream
        node, but without that node's real password, must not be able to
        alter that node's subscriptions -- the exact attack the fix in
        this session closes (a message's From: header alone was
        previously sufficient, with no tie to the actual authenticated
        BinkP session)."""
        from anetbbs.models import (db, EchomailNetwork, FileArea, NetmailMessage,
                                    BinkPNode, FileEchoSubscription)
        from anetbbs.echomail.filefix import handle_filefix_netmail

        with self.app.app_context():
            net = EchomailNetwork(name='FilefixE2EHubBadPw', network_type='binkp',
                                  our_address='6:6/1')
            db.session.add(net)
            node = BinkPNode(name='Downstream', ftn_address='6:6/2',
                             password='realsecret', is_active=True)
            db.session.add(node)
            area = FileArea(tag='FF.E2EHUBPW', name='E2E Hub Pw',
                            is_active=True, is_subscribed=True)
            db.session.add(area)
            db.session.flush()

            inbound = NetmailMessage(
                network_id=net.id, from_address='6:6/2', to_address='6:6/1',
                from_name='Forger', to_name='filefix', subject='guessedwrong',
                body='+FF.E2EHUBPW\n', direction='inbound', status='received')
            db.session.add(inbound)
            db.session.commit()
            nm_id = inbound.id

            response = handle_filefix_netmail(nm_id)
            self.assertIn('password', response.lower())

            self.assertIsNone(FileEchoSubscription.query.filter_by(
                file_area_id=area.id, peer_address='6:6/2').first(),
                'wrong password must not create a subscription')

    def test_hub_side_sysop_only_area_cannot_be_subscribed_via_tag(self):
        """Same fix as areafix.py's hub side, identical rationale: a
        sysop-only file area must not be subscribable via a plain +TAG
        from any downstream node."""
        from anetbbs.models import db, EchomailNetwork, FileArea, FileEchoSubscription
        from anetbbs.echomail.filefix import _process_node_request

        with self.app.app_context():
            net = EchomailNetwork(name='FilefixSysopOnlyNet', network_type='binkp',
                                  our_address='3:3/1b')
            db.session.add(net)
            db.session.flush()
            area = FileArea(tag='FF.SYSOPONLY', name='Sysop Only', network_id=net.id,
                            is_active=True, is_subscribed=True, is_sysop_only=True)
            db.session.add(area)
            db.session.commit()

            response, log_kwargs = _process_node_request(
                '3:3/2', '3:3/2', '', '+FF.SYSOPONLY\n', '', net.id)

            self.assertNotIn('subscribed', response)
            sub = FileEchoSubscription.query.filter_by(
                file_area_id=area.id, peer_address='3:3/2').first()
            self.assertIsNone(sub)

    def test_hub_side_plus_all_excludes_sysop_only_areas(self):
        from anetbbs.models import db, EchomailNetwork, FileArea, FileEchoSubscription
        from anetbbs.echomail.filefix import _process_node_request

        with self.app.app_context():
            net = EchomailNetwork(name='FilefixPlusAllNet', network_type='binkp',
                                  our_address='3:4/1')
            db.session.add(net)
            db.session.flush()
            public_area = FileArea(tag='FF.PUBLIC', name='Public', network_id=net.id,
                                   is_active=True, is_subscribed=True,
                                   is_sysop_only=False)
            sysop_area = FileArea(tag='FF.INTERNAL', name='Internal', network_id=net.id,
                                  is_active=True, is_subscribed=True,
                                  is_sysop_only=True)
            db.session.add_all([public_area, sysop_area])
            db.session.commit()

            _process_node_request('3:4/2', '3:4/2', 'testpw123', '+ALL\n', 'testpw123', net.id)

            self.assertIsNotNone(FileEchoSubscription.query.filter_by(
                file_area_id=public_area.id, peer_address='3:4/2').first())
            self.assertIsNone(FileEchoSubscription.query.filter_by(
                file_area_id=sysop_area.id, peer_address='3:4/2').first(),
                '+ALL must never sweep in a sysop-only file area')

    def test_hub_side_plus_all_never_subscribes_areas_from_other_networks(self):
        """Real bug reported live, same class as areafix's: a downstream
        node's +ALL subscribed it to file areas from EVERY network this
        hub relays, not just the one it's actually a member of."""
        from anetbbs.models import db, EchomailNetwork, FileArea, FileEchoSubscription
        from anetbbs.echomail.filefix import _process_node_request

        with self.app.app_context():
            own_net = EchomailNetwork(name='FilefixCrossNetOwn', network_type='binkp',
                                      our_address='9:1/1')
            other_net = EchomailNetwork(name='FilefixCrossNetOther', network_type='binkp',
                                        our_address='9:2/1')
            db.session.add_all([own_net, other_net])
            db.session.flush()
            own_area = FileArea(tag='FF.OWNNET', name='Own Net', network_id=own_net.id,
                                is_active=True, is_subscribed=True)
            other_area = FileArea(tag='FF.OTHERNET', name='Other Net', network_id=other_net.id,
                                  is_active=True, is_subscribed=True)
            db.session.add_all([own_area, other_area])
            db.session.commit()

            _process_node_request('9:1/2', '9:1/2', 'testpw123', '+ALL\n',
                                  'testpw123', own_net.id)

            self.assertIsNotNone(FileEchoSubscription.query.filter_by(
                file_area_id=own_area.id, peer_address='9:1/2').first())
            self.assertIsNone(FileEchoSubscription.query.filter_by(
                file_area_id=other_area.id, peer_address='9:1/2').first(),
                "+ALL must never subscribe a node to a DIFFERENT network's file areas")

    def test_hub_side_plus_tag_rejects_file_area_from_other_network(self):
        from anetbbs.models import db, EchomailNetwork, FileArea, FileEchoSubscription
        from anetbbs.echomail.filefix import _process_node_request

        with self.app.app_context():
            own_net = EchomailNetwork(name='FilefixCrossTagOwn', network_type='binkp',
                                      our_address='9:3/1')
            other_net = EchomailNetwork(name='FilefixCrossTagOther', network_type='binkp',
                                        our_address='9:4/1')
            db.session.add_all([own_net, other_net])
            db.session.flush()
            other_area = FileArea(tag='FF.FOREIGNTAG', name='Foreign', network_id=other_net.id,
                                  is_active=True, is_subscribed=True)
            db.session.add(other_area)
            db.session.commit()

            response, log_kwargs = _process_node_request(
                '9:3/2', '9:3/2', 'testpw123', '+FF.FOREIGNTAG\n',
                'testpw123', own_net.id)

            self.assertNotIn('FF.FOREIGNTAG : subscribed', response)
            self.assertIsNone(FileEchoSubscription.query.filter_by(
                file_area_id=other_area.id, peer_address='9:3/2').first())

    def test_hub_side_unset_network_id_fails_closed_not_open(self):
        """network_id=None (legacy row / lookup failure) must see ZERO
        available file areas, never fall back to 'show everything'."""
        from anetbbs.models import db, EchomailNetwork, FileArea, FileEchoSubscription
        from anetbbs.echomail.filefix import _process_node_request

        with self.app.app_context():
            net = EchomailNetwork(name='FilefixUnsetNetIdNet', network_type='binkp',
                                  our_address='9:5/1')
            db.session.add(net)
            db.session.flush()
            area = FileArea(tag='FF.NONETID', name='No Net Id', network_id=net.id,
                            is_active=True, is_subscribed=True)
            db.session.add(area)
            db.session.commit()

            response, log_kwargs = _process_node_request(
                '9:5/2', '9:5/2', 'testpw123', '+ALL\n', 'testpw123', None)

            self.assertIn('subscribed to 0 file areas', response)
            self.assertIsNone(FileEchoSubscription.query.filter_by(
                file_area_id=area.id, peer_address='9:5/2').first())

    def test_areafix_log_bot_column_defaults_to_areafix(self):
        """Pre-existing rows (all message-echo requests, from before this
        column existed) must read as 'areafix' without a backfill."""
        from anetbbs.models import db, AreafixLog

        with self.app.app_context():
            row = AreafixLog(from_address='1:1/9', request_type='help',
                             response='x', success=True)
            db.session.add(row)
            db.session.commit()
            self.assertEqual(row.bot, 'areafix')

    def test_help_text_lists_every_accepted_command_but_not_rescan_or_compress(self):
        """filefix.py reuses areafix.py's parse_request(), but has no
        toss_area_messages()-equivalent for files -- %RESCAN/%COMPRESS
        must NOT be advertised here since neither is implemented."""
        from anetbbs.echomail.filefix import _help_text
        text = _help_text()
        for token in ('+AREA.TAG', '*AREA.TAG', '-AREA.TAG', '+ALL', '-ALL',
                     '%LIST', '%QUERY', '%HELP'):
            self.assertIn(token, text, f'{token!r} missing from filefix %HELP text')
        for token in ('%RESCAN', '%COMPRESS'):
            self.assertNotIn(token, text,
                            f'{token!r} must not be advertised -- filefix has no '
                            'implementation for it')


if __name__ == '__main__':
    unittest.main()
