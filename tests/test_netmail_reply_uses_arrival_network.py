"""Regression test for a real live bug: replying to netmail that arrived
via ordinary cross-zone FTN store-and-forward routing (a hub relaying
mail from a zone its own our_address doesn't share) hit "No active FTN
network covers zone of ...", even though the network that had JUST
delivered the message obviously could carry traffic for that zone.

Real report: a sysop's real Fidonet network (our_address 1:123/3003,
zone 1) relayed a netmail originally sent from a zone-2 address (a
completely normal cross-zone hub relay -- not a direct/crash delivery,
network_id was a real, non-NULL value). Replying failed because
find_network_for_address() only matches a network whose OWN our_address
zone equals the destination zone -- correct for picking a network to
compose a brand-new message through with no prior routing evidence, but
wrong for a reply: the network that delivered the original message
already proved it can carry mail for that zone.

Fixed in anetbbs/web/netmail.py's compose(): a reply (parent set) with
a real parent.network_id now routes through THAT network directly,
skipping the zone-matching gate entirely. Only falls back to
find_network_for_address() for a fresh compose (no parent) or when
parent.network_id doesn't resolve to a still-active network.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _fresh_app(db_path):
    import anetbbs.config as cfg_mod
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    os.environ['FLASK_ENV'] = 'testing'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    return app


class ReplyUsesArrivalNetworkTests(unittest.TestCase):
    def setUp(self):
        import anetbbs.config as cfg_mod
        import tempfile
        self._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        self.addCleanup(
            lambda: setattr(cfg_mod.TestingConfig,
                            'SQLALCHEMY_DATABASE_URI', self._orig_db_uri))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'reply_arrival_net.db'))
        with self.app.app_context():
            from anetbbs.models import db, User, NetmailMessage, EchomailNetwork
            db.create_all()
            user = User(username='stingray', email='sr@example.test',
                       password_hash='x', access_level=100, is_admin=True)
            db.session.add(user)
            db.session.commit()
            self.user_id = user.id

            # our_address is zone 1 -- the network's OWN zone does not
            # match the zone-2 sender it's about to relay a reply to.
            net = EchomailNetwork(name='Fidonet', network_type='binkp',
                                  our_address='1:123/3003',
                                  hub_address='1:3634/12', is_active=True)
            db.session.add(net)
            db.session.commit()
            self.net_id = net.id

            # Arrived with a REAL network_id (relayed through the hub
            # above via ordinary cross-zone routing) -- NOT the
            # anonymous-crashmail case (that has network_id=None).
            parent = NetmailMessage(
                network_id=net.id, direction='inbound', status='received',
                from_address='2:280/464', to_address='1:123/3003',
                from_name='Remote Sysop', to_name='StingRay',
                subject='Fwd: Re: Weekly nodelist report',
                body='cross-zone relayed netmail', to_user_id=user.id,
            )
            db.session.add(parent)
            db.session.commit()
            self.parent_id = parent.id

    def _client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.user_id)
            sess['_fresh'] = True
        return client

    def test_reply_to_cross_zone_relayed_netmail_succeeds(self):
        client = self._client()
        resp = client.post(f'/netmail/{self.parent_id}/reply', data={
            'to_address': '2:280/464', 'to_name': 'Remote Sysop',
            'subject': 'Re: Fwd: Re: Weekly nodelist report',
            'body': 'Fixed now, thanks for the report!',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'No active FTN network covers zone', resp.data)

        from anetbbs.models import NetmailMessage
        with self.app.app_context():
            reply = (NetmailMessage.query
                    .filter_by(direction='outbound')
                    .order_by(NetmailMessage.id.desc()).first())
            self.assertIsNotNone(reply)
            self.assertEqual(reply.network_id, self.net_id,
                            'must route via the SAME network the '
                            'original arrived on, not a fresh zone match')
            self.assertEqual(reply.from_address, '1:123/3003')
            self.assertEqual(reply.status, 'queued')

    def test_reply_when_arrival_network_is_now_inactive_shows_clear_error(self):
        from anetbbs.models import db, EchomailNetwork
        with self.app.app_context():
            net = EchomailNetwork.query.get(self.net_id)
            net.is_active = False
            db.session.commit()

        client = self._client()
        resp = client.post(f'/netmail/{self.parent_id}/reply', data={
            'to_address': '2:280/464', 'to_name': 'Remote Sysop',
            'subject': 'Re: hello', 'body': 'x',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'no longer active', resp.data)

        from anetbbs.models import NetmailMessage
        with self.app.app_context():
            reply = (NetmailMessage.query
                    .filter_by(direction='outbound').first())
            self.assertIsNone(reply, 'must not create a message when the '
                             'arrival network is no longer usable')

    def test_fresh_compose_with_no_parent_still_uses_zone_matching(self):
        """A brand-new message (no parent, no prior routing evidence)
        must still go through the normal zone-matching gate -- this fix
        only changes behavior for REPLIES."""
        # find_network_for_address() has a pre-existing "only one active
        # network" fallback that ignores zone matching entirely -- add a
        # second active network (a different zone than either candidate)
        # so that fallback doesn't mask what this test is actually
        # checking.
        from anetbbs.models import db, EchomailNetwork
        with self.app.app_context():
            db.session.add(EchomailNetwork(
                name='OtherNet', network_type='binkp',
                our_address='21:1/100', hub_address='21:1/0',
                is_active=True))
            db.session.commit()

        client = self._client()
        resp = client.post('/netmail/compose', data={
            'to_address': '2:280/464', 'to_name': 'Someone New',
            'subject': 'Fresh message', 'body': 'x',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'No active FTN network covers zone', resp.data)


if __name__ == '__main__':
    unittest.main()
