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
            node = BinkPNode(name='Downstream', ftn_address='2:2/2',
                             password='', is_active=True)
            db.session.add(node)
            db.session.flush()
            area = EchoArea(tag='AF.HUBTEST', name='Hub Test', network_id=net.id,
                            is_active=True, is_subscribed=True)
            db.session.add(area)
            db.session.commit()

            response, log_kwargs = _process_node_request(
                node, '2:2/2', '', '+AF.HUBTEST\n')

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


if __name__ == '__main__':
    unittest.main()
