"""Regression test for a real gap found in a full echomail-subsystem
audit: AreaFix/FileFix only ever got dispatched from binkp_server.py's
INBOUND-listener session (a peer dialing INTO us). poller.py's
_import_netmail() -- the receive side of an OUTBOUND poll (a hub
dialing out to an upstream network, or the newer hub-initiated "Poll
Node" dialing out to a downstream node) -- never checked the recipient
name at all, so a netmail addressed to AreaFix/FileFix arriving as part
of the response to OUR OWN poll (a real scenario now that hub-initiated
node polling exists) just sat there as a plain unread netmail, never
processed.

Fixed by adding the identical to_name dispatch check
binkp_server.py's _import_pkt_payload already uses, right after the
netmail row is created in _import_netmail().
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class AreafixViaOutboundPollReceiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.areafix_outbound_poll_test.db')
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

    def test_areafix_netmail_received_via_outbound_poll_is_processed(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea, NetmailMessage
        from anetbbs.echomail.poller import _import_message

        with self.app.app_context():
            net = EchomailNetwork(name='AreafixOutboundPollNet', network_type='binkp',
                                  our_address='4:4/1', hub_address='4:4/2',
                                  areafix_password='outpollpw')
            db.session.add(net)
            db.session.flush()
            area = EchoArea(tag='AF.OUTPOLL', name='Out Poll Test', network_id=net.id,
                            is_active=True, is_subscribed=False)
            db.session.add(area)
            db.session.commit()

            # Shape matches what BinkPClient.poll()'s 'received' list
            # produces for a netmail (no area_tag) -- see
            # _import_message()'s own "no AREA: kludge" branch. `subject`
            # carries the AreaFix password (FTS-0024), not the robot name.
            msg_data = {
                'area_tag': None,
                'from_name': 'Hub',
                'from_address': '4:4/2',
                'to_name': 'areafix',
                'to_address': '4:4/1',
                'subject': 'outpollpw',
                'body': '+AF.OUTPOLL\n',
            }

            rc = _import_message(net, msg_data)
            self.assertEqual(rc, 1)

            refreshed_area = EchoArea.query.filter_by(tag='AF.OUTPOLL').first()
            self.assertTrue(refreshed_area.is_subscribed,
                            'AreaFix must actually process a request received '
                            'via an outbound-poll session, not just log it as '
                            'an ordinary netmail')

            reply = (NetmailMessage.query
                    .filter_by(direction='outbound', from_name='Areafix')
                    .order_by(NetmailMessage.id.desc()).first())
            self.assertIsNotNone(reply, 'expected a queued Areafix reply netmail')

    def test_filefix_netmail_received_via_outbound_poll_is_processed(self):
        from anetbbs.models import db, EchomailNetwork, FileArea, NetmailMessage
        from anetbbs.echomail.poller import _import_message

        with self.app.app_context():
            net = EchomailNetwork(name='FilefixOutboundPollNet', network_type='binkp',
                                  our_address='4:5/1', hub_address='4:5/2',
                                  areafix_password='ffoutpollpw')
            db.session.add(net)
            db.session.flush()
            area = FileArea(tag='FF.OUTPOLL', name='FF Out Poll Test', network_id=net.id,
                            is_active=True, is_subscribed=False)
            db.session.add(area)
            db.session.commit()

            msg_data = {
                'area_tag': None,
                'from_name': 'Hub',
                'from_address': '4:5/2',
                'to_name': 'filefix',
                'to_address': '4:5/1',
                'subject': 'ffoutpollpw',
                'body': '+FF.OUTPOLL\n',
            }

            rc = _import_message(net, msg_data)
            self.assertEqual(rc, 1)

            refreshed_area = FileArea.query.filter_by(tag='FF.OUTPOLL').first()
            self.assertTrue(refreshed_area.is_subscribed,
                            'FileFix must actually process a request received '
                            'via an outbound-poll session')

            reply = (NetmailMessage.query
                    .filter_by(direction='outbound', from_name='FileFix')
                    .order_by(NetmailMessage.id.desc()).first())
            self.assertIsNotNone(reply, 'expected a queued FileFix reply netmail')

    def test_ordinary_netmail_via_outbound_poll_is_unaffected(self):
        """A netmail NOT addressed to areafix/filefix must still just
        import as a plain netmail -- no bot dispatch, no crash."""
        from anetbbs.models import db, EchomailNetwork, NetmailMessage
        from anetbbs.echomail.poller import _import_message

        with self.app.app_context():
            net = EchomailNetwork(name='OrdinaryOutboundPollNet', network_type='binkp',
                                  our_address='4:6/1', hub_address='4:6/2')
            db.session.add(net)
            db.session.commit()

            msg_data = {
                'area_tag': None,
                'from_name': 'Someone',
                'from_address': '4:6/2',
                'to_name': 'Sysop',
                'to_address': '4:6/1',
                'subject': 'Hello',
                'body': 'Just saying hi.\n',
            }

            rc = _import_message(net, msg_data)
            self.assertEqual(rc, 1)

            nm = NetmailMessage.query.filter_by(subject='Hello').first()
            self.assertIsNotNone(nm)
            self.assertEqual(
                NetmailMessage.query.filter_by(
                    direction='outbound', network_id=net.id).count(), 0,
                'an ordinary netmail must never trigger a bot reply')


if __name__ == '__main__':
    unittest.main()
