"""Regression tests for the echomail tosser (anetbbs/echomail/tosser.py).

This module had NO direct unit test coverage before this session's full
echomail-subsystem audit surfaced a real bug: the SEEN-BY loop-
prevention check in toss_message() never actually fired, for two
compounding reasons --

1. Raw SEEN-BY lines as stored by binkp.py's inbound parser are NOT
   tokenized -- a single JSON list entry can be a whole
   "234/567 234/568 235/1"-style line with several space-separated
   addresses on it (standard FTN convention). Comparing a single bare
   address against the whole raw line as one opaque string can never
   match unless there's exactly one address on that line already.
2. Even ignoring (1), SEEN-BY entries are always bare `net/node` (no
   zone) -- confirmed against binkp.py's own outbound packer -- while
   the candidate node's ftn_address is a full `zone:net/node` string.
   Comparing "1:234/567" against "234/567" can never match.

Net effect: a hub that imports echomail from downstream node A, where A
is ALSO subscribed to receive that same area back (a completely normal
bidirectional echo relationship), would unconditionally queue the
message right back to A -- a bounce/duplicate-delivery bug and a
contributor to mail loops, which is exactly the kind of bug this
session's broader BinkP audit was chasing.
"""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class TosserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.tosser_test.db')
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

    def _make_area_and_network(self, name):
        from anetbbs.models import db, EchomailNetwork, EchoArea
        net = EchomailNetwork(name=name, network_type='binkp',
                              our_address='1:1/1')
        db.session.add(net)
        db.session.flush()
        area = EchoArea(tag=f'{name}.ECHO', name=name, network_id=net.id,
                        is_active=True, is_subscribed=True)
        db.session.add(area)
        db.session.commit()
        return net, area

    def test_basic_toss_queues_subscribed_active_node(self):
        from anetbbs.models import (db, EchomailMessage, BinkPNode,
                                    EchoAreaNode, BinkPHoldQueue)
        from anetbbs.echomail.tosser import toss_message

        with self.app.app_context():
            net, area = self._make_area_and_network('TossBasic')
            node = BinkPNode(name='Sub', ftn_address='1:200/1',
                             password='x', is_active=True)
            db.session.add(node)
            db.session.flush()
            db.session.add(EchoAreaNode(node_id=node.id, echo_area_id=area.id))
            msg = EchomailMessage(
                area_id=area.id, network_id=net.id, from_name='A', to_name='All',
                subject='hi', body='hello', direction='inbound')
            db.session.add(msg)
            db.session.commit()

            created = toss_message(msg.id)
            self.assertEqual(created, 1)
            self.assertEqual(BinkPHoldQueue.query.filter_by(
                node_id=node.id, message_id=msg.id).count(), 1)

    def test_toss_is_idempotent(self):
        from anetbbs.models import (db, EchomailMessage, BinkPNode,
                                    EchoAreaNode, BinkPHoldQueue)
        from anetbbs.echomail.tosser import toss_message

        with self.app.app_context():
            net, area = self._make_area_and_network('TossIdempotent')
            node = BinkPNode(name='Sub', ftn_address='1:200/2',
                             password='x', is_active=True)
            db.session.add(node)
            db.session.flush()
            db.session.add(EchoAreaNode(node_id=node.id, echo_area_id=area.id))
            msg = EchomailMessage(
                area_id=area.id, network_id=net.id, from_name='A', to_name='All',
                subject='hi', body='hello', direction='inbound')
            db.session.add(msg)
            db.session.commit()

            first = toss_message(msg.id)
            second = toss_message(msg.id)
            self.assertEqual(first, 1)
            self.assertEqual(second, 0, 'calling toss_message twice must not duplicate')
            self.assertEqual(BinkPHoldQueue.query.filter_by(message_id=msg.id).count(), 1)

    def test_zone_qualified_node_in_bare_seenby_is_skipped(self):
        """The core fix: node.ftn_address is zone-qualified ("1:200/3")
        but SEEN-BY entries are always bare net/node ("200/3") -- must
        still match after stripping the zone."""
        from anetbbs.models import (db, EchomailMessage, BinkPNode,
                                    EchoAreaNode, BinkPHoldQueue)
        from anetbbs.echomail.tosser import toss_message

        with self.app.app_context():
            net, area = self._make_area_and_network('TossZoneSeenby')
            node = BinkPNode(name='AlreadyHasIt', ftn_address='1:200/3',
                             password='x', is_active=True)
            db.session.add(node)
            db.session.flush()
            db.session.add(EchoAreaNode(node_id=node.id, echo_area_id=area.id))
            msg = EchomailMessage(
                area_id=area.id, network_id=net.id, from_name='A', to_name='All',
                subject='hi', body='hello', direction='inbound',
                seenby=json.dumps(['200/3']))
            db.session.add(msg)
            db.session.commit()

            created = toss_message(msg.id)
            self.assertEqual(created, 0,
                             'node already in SEEN-BY must not be re-tossed')
            self.assertEqual(BinkPHoldQueue.query.filter_by(
                node_id=node.id, message_id=msg.id).count(), 0)

    def test_multi_address_seenby_line_is_tokenized(self):
        """A single stored SEEN-BY entry can be a whole line with
        several space-separated addresses (standard FTN convention) --
        each one must be checked individually, not the whole line as
        one opaque string."""
        from anetbbs.models import (db, EchomailMessage, BinkPNode,
                                    EchoAreaNode, BinkPHoldQueue)
        from anetbbs.echomail.tosser import toss_message

        with self.app.app_context():
            net, area = self._make_area_and_network('TossMultiSeenby')
            node = BinkPNode(name='SecondInLine', ftn_address='1:200/5',
                             password='x', is_active=True)
            db.session.add(node)
            db.session.flush()
            db.session.add(EchoAreaNode(node_id=node.id, echo_area_id=area.id))
            msg = EchomailMessage(
                area_id=area.id, network_id=net.id, from_name='A', to_name='All',
                subject='hi', body='hello', direction='inbound',
                # One raw SEEN-BY line containing multiple addresses,
                # exactly as binkp.py's inbound parser stores it.
                seenby=json.dumps(['200/4 200/5 200/6']))
            db.session.add(msg)
            db.session.commit()

            created = toss_message(msg.id)
            self.assertEqual(created, 0,
                             'an address embedded in a multi-address SEEN-BY '
                             'line must still be recognized')

    def test_node_not_in_seenby_is_still_tossed(self):
        """Sanity check the fix isn't overly broad: a genuinely new
        subscriber not mentioned anywhere in SEEN-BY must still get the
        message."""
        from anetbbs.models import (db, EchomailMessage, BinkPNode,
                                    EchoAreaNode, BinkPHoldQueue)
        from anetbbs.echomail.tosser import toss_message

        with self.app.app_context():
            net, area = self._make_area_and_network('TossNewSubscriber')
            node = BinkPNode(name='NewGuy', ftn_address='1:200/9',
                             password='x', is_active=True)
            db.session.add(node)
            db.session.flush()
            db.session.add(EchoAreaNode(node_id=node.id, echo_area_id=area.id))
            msg = EchomailMessage(
                area_id=area.id, network_id=net.id, from_name='A', to_name='All',
                subject='hi', body='hello', direction='inbound',
                seenby=json.dumps(['200/1 200/2']))
            db.session.add(msg)
            db.session.commit()

            created = toss_message(msg.id)
            self.assertEqual(created, 1)

    def test_malformed_seenby_json_logs_and_does_not_crash(self):
        from anetbbs.models import db, EchomailMessage, BinkPNode, EchoAreaNode
        from anetbbs.echomail.tosser import toss_message

        with self.app.app_context():
            net, area = self._make_area_and_network('TossBadJson')
            node = BinkPNode(name='Sub', ftn_address='1:200/7',
                             password='x', is_active=True)
            db.session.add(node)
            db.session.flush()
            db.session.add(EchoAreaNode(node_id=node.id, echo_area_id=area.id))
            msg = EchomailMessage(
                area_id=area.id, network_id=net.id, from_name='A', to_name='All',
                subject='hi', body='hello', direction='inbound',
                seenby='not valid json{{{')
            db.session.add(msg)
            db.session.commit()

            # Must not raise, and must fail open (toss anyway) rather
            # than silently dropping the message.
            created = toss_message(msg.id)
            self.assertEqual(created, 1)

    def test_exclude_peer_address_skips_the_node_even_with_empty_seenby(self):
        """Real gap found in a full echomail-subsystem audit: loop-
        prevention relied solely on SEEN-BY with no independent
        fallback -- if a downstream node's own tosser has a bug and
        never adds itself to SEEN-BY, the hub would toss the message
        straight back to the node it just received it from. exclude_
        peer_address is a caller-supplied "this is the peer we JUST
        imported this from" signal, independent of SEEN-BY entirely."""
        from anetbbs.models import (db, EchomailMessage, BinkPNode,
                                    EchoAreaNode, BinkPHoldQueue)
        from anetbbs.echomail.tosser import toss_message

        with self.app.app_context():
            net, area = self._make_area_and_network('TossExcludePeer')
            node = BinkPNode(name='JustSentThis', ftn_address='1:200/8',
                             password='x', is_active=True)
            db.session.add(node)
            db.session.flush()
            db.session.add(EchoAreaNode(node_id=node.id, echo_area_id=area.id))
            msg = EchomailMessage(
                area_id=area.id, network_id=net.id, from_name='A', to_name='All',
                subject='hi', body='hello', direction='inbound',
                seenby=json.dumps([]))  # empty -- peer's tosser bug scenario
            db.session.add(msg)
            db.session.commit()

            created = toss_message(msg.id, exclude_peer_address='1:200/8')
            self.assertEqual(created, 0,
                             'must not toss back to the peer session this '
                             'message was just received from')
            self.assertEqual(BinkPHoldQueue.query.filter_by(
                node_id=node.id, message_id=msg.id).count(), 0)

    def test_exclude_peer_address_does_not_affect_other_nodes(self):
        """Sanity check the fallback isn't overly broad: a node with a
        DIFFERENT address than the excluded peer must still be tossed."""
        from anetbbs.models import (db, EchomailMessage, BinkPNode,
                                    EchoAreaNode, BinkPHoldQueue)
        from anetbbs.echomail.tosser import toss_message

        with self.app.app_context():
            net, area = self._make_area_and_network('TossExcludeOther')
            node = BinkPNode(name='GenuineSubscriber', ftn_address='1:200/12',
                             password='x', is_active=True)
            db.session.add(node)
            db.session.flush()
            db.session.add(EchoAreaNode(node_id=node.id, echo_area_id=area.id))
            msg = EchomailMessage(
                area_id=area.id, network_id=net.id, from_name='A', to_name='All',
                subject='hi', body='hello', direction='inbound')
            db.session.add(msg)
            db.session.commit()

            created = toss_message(msg.id, exclude_peer_address='1:200/999')
            self.assertEqual(created, 1)
            self.assertEqual(BinkPHoldQueue.query.filter_by(
                node_id=node.id, message_id=msg.id).count(), 1)

    def test_exclude_peer_address_zone_qualified_matches_bare(self):
        """Same zone-stripping normalization as SEEN-BY comparison --
        the caller passes a full peer address (however it happened to
        be captured), a candidate node's ftn_address is also
        zone-qualified, both must reduce to the same bare form."""
        from anetbbs.models import (db, EchomailMessage, BinkPNode,
                                    EchoAreaNode, BinkPHoldQueue)
        from anetbbs.echomail.tosser import toss_message

        with self.app.app_context():
            net, area = self._make_area_and_network('TossExcludeZone')
            node = BinkPNode(name='ZoneQualifiedPeer', ftn_address='1:200/10',
                             password='x', is_active=True)
            db.session.add(node)
            db.session.flush()
            db.session.add(EchoAreaNode(node_id=node.id, echo_area_id=area.id))
            msg = EchomailMessage(
                area_id=area.id, network_id=net.id, from_name='A', to_name='All',
                subject='hi', body='hello', direction='inbound')
            db.session.add(msg)
            db.session.commit()

            created = toss_message(msg.id, exclude_peer_address='1:200/10')
            self.assertEqual(created, 0)

    def test_exclude_peer_address_none_is_a_no_op(self):
        """Default/omitted must behave exactly like before this fix."""
        from anetbbs.models import (db, EchomailMessage, BinkPNode,
                                    EchoAreaNode, BinkPHoldQueue)
        from anetbbs.echomail.tosser import toss_message

        with self.app.app_context():
            net, area = self._make_area_and_network('TossExcludeNone')
            node = BinkPNode(name='Sub', ftn_address='1:200/11',
                             password='x', is_active=True)
            db.session.add(node)
            db.session.flush()
            db.session.add(EchoAreaNode(node_id=node.id, echo_area_id=area.id))
            msg = EchomailMessage(
                area_id=area.id, network_id=net.id, from_name='A', to_name='All',
                subject='hi', body='hello', direction='inbound')
            db.session.add(msg)
            db.session.commit()

            created = toss_message(msg.id)
            self.assertEqual(created, 1)


if __name__ == '__main__':
    unittest.main()
