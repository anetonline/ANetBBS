"""Regression tests for the netmail compose route's "direct crash reply"
path (anetbbs/web/netmail.py's compose()).

Real report: replying to netmail that arrived via anonymous crash
delivery (network_id is None -- see the nodelist crashmail-compliance
fix in binkp_server.py) hit "No active FTN network covers zone", since
there's no configured EchomailNetwork/hub to route a reply through for
an address the sysop has no formal network membership with. Fixed by
detecting this case and routing the reply through send_netmail_direct_now()
(poller.py) instead -- dial the sender back directly, matching real FTN
crashmail semantics.

The critical security property tested here: the actual dial target
(origin_ip) is ALWAYS copied server-side from the parent message's own
DB column, never taken from posted form fields -- a tampered to_address
in the POST body must not change where the reply gets delivered.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _SyncThread:
    """Stand-in for threading.Thread that runs its target synchronously
    on .start() -- the compose route fires off the direct-dial send in a
    background thread (same established pattern as the admin "Poll Now"
    button), which these tests need to happen inline to assert on."""
    def __init__(self, target=None, daemon=None, **kw):
        self._target = target

    def start(self):
        if self._target:
            self._target()


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


class DirectCrashReplyComposeTests(unittest.TestCase):
    def setUp(self):
        import anetbbs.config as cfg_mod
        import tempfile
        self._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        self.addCleanup(
            lambda: setattr(cfg_mod.TestingConfig,
                            'SQLALCHEMY_DATABASE_URI', self._orig_db_uri))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'crash_reply_compose.db'))
        with self.app.app_context():
            from anetbbs.models import db, User, NetmailMessage
            db.create_all()
            user = User(username='stingray', email='sr@example.test',
                       password_hash='x', access_level=100, is_admin=True)
            db.session.add(user)
            db.session.commit()
            self.user_id = user.id

            parent = NetmailMessage(
                network_id=None, direction='inbound', status='received',
                from_address='2:280/464', to_address='1:123/3003',
                from_name='Remote Sysop', to_name='StingRay',
                subject='Fwd: Re: Weekly nodelist report',
                body='Not fixed yet: unknown address', to_user_id=user.id,
                origin_ip='203.0.113.7',
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

    def test_reply_bypasses_the_no_network_error_and_queues_a_direct_send(self):
        client = self._client()
        with patch('threading.Thread', _SyncThread), \
             patch('anetbbs.echomail.poller.send_netmail_direct_now') as fake_send:
            resp = client.post(f'/netmail/{self.parent_id}/reply', data={
                'to_address': '2:280/464', 'to_name': 'Remote Sysop',
                'subject': 'Re: Fwd: Re: Weekly nodelist report',
                'body': 'Fixed now, thanks for the report!',
            }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'No active FTN network covers zone', resp.data)
        fake_send.assert_called_once()

        from anetbbs.models import NetmailMessage
        with self.app.app_context():
            reply = (NetmailMessage.query
                    .filter_by(direction='outbound')
                    .order_by(NetmailMessage.id.desc()).first())
            # There's exactly one outbound row (the reply just created).
            self.assertIsNotNone(reply)
            self.assertIsNone(reply.network_id)
            self.assertEqual(reply.origin_ip, '203.0.113.7')
            self.assertTrue(reply.is_crash)
            self.assertEqual(reply.to_address, '2:280/464')
            self.assertEqual(reply.from_address, '1:123/3003',
                            'from_address must default to the AKA the '
                            'original was addressed to')

    def test_tampered_to_address_in_post_body_is_ignored(self):
        """The only thing that can ever change where a direct crash reply
        gets dialed is the parent message's own DB-stored origin_ip --
        never trust a posted to_address for this path, or a malicious/
        confused client could redirect delivery."""
        client = self._client()
        with patch('threading.Thread', _SyncThread), \
             patch('anetbbs.echomail.poller.send_netmail_direct_now'):
            client.post(f'/netmail/{self.parent_id}/reply', data={
                'to_address': '9:999/999', 'to_name': 'Someone Else',
                'subject': 'Re: hijack attempt', 'body': 'x',
            }, follow_redirects=True)

        from anetbbs.models import NetmailMessage
        with self.app.app_context():
            reply = (NetmailMessage.query
                    .filter_by(direction='outbound')
                    .order_by(NetmailMessage.id.desc()).first())
            self.assertEqual(reply.to_address, '2:280/464',
                            'to_address must be forced to the real '
                            'sender, ignoring the posted value')
            self.assertEqual(reply.to_name, 'Remote Sysop')

    def test_send_netmail_direct_now_is_called_with_the_new_message_id(self):
        client = self._client()
        with patch('threading.Thread', _SyncThread), \
             patch('anetbbs.echomail.poller.send_netmail_direct_now') as fake_send:
            client.post(f'/netmail/{self.parent_id}/reply', data={
                'to_address': '2:280/464', 'to_name': 'Remote Sysop',
                'subject': 'Re: nodelist', 'body': 'x',
            }, follow_redirects=True)

        from anetbbs.models import NetmailMessage
        with self.app.app_context():
            reply = (NetmailMessage.query
                    .filter_by(direction='outbound')
                    .order_by(NetmailMessage.id.desc()).first())
            reply_id = reply.id
        fake_send.assert_called_once()
        called_app, called_id = fake_send.call_args[0]
        self.assertEqual(called_id, reply_id)

    def test_ordinary_reply_to_a_networked_netmail_is_unaffected(self):
        """A normal reply (parent has a real network_id) must still go
        through the ordinary find_network_for_address() path, not the
        direct-dial one -- this feature must not change existing
        behavior for the common case."""
        from anetbbs.models import db, NetmailMessage, EchomailNetwork
        with self.app.app_context():
            net = EchomailNetwork(name='zer0net', network_type='binkp',
                                  our_address='1:123/3003',
                                  hub_address='1:123/0', is_active=True)
            db.session.add(net)
            db.session.commit()
            normal_parent = NetmailMessage(
                network_id=net.id, direction='inbound', status='received',
                from_address='1:123/99', to_address='1:123/3003',
                from_name='SomeNode', to_name='StingRay',
                subject='hello', body='hi', to_user_id=self.user_id,
            )
            db.session.add(normal_parent)
            db.session.commit()
            normal_parent_id = normal_parent.id
            net_id = net.id

        client = self._client()
        with patch('threading.Thread', _SyncThread), \
             patch('anetbbs.echomail.poller.send_netmail_direct_now') as fake_send:
            resp = client.post(f'/netmail/{normal_parent_id}/reply', data={
                'to_address': '1:123/99', 'to_name': 'SomeNode',
                'subject': 'Re: hello', 'body': 'hi back',
            }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        fake_send.assert_not_called()

        with self.app.app_context():
            reply = (NetmailMessage.query
                    .filter_by(direction='outbound')
                    .order_by(NetmailMessage.id.desc()).first())
            self.assertEqual(reply.network_id, net_id)
            self.assertIsNone(reply.origin_ip)


if __name__ == '__main__':
    unittest.main()
