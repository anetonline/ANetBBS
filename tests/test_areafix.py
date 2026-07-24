"""Regression tests for the AreaFix bot (anetbbs/echomail/areafix.py).

This module had NO direct unit test coverage before this session's
full echomail-subsystem audit surfaced a real security gap: inbound
netmail addressed to "areafix" was applied unconditionally, with no
verification of the AreaFix password send_areafix_request() so
carefully places in the Subject line on the way out (FTS-0024). Any
netmail reaching this system addressed to the areafix robot -- spoofed
From:, or a legitimate but unrelated node -- could silently
unsubscribe/resubscribe every echo area, and on the hub side, a
message's From: address alone (not tied to the authenticated BinkP
session that delivered it) let one peer forge a request altering a
DIFFERENT downstream node's subscriptions.

Covers: process_request (leaf side, on EchoArea.is_subscribed),
_process_node_request (hub side, on EchoAreaNode), the new password
gate on both, and handle_areafix_netmail end-to-end for both paths.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class AreafixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.areafix_test.db')
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

    def test_leaf_side_subscribe_and_unsubscribe_no_password_configured(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea
        from anetbbs.echomail.areafix import process_request

        with self.app.app_context():
            net = EchomailNetwork(name='AreafixLeafNet', network_type='binkp',
                                  our_address='1:1/1')
            db.session.add(net)
            db.session.flush()
            a1 = EchoArea(tag='AF.SUBBED', name='Subbed', network_id=net.id,
                         is_active=True, is_subscribed=True)
            a2 = EchoArea(tag='AF.NOTSUBBED', name='Not subbed', network_id=net.id,
                         is_active=True, is_subscribed=False)
            db.session.add_all([a1, a2])
            db.session.commit()

            response, log_kwargs = process_request(
                net, '1:1/2', '', '-AF.SUBBED\n+AF.NOTSUBBED\n')

            self.assertIn('-AF.SUBBED : unsubscribed', response)
            self.assertIn('+AF.NOTSUBBED : subscribed', response)
            self.assertTrue(log_kwargs['success'])

            refreshed_a1 = EchoArea.query.filter_by(tag='AF.SUBBED').first()
            refreshed_a2 = EchoArea.query.filter_by(tag='AF.NOTSUBBED').first()
            self.assertFalse(refreshed_a1.is_subscribed)
            self.assertTrue(refreshed_a2.is_subscribed)

    def test_leaf_side_wrong_password_rejects_without_applying_changes(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea
        from anetbbs.echomail.areafix import process_request

        with self.app.app_context():
            net = EchomailNetwork(name='AreafixPwNet', network_type='binkp',
                                  our_address='1:1/1',
                                  areafix_password='correcthorse')
            db.session.add(net)
            db.session.flush()
            area = EchoArea(tag='AF.PWTEST', name='Pw Test', network_id=net.id,
                            is_active=True, is_subscribed=False)
            db.session.add(area)
            db.session.commit()

            response, log_kwargs = process_request(
                net, '1:1/2', 'wrongpassword', '+AF.PWTEST\n')

            self.assertFalse(log_kwargs['success'])
            self.assertEqual(log_kwargs['request_type'], 'badpw')
            self.assertIn('password', response.lower())

            refreshed = EchoArea.query.filter_by(tag='AF.PWTEST').first()
            self.assertFalse(refreshed.is_subscribed,
                             'a bad password must not apply any changes')

    def test_leaf_side_correct_password_applies_changes(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea
        from anetbbs.echomail.areafix import process_request

        with self.app.app_context():
            net = EchomailNetwork(name='AreafixPwOkNet', network_type='binkp',
                                  our_address='1:1/1',
                                  areafix_password='correcthorse')
            db.session.add(net)
            db.session.flush()
            area = EchoArea(tag='AF.PWOK', name='Pw Ok', network_id=net.id,
                            is_active=True, is_subscribed=False)
            db.session.add(area)
            db.session.commit()

            response, log_kwargs = process_request(
                net, '1:1/2', 'correcthorse', '+AF.PWOK\n')

            self.assertTrue(log_kwargs['success'])
            refreshed = EchoArea.query.filter_by(tag='AF.PWOK').first()
            self.assertTrue(refreshed.is_subscribed)

    def test_leaf_side_falls_back_to_binkp_password_when_no_areafix_password(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea
        from anetbbs.echomail.areafix import process_request

        with self.app.app_context():
            net = EchomailNetwork(name='AreafixBinkpPwFallback', network_type='binkp',
                                  our_address='1:1/1',
                                  binkp_password='sessionsecret')
            db.session.add(net)
            db.session.flush()
            area = EchoArea(tag='AF.FALLBACK', name='Fallback', network_id=net.id,
                            is_active=True, is_subscribed=False)
            db.session.add(area)
            db.session.commit()

            response, log_kwargs = process_request(
                net, '1:1/2', 'sessionsecret', '+AF.FALLBACK\n')
            self.assertTrue(log_kwargs['success'])

    def test_hub_side_subscribe_creates_echoareanode(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea, EchoAreaNode, BinkPNode
        from anetbbs.echomail.areafix import _process_node_request

        with self.app.app_context():
            net = EchomailNetwork(name='AreafixHubTestNet', network_type='binkp',
                                  our_address='2:2/1')
            db.session.add(net)
            db.session.flush()
            node = BinkPNode(name='Downstream', ftn_address='2:2/2',
                             password='testpw123', is_active=True,
                             network_id=net.id)
            db.session.add(node)
            db.session.flush()
            area = EchoArea(tag='AF.HUBTEST', name='Hub Test', network_id=net.id,
                            is_active=True, is_subscribed=True)
            db.session.add(area)
            db.session.commit()

            response, log_kwargs = _process_node_request(
                node, '2:2/2', 'testpw123', '+AF.HUBTEST\n')

            self.assertIn('+AF.HUBTEST : subscribed', response)
            sub = EchoAreaNode.query.filter_by(
                node_id=node.id, echo_area_id=area.id).first()
            self.assertIsNotNone(sub)

    def test_hub_side_wrong_node_password_rejects_without_applying_changes(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea, EchoAreaNode, BinkPNode
        from anetbbs.echomail.areafix import _process_node_request

        with self.app.app_context():
            net = EchomailNetwork(name='AreafixHubPwNet', network_type='binkp',
                                  our_address='5:5/1')
            db.session.add(net)
            node = BinkPNode(name='Downstream', ftn_address='5:5/5',
                             password='realpassword', is_active=True)
            db.session.add(node)
            db.session.flush()
            area = EchoArea(tag='AF.HUBPW', name='Hub Pw', network_id=net.id,
                            is_active=True, is_subscribed=True)
            db.session.add(area)
            db.session.commit()

            response, log_kwargs = _process_node_request(
                node, '5:5/5', 'wrongpassword', '+AF.HUBPW\n')

            self.assertFalse(log_kwargs['success'])
            self.assertEqual(log_kwargs['request_type'], 'badpw')
            self.assertIsNone(EchoAreaNode.query.filter_by(
                node_id=node.id, echo_area_id=area.id).first())

    def test_handle_areafix_netmail_leaf_path_end_to_end(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea, NetmailMessage
        from anetbbs.echomail.areafix import handle_areafix_netmail

        with self.app.app_context():
            net = EchomailNetwork(name='AreafixE2ELeaf', network_type='binkp',
                                  our_address='1:1/1', hub_address='1:1/2')
            db.session.add(net)
            db.session.flush()
            area = EchoArea(tag='AF.E2ELEAF', name='E2E Leaf', network_id=net.id,
                            is_active=True, is_subscribed=False)
            db.session.add(area)
            db.session.flush()

            inbound = NetmailMessage(
                network_id=net.id, from_address='1:1/2', to_address='1:1/1',
                from_name='Hub', to_name='areafix', subject='areafix',
                body='+AF.E2ELEAF\n', direction='inbound', status='received')
            db.session.add(inbound)
            db.session.commit()
            nm_id = inbound.id

            response = handle_areafix_netmail(nm_id)
            self.assertIsNotNone(response)
            self.assertIn('+AF.E2ELEAF : subscribed', response)

            refreshed_area = EchoArea.query.filter_by(tag='AF.E2ELEAF').first()
            self.assertTrue(refreshed_area.is_subscribed)

            reply = (NetmailMessage.query
                    .filter_by(direction='outbound', from_name='Areafix')
                    .order_by(NetmailMessage.id.desc()).first())
            self.assertIsNotNone(reply, 'expected a queued Areafix reply netmail')
            self.assertEqual(reply.to_address, '1:1/2')
            self.assertEqual(reply.status, 'queued')

    def test_handle_areafix_netmail_hub_path_wrong_password_makes_no_changes(self):
        """End-to-end: a forged From: address matching a real downstream
        node, but without that node's real password, must not be able to
        alter that node's subscriptions."""
        from anetbbs.models import (db, EchomailNetwork, EchoArea, NetmailMessage,
                                    BinkPNode, EchoAreaNode)
        from anetbbs.echomail.areafix import handle_areafix_netmail

        with self.app.app_context():
            net = EchomailNetwork(name='AreafixE2EHubBadPw', network_type='binkp',
                                  our_address='6:6/1')
            db.session.add(net)
            node = BinkPNode(name='Downstream', ftn_address='6:6/2',
                             password='realsecret', is_active=True)
            db.session.add(node)
            db.session.flush()
            area = EchoArea(tag='AF.E2EHUBPW', name='E2E Hub Pw', network_id=net.id,
                            is_active=True, is_subscribed=True)
            db.session.add(area)
            db.session.flush()

            inbound = NetmailMessage(
                network_id=net.id, from_address='6:6/2', to_address='6:6/1',
                from_name='Forger', to_name='areafix', subject='guessedwrong',
                body='+AF.E2EHUBPW\n', direction='inbound', status='received')
            db.session.add(inbound)
            db.session.commit()
            nm_id = inbound.id

            response = handle_areafix_netmail(nm_id)
            self.assertIn('password', response.lower())

            self.assertIsNone(EchoAreaNode.query.filter_by(
                node_id=node.id, echo_area_id=area.id).first(),
                'wrong password must not create a subscription')

    def test_parse_request_captures_rescan_argument(self):
        """Real report: a real downstream sysop sent repeated
        "%RESCAN AREA.TAG" requests trying to recover a backlog he'd
        never received -- the old parser only captured 'RESCAN' as the
        target and silently discarded the area-tag argument entirely."""
        from anetbbs.echomail.areafix import parse_request
        self.assertEqual(parse_request('%RESCAN AF.SOMETAG\n'),
                         [('%', 'RESCAN', 'AF.SOMETAG')])
        self.assertEqual(parse_request('%RESCAN\n'),
                         [('%', 'RESCAN', None)])
        # Plain +/- commands (no second token) must still parse with arg=None.
        self.assertEqual(parse_request('+AF.SOMETAG\n'),
                         [('+', 'AF.SOMETAG', None)])

    def test_hub_side_rescan_with_tag_requeues_that_areas_messages(self):
        from anetbbs.models import (db, EchomailNetwork, EchoArea, EchoAreaNode,
                                    BinkPNode, BinkPHoldQueue, EchomailMessage)
        from anetbbs.echomail.areafix import _process_node_request

        with self.app.app_context():
            net = EchomailNetwork(name='RescanNet', network_type='binkp',
                                  our_address='7:7/1')
            db.session.add(net)
            db.session.flush()
            node = BinkPNode(name='Rescanner', ftn_address='7:7/2',
                             password='testpw123', is_active=True,
                             network_id=net.id)
            db.session.add(node)
            db.session.flush()
            area = EchoArea(tag='AF.RESCAN', name='Rescan Test', network_id=net.id,
                            is_active=True, is_subscribed=True)
            db.session.add(area)
            db.session.flush()
            # Real backlog: 3 messages already exist in the area, none
            # ever queued for this node (matches the live-reported shape
            # -- local/imported messages that predate the node's own
            # subscription, or that were never tossed at all).
            for n in range(3):
                db.session.add(EchomailMessage(
                    area_id=area.id, network_id=net.id, from_name='Sysop',
                    to_name='All', subject=f'Backlog {n}', body='x',
                    direction='inbound'))
            db.session.add(EchoAreaNode(node_id=node.id, echo_area_id=area.id))
            db.session.commit()

            response, log_kwargs = _process_node_request(
                node, '7:7/2', 'testpw123', '%RESCAN AF.RESCAN\n')

            self.assertIn('queued 3 message(s)', response)
            self.assertEqual(
                BinkPHoldQueue.query.filter_by(node_id=node.id).count(), 3)

    def test_hub_side_bare_rescan_requeues_every_subscribed_area(self):
        from anetbbs.models import (db, EchomailNetwork, EchoArea, EchoAreaNode,
                                    BinkPNode, BinkPHoldQueue, EchomailMessage)
        from anetbbs.echomail.areafix import _process_node_request

        with self.app.app_context():
            net = EchomailNetwork(name='BareRescanNet', network_type='binkp',
                                  our_address='8:8/1')
            db.session.add(net)
            node = BinkPNode(name='BareRescanner', ftn_address='8:8/2',
                             password='testpw123', is_active=True)
            db.session.add(node)
            db.session.flush()
            area_a = EchoArea(tag='AF.BARE.A', name='A', network_id=net.id,
                              is_active=True, is_subscribed=True)
            area_b = EchoArea(tag='AF.BARE.B', name='B', network_id=net.id,
                              is_active=True, is_subscribed=True)
            db.session.add_all([area_a, area_b])
            db.session.flush()
            db.session.add(EchomailMessage(
                area_id=area_a.id, network_id=net.id, from_name='Sysop',
                to_name='All', subject='A msg', body='x', direction='inbound'))
            db.session.add(EchomailMessage(
                area_id=area_b.id, network_id=net.id, from_name='Sysop',
                to_name='All', subject='B msg', body='x', direction='inbound'))
            db.session.add(EchoAreaNode(node_id=node.id, echo_area_id=area_a.id))
            db.session.add(EchoAreaNode(node_id=node.id, echo_area_id=area_b.id))
            db.session.commit()

            response, log_kwargs = _process_node_request(
                node, '8:8/2', 'testpw123', '%RESCAN\n')

            self.assertIn('queued 2 message(s) across 2 subscribed area(s)', response)
            self.assertEqual(
                BinkPHoldQueue.query.filter_by(node_id=node.id).count(), 2)

    def test_hub_side_sysop_only_area_cannot_be_subscribed_via_tag(self):
        """Real gap found in a full echomail-subsystem audit: is_sysop_only
        areas (e.g. interbbs_sync.py's Wall/Last-Callers-sync/casino-
        score-sync machine-to-machine channels) were subscribable by any
        downstream node via a plain +TAG, same as a public area."""
        from anetbbs.models import db, EchomailNetwork, EchoArea, EchoAreaNode, BinkPNode
        from anetbbs.echomail.areafix import _process_node_request

        with self.app.app_context():
            net = EchomailNetwork(name='AreafixSysopOnlyNet', network_type='binkp',
                                  our_address='3:3/1')
            db.session.add(net)
            db.session.flush()
            node = BinkPNode(name='Downstream', ftn_address='3:3/2',
                             password='testpw123', is_active=True,
                             network_id=net.id)
            db.session.add(node)
            db.session.flush()
            area = EchoArea(tag='AF.SYSOPONLY', name='Sysop Only', network_id=net.id,
                            is_active=True, is_subscribed=True, is_sysop_only=True)
            db.session.add(area)
            db.session.commit()

            response, log_kwargs = _process_node_request(
                node, '3:3/2', 'testpw123', '+AF.SYSOPONLY\n')

            self.assertNotIn('subscribed', response)
            sub = EchoAreaNode.query.filter_by(
                node_id=node.id, echo_area_id=area.id).first()
            self.assertIsNone(sub,
                             'a sysop-only area must not become subscribable '
                             'via AreaFix from any downstream node')

    def test_hub_side_plus_all_excludes_sysop_only_areas(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea, EchoAreaNode, BinkPNode
        from anetbbs.echomail.areafix import _process_node_request

        with self.app.app_context():
            net = EchomailNetwork(name='AreafixPlusAllSysopNet', network_type='binkp',
                                  our_address='3:4/1')
            db.session.add(net)
            db.session.flush()
            node = BinkPNode(name='Downstream', ftn_address='3:4/2',
                             password='testpw123', is_active=True,
                             network_id=net.id)
            db.session.add(node)
            db.session.flush()
            public_area = EchoArea(tag='AF.PUBLIC', name='Public', network_id=net.id,
                                   is_active=True, is_subscribed=True,
                                   is_sysop_only=False)
            sysop_area = EchoArea(tag='AF.INTERNAL', name='Internal', network_id=net.id,
                                  is_active=True, is_subscribed=True,
                                  is_sysop_only=True)
            db.session.add_all([public_area, sysop_area])
            db.session.commit()

            _process_node_request(node, '3:4/2', 'testpw123', '+ALL\n')

            self.assertIsNotNone(EchoAreaNode.query.filter_by(
                node_id=node.id, echo_area_id=public_area.id).first())
            self.assertIsNone(EchoAreaNode.query.filter_by(
                node_id=node.id, echo_area_id=sysop_area.id).first(),
                '+ALL must never sweep in a sysop-only area')

    def test_hub_side_legacy_null_is_sysop_only_is_treated_as_not_sysop_only(self):
        """A migrated/legacy EchoArea row with is_sysop_only IS NULL
        (nullable column, no DB-level default) must not get excluded by
        SQL's three-valued NULL logic -- only an explicit True hides it,
        same NULL-safety convention as LASTCALLERS_HIDE_SYSOP."""
        from anetbbs.models import db, EchomailNetwork, EchoArea, EchoAreaNode, BinkPNode
        from anetbbs.echomail.areafix import _process_node_request

        with self.app.app_context():
            net = EchomailNetwork(name='AreafixLegacyNullNet', network_type='binkp',
                                  our_address='3:5/1')
            db.session.add(net)
            db.session.flush()
            node = BinkPNode(name='Downstream', ftn_address='3:5/2',
                             password='testpw123', is_active=True,
                             network_id=net.id)
            db.session.add(node)
            db.session.flush()
            area = EchoArea(tag='AF.LEGACY', name='Legacy', network_id=net.id,
                            is_active=True, is_subscribed=True)
            db.session.add(area)
            db.session.flush()
            db.session.execute(db.text(
                'UPDATE echo_areas SET is_sysop_only = NULL WHERE id = :id'),
                {'id': area.id})
            db.session.commit()

            response, log_kwargs = _process_node_request(
                node, '3:5/2', 'testpw123', '+AF.LEGACY\n')

            self.assertIn('subscribed', response)
            self.assertIsNotNone(EchoAreaNode.query.filter_by(
                node_id=node.id, echo_area_id=area.id).first())

    def test_hub_side_plus_all_never_subscribes_areas_from_other_networks(self):
        """Real bug reported live: a downstream node's +ALL subscribed it
        to EVERY echo area this hub relays across ALL networks, not just
        the one network the node itself is a member of -- a cross-network
        data leak. all_areas must be scoped to node.network_id."""
        from anetbbs.models import db, EchomailNetwork, EchoArea, EchoAreaNode, BinkPNode
        from anetbbs.echomail.areafix import _process_node_request

        with self.app.app_context():
            own_net = EchomailNetwork(name='AreafixCrossNetOwn', network_type='binkp',
                                      our_address='9:1/1')
            other_net = EchomailNetwork(name='AreafixCrossNetOther', network_type='binkp',
                                        our_address='9:2/1')
            db.session.add_all([own_net, other_net])
            db.session.flush()
            node = BinkPNode(name='CrossNetNode', ftn_address='9:1/2',
                             password='testpw123', is_active=True,
                             network_id=own_net.id)
            db.session.add(node)
            own_area = EchoArea(tag='AF.OWNNET', name='Own Net', network_id=own_net.id,
                                is_active=True, is_subscribed=True)
            other_area = EchoArea(tag='AF.OTHERNET', name='Other Net', network_id=other_net.id,
                                  is_active=True, is_subscribed=True)
            db.session.add_all([own_area, other_area])
            db.session.commit()

            response, log_kwargs = _process_node_request(
                node, '9:1/2', 'testpw123', '+ALL\n')

            self.assertIsNotNone(EchoAreaNode.query.filter_by(
                node_id=node.id, echo_area_id=own_area.id).first())
            self.assertIsNone(EchoAreaNode.query.filter_by(
                node_id=node.id, echo_area_id=other_area.id).first(),
                "+ALL must never subscribe a node to a DIFFERENT network's areas")

    def test_hub_side_plus_tag_rejects_area_from_other_network(self):
        """Same bug, single-tag form: a plain +TAG for an area belonging
        to a network the requesting node isn't a member of must be
        rejected, not silently allowed through."""
        from anetbbs.models import db, EchomailNetwork, EchoArea, EchoAreaNode, BinkPNode
        from anetbbs.echomail.areafix import _process_node_request

        with self.app.app_context():
            own_net = EchomailNetwork(name='AreafixCrossTagOwn', network_type='binkp',
                                      our_address='9:3/1')
            other_net = EchomailNetwork(name='AreafixCrossTagOther', network_type='binkp',
                                        our_address='9:4/1')
            db.session.add_all([own_net, other_net])
            db.session.flush()
            node = BinkPNode(name='CrossTagNode', ftn_address='9:3/2',
                             password='testpw123', is_active=True,
                             network_id=own_net.id)
            db.session.add(node)
            other_area = EchoArea(tag='AF.FOREIGNTAG', name='Foreign', network_id=other_net.id,
                                  is_active=True, is_subscribed=True)
            db.session.add(other_area)
            db.session.commit()

            response, log_kwargs = _process_node_request(
                node, '9:3/2', 'testpw123', '+AF.FOREIGNTAG\n')

            self.assertNotIn('AF.FOREIGNTAG : subscribed', response)
            self.assertIsNone(EchoAreaNode.query.filter_by(
                node_id=node.id, echo_area_id=other_area.id).first())

    def test_hub_side_unset_network_id_fails_closed_not_open(self):
        """A legacy BinkPNode row with no network_id set (nullable column,
        rows created before it existed) must see ZERO available areas,
        never fall back to 'show everything' -- that fallback is exactly
        the cross-network leak this fix closes."""
        from anetbbs.models import db, EchomailNetwork, EchoArea, EchoAreaNode, BinkPNode
        from anetbbs.echomail.areafix import _process_node_request

        with self.app.app_context():
            net = EchomailNetwork(name='AreafixUnsetNetIdNet', network_type='binkp',
                                  our_address='9:5/1')
            db.session.add(net)
            db.session.flush()
            node = BinkPNode(name='NoNetIdNode', ftn_address='9:5/2',
                             password='testpw123', is_active=True)
            db.session.add(node)
            area = EchoArea(tag='AF.NONETID', name='No Net Id', network_id=net.id,
                            is_active=True, is_subscribed=True)
            db.session.add(area)
            db.session.commit()
            self.assertIsNone(node.network_id)

            response, log_kwargs = _process_node_request(
                node, '9:5/2', 'testpw123', '+ALL\n')

            self.assertIn('subscribed to 0 areas', response)
            self.assertIsNone(EchoAreaNode.query.filter_by(
                node_id=node.id, echo_area_id=area.id).first())

    def test_password_command_changes_node_password_when_authenticated(self):
        """Real gap found in a full echomail-subsystem audit: no remote
        way for a downstream sysop to rotate their own AreaFix/BinkP
        password. New password value must survive with its exact case
        -- NOT go through parse_request()'s uppercasing arg capture."""
        from anetbbs.models import db, EchomailNetwork, EchoArea, BinkPNode
        from anetbbs.echomail.areafix import _process_node_request

        with self.app.app_context():
            net = EchomailNetwork(name='AreafixPwChangeNet', network_type='binkp',
                                  our_address='7:1/1')
            db.session.add(net)
            node = BinkPNode(name='PwChangeNode', ftn_address='7:1/2',
                             password='OldSecret1', is_active=True)
            db.session.add(node)
            db.session.commit()
            node_id = node.id

            response, log_kwargs = _process_node_request(
                node, '7:1/2', 'OldSecret1', '%PASSWORD NewMixedCase99\n')

            self.assertIn('password changed', response)
            refreshed = BinkPNode.query.get(node_id)
            self.assertEqual(refreshed.password, 'NewMixedCase99',
                             'new password must preserve its exact case, not '
                             'get uppercased like a normal command argument')

    def test_password_command_rejected_when_node_has_no_password_set(self):
        """SECURITY: a node with no password configured must not be able
        to bootstrap one via %PASSWORD. Originally this exercised a
        %PASSWORD-specific guard because the surrounding auth check only
        rejected a WRONG password, not a MISSING one -- that surrounding
        check was later hardened (full access-control audit) to reject
        a missing password unconditionally too, so this now gets caught
        by the earlier, general gate. Kept as a regression test for the
        core guarantee either way: no password bootstrap without real
        authentication."""
        from anetbbs.models import db, EchomailNetwork, EchoArea, BinkPNode
        from anetbbs.echomail.areafix import _process_node_request

        with self.app.app_context():
            net = EchomailNetwork(name='AreafixPwBootstrapNet', network_type='binkp',
                                  our_address='7:2/1')
            db.session.add(net)
            node = BinkPNode(name='NoPwNode', ftn_address='7:2/2',
                             password='', is_active=True)
            db.session.add(node)
            db.session.commit()
            node_id = node.id

            response, log_kwargs = _process_node_request(
                node, '7:2/2', '', '%PASSWORD Whatever123\n')

            self.assertIn('password incorrect or missing', response)
            self.assertFalse(log_kwargs['success'])
            refreshed = BinkPNode.query.get(node_id)
            self.assertEqual(refreshed.password, '',
                             'must not silently set a password on an '
                             'unprotected node from an unauthenticated request')

    def test_password_command_with_no_argument_is_an_error(self):
        from anetbbs.models import db, EchomailNetwork, BinkPNode
        from anetbbs.echomail.areafix import _process_node_request

        with self.app.app_context():
            net = EchomailNetwork(name='AreafixPwNoArgNet', network_type='binkp',
                                  our_address='7:3/1')
            db.session.add(net)
            node = BinkPNode(name='PwNoArgNode', ftn_address='7:3/2',
                             password='RealPassword', is_active=True)
            db.session.add(node)
            db.session.commit()
            node_id = node.id

            response, log_kwargs = _process_node_request(
                node, '7:3/2', 'RealPassword', '%PASSWORD\n')

            self.assertIn('ERROR', response)
            refreshed = BinkPNode.query.get(node_id)
            self.assertEqual(refreshed.password, 'RealPassword')

    def test_request_type_classifies_plain_query_correctly(self):
        """Real bug found in a full echomail-subsystem audit: a request
        containing ONLY %LIST/%HELP/%QUERY (no +/- at all) was logged
        as request_type='unsubscribe' even though nothing was
        unsubscribed -- false entries in the AreaFix audit trail."""
        from anetbbs.echomail.areafix import _classify_request_type, parse_request
        self.assertEqual(_classify_request_type(parse_request('%LIST\n')), 'query')
        self.assertEqual(_classify_request_type(parse_request('%HELP\n')), 'query')
        self.assertEqual(_classify_request_type(parse_request('%QUERY\n')), 'query')
        self.assertEqual(_classify_request_type(parse_request('%RESCAN\n')), 'query')

    def test_request_type_classifies_subscribe_and_unsubscribe(self):
        from anetbbs.echomail.areafix import _classify_request_type, parse_request
        self.assertEqual(_classify_request_type(parse_request('+AF.TAG\n')), 'subscribe')
        self.assertEqual(_classify_request_type(parse_request('-AF.TAG\n')), 'unsubscribe')
        # Mixed batch with at least one '+' counts as subscribe.
        self.assertEqual(
            _classify_request_type(parse_request('+AF.A\n-AF.B\n')), 'subscribe')

    def test_leaf_side_plain_list_request_logs_as_query_not_unsubscribe(self):
        from anetbbs.models import db, EchomailNetwork
        from anetbbs.echomail.areafix import process_request

        with self.app.app_context():
            net = EchomailNetwork(name='AreafixQueryLogNet', network_type='binkp',
                                  our_address='6:6/1', hub_address='6:6/2')
            db.session.add(net)
            db.session.commit()

            response, log_kwargs = process_request(net, '6:6/2', '', '%LIST\n')
            self.assertEqual(log_kwargs['request_type'], 'query')

    def test_help_text_lists_every_accepted_command(self):
        from anetbbs.echomail.areafix import _help_text
        text = _help_text()
        for token in ('+AREA.TAG', '*AREA.TAG', '-AREA.TAG', '+ALL', '-ALL',
                     '%LIST', '%QUERY', '%RESCAN', '%COMPRESS', '%PASSWORD', '%HELP'):
            self.assertIn(token, text, f'{token!r} missing from areafix %HELP text')


if __name__ == '__main__':
    unittest.main()
