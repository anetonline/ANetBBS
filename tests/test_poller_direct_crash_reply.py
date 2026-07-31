"""Regression tests for send_netmail_direct_now() (anetbbs/echomail/poller.py).

Real report: a sysop received crash-delivered netmail from an FTN address
covered by no configured EchomailNetwork (see the nodelist crashmail-
compliance fix in binkp_server.py / test_binkp_anonymous_crashmail.py),
then tried to reply and hit "No active FTN network covers zone" -- there
was no hub to route a reply through, because there's no hub in this
relationship at all: the sender crash-delivered straight to us.

send_netmail_direct_now() is the outbound half of the fix: dial the
sender's own real IP (captured at receive time on NetmailMessage.origin_ip)
directly on the standard BinkP port, bypassing the normal per-network
hub_address queue entirely, since there IS no network/hub here.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

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
    return app


class SendNetmailDirectNowTests(unittest.TestCase):
    def setUp(self):
        import anetbbs.config as cfg_mod
        import tempfile
        self._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        self.addCleanup(
            lambda: setattr(cfg_mod.TestingConfig,
                            'SQLALCHEMY_DATABASE_URI', self._orig_db_uri))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'direct_crash.db'))
        with self.app.app_context():
            from anetbbs.models import db
            db.create_all()

    def _make_netmail(self, **overrides):
        from anetbbs.models import db, NetmailMessage
        with self.app.app_context():
            kwargs = dict(
                network_id=None, direction='outbound', status='queued',
                from_address='1:123/3003', to_address='2:280/464',
                from_name='StingRay', to_name='Remote Sysop',
                subject='Re: nodelist report', body='Fixed now, thanks!',
                origin_ip='203.0.113.7',
            )
            kwargs.update(overrides)
            nm = NetmailMessage(**kwargs)
            db.session.add(nm)
            db.session.commit()
            return nm.id

    def test_successful_delivery_marks_sent(self):
        from anetbbs.echomail import poller
        nm_id = self._make_netmail()
        fake_client = MagicMock()
        fake_client.poll.return_value = {'sent': 1, 'received': []}
        with patch('anetbbs.echomail.binkp.BinkPClient', return_value=fake_client) as ctor:
            poller.send_netmail_direct_now(self.app, nm_id)

        # Dialed the recipient's own recorded IP, standard BinkP port --
        # not any per-network hub_address (there is none here).
        _, kwargs = ctor.call_args
        self.assertEqual(kwargs['host'], '203.0.113.7')
        self.assertEqual(kwargs['port'], 24554)
        self.assertEqual(kwargs['password'], '',
                        'no shared secret exists with an unlisted peer')
        self.assertTrue(kwargs['default_crash'])

        from anetbbs.models import NetmailMessage
        with self.app.app_context():
            nm = NetmailMessage.query.get(nm_id)
            self.assertEqual(nm.status, 'sent')
            self.assertTrue(nm.is_sent)
            self.assertIsNotNone(nm.sent_at)

    def test_unacknowledged_delivery_leaves_queued_with_error_note(self):
        from anetbbs.echomail import poller
        nm_id = self._make_netmail()
        fake_client = MagicMock()
        fake_client.poll.return_value = {'sent': 0, 'received': []}
        with patch('anetbbs.echomail.binkp.BinkPClient', return_value=fake_client):
            poller.send_netmail_direct_now(self.app, nm_id)

        from anetbbs.models import NetmailMessage
        with self.app.app_context():
            nm = NetmailMessage.query.get(nm_id)
            self.assertEqual(nm.status, 'queued',
                            'not acknowledged -- must stay queued, not '
                            'silently marked sent')
            self.assertFalse(nm.is_sent)
            self.assertIsNotNone(nm.error_message)

    def test_connection_exception_does_not_propagate_and_records_error(self):
        from anetbbs.echomail import poller
        nm_id = self._make_netmail()
        with patch('anetbbs.echomail.binkp.BinkPClient',
                   side_effect=ConnectionRefusedError('refused')):
            poller.send_netmail_direct_now(self.app, nm_id)  # must not raise

        from anetbbs.models import NetmailMessage
        with self.app.app_context():
            nm = NetmailMessage.query.get(nm_id)
            self.assertEqual(nm.status, 'queued')
            self.assertIn('ConnectionRefusedError', nm.error_message)

    def test_ineligible_message_with_a_real_network_is_never_dialed(self):
        """A netmail with a real network_id already has a proper hub
        route (poller.py's normal _do_poll path) -- send_netmail_direct_now
        must refuse to touch it rather than dialing origin_ip anyway."""
        from anetbbs.echomail import poller
        nm_id = self._make_netmail(network_id=1)
        with patch('anetbbs.echomail.binkp.BinkPClient') as ctor:
            poller.send_netmail_direct_now(self.app, nm_id)
        ctor.assert_not_called()

    def test_already_sent_message_is_never_redialed(self):
        from anetbbs.echomail import poller
        nm_id = self._make_netmail(status='sent')
        with patch('anetbbs.echomail.binkp.BinkPClient') as ctor:
            poller.send_netmail_direct_now(self.app, nm_id)
        ctor.assert_not_called()

    def test_message_with_no_origin_ip_is_never_dialed(self):
        from anetbbs.echomail import poller
        nm_id = self._make_netmail(origin_ip=None)
        with patch('anetbbs.echomail.binkp.BinkPClient') as ctor:
            poller.send_netmail_direct_now(self.app, nm_id)
        ctor.assert_not_called()

    def test_unknown_netmail_id_is_a_silent_noop(self):
        from anetbbs.echomail import poller
        poller.send_netmail_direct_now(self.app, 999999)  # must not raise


if __name__ == '__main__':
    unittest.main()
