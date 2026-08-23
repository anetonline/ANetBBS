"""Tests making 'Crash' actually trigger an immediate out-of-schedule
delivery attempt, for both netmail and file-echo/TIC (HatchQueue).

Before this, NetmailMessage.is_crash (checked via the compose form's
existing Crash checkbox) only set the FTS-0001 ATTR_CRASH bit in the
outbound packet header -- cosmetic metadata for the RECEIVING tosser --
and the message just sat queued for the next scheduled poll like any
other, exactly like a normal message would. The ONLY existing immediate-
delivery path was the unrelated "direct crash reply" special case (no
EchomailNetwork at all, dialing a captured origin_ip). HatchQueue
(file-echo/TIC) had no flavor concept whatsoever.

poller.trigger_immediate_delivery() is the new shared entry point: it
prefers a direct dial to a known, dialable downstream BinkPNode matching
the destination address (real crash semantics -- straight to the
recipient) and falls back to polling the owning EchomailNetwork's hub
otherwise.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class _SyncThread:
    """Stand-in for threading.Thread that runs its target synchronously
    on .start() -- same established pattern as
    test_netmail_direct_crash_reply_compose.py's own _SyncThread."""
    def __init__(self, target=None, daemon=None, **kw):
        self._target = target

    def start(self):
        if self._target:
            self._target()


def _fresh_app(db_path):
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    os.environ['FLASK_ENV'] = 'testing'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    return app


class TriggerImmediateDeliveryResolutionTests(unittest.TestCase):
    """Pure resolution-logic tests -- no real BinkP I/O, poll_node_now/
    poll_network_now are patched so only the routing decision (node vs.
    network) is under test."""

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.crash_resolve_test.db')
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

    def test_prefers_direct_dial_to_a_dialable_matching_node(self):
        from anetbbs.echomail import poller as poller_mod
        from anetbbs.models import db, EchomailNetwork, BinkPNode

        with self.app.app_context():
            net = EchomailNetwork(name='CrashResolveNet1', network_type='binkp',
                                  our_address='1200:1/1', hub_address='1200:1/2',
                                  is_active=True)
            db.session.add(net)
            db.session.commit()
            node = BinkPNode(name='DirectNode', ftn_address='1200:1/50',
                             password='x', network_id=net.id,
                             binkp_host='directnode.example.test', is_active=True)
            db.session.add(node)
            db.session.commit()

            with patch.object(poller_mod, 'poll_node_now') as fake_node, \
                 patch.object(poller_mod, 'poll_network_now') as fake_net:
                poller_mod.trigger_immediate_delivery(
                    self.app, network_id=net.id, to_address='1200:1/50')

            fake_node.assert_called_once_with(self.app, node.id)
            fake_net.assert_not_called()

    def test_falls_back_to_network_when_node_has_no_dial_out_address(self):
        from anetbbs.echomail import poller as poller_mod
        from anetbbs.models import db, EchomailNetwork, BinkPNode

        with self.app.app_context():
            net = EchomailNetwork(name='CrashResolveNet2', network_type='binkp',
                                  our_address='1200:1/3', hub_address='1200:1/4',
                                  is_active=True)
            db.session.add(net)
            db.session.commit()
            node = BinkPNode(name='PollInOnlyNode', ftn_address='1200:1/51',
                             password='x', network_id=net.id,
                             binkp_host=None, is_active=True)
            db.session.add(node)
            db.session.commit()

            with patch.object(poller_mod, 'poll_node_now') as fake_node, \
                 patch.object(poller_mod, 'poll_network_now') as fake_net:
                poller_mod.trigger_immediate_delivery(
                    self.app, network_id=net.id, to_address='1200:1/51')

            fake_node.assert_not_called()
            fake_net.assert_called_once_with(self.app, net.id)

    def test_falls_back_to_network_when_to_address_matches_no_node(self):
        from anetbbs.echomail import poller as poller_mod
        from anetbbs.models import db, EchomailNetwork

        with self.app.app_context():
            net = EchomailNetwork(name='CrashResolveNet3', network_type='binkp',
                                  our_address='1200:1/5', hub_address='1200:1/6',
                                  is_active=True)
            db.session.add(net)
            db.session.commit()

            with patch.object(poller_mod, 'poll_node_now') as fake_node, \
                 patch.object(poller_mod, 'poll_network_now') as fake_net:
                poller_mod.trigger_immediate_delivery(
                    self.app, network_id=net.id, to_address='1200:1/999')

            fake_node.assert_not_called()
            fake_net.assert_called_once_with(self.app, net.id)

    def test_does_nothing_when_neither_resolves(self):
        from anetbbs.echomail import poller as poller_mod

        with self.app.app_context():
            with patch.object(poller_mod, 'poll_node_now') as fake_node, \
                 patch.object(poller_mod, 'poll_network_now') as fake_net:
                poller_mod.trigger_immediate_delivery(self.app)  # no args at all

            fake_node.assert_not_called()
            fake_net.assert_not_called()


class NetmailComposeCrashTriggerTests(unittest.TestCase):
    """A normal (non-direct-crash-reply) netmail that ticks the Crash
    checkbox must trigger an immediate delivery attempt, not just sit
    queued for the next scheduled poll."""

    def setUp(self):
        import tempfile
        self._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        self.addCleanup(
            lambda: setattr(cfg_mod.TestingConfig,
                            'SQLALCHEMY_DATABASE_URI', self._orig_db_uri))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'crash_compose.db'))
        with self.app.app_context():
            from anetbbs.models import db, User, EchomailNetwork
            db.create_all()
            user = User(username='cscomposer', email='cs@example.test',
                       password_hash='x', access_level=100, is_admin=True)
            db.session.add(user)
            net = EchomailNetwork(name='ComposeCrashNet', network_type='binkp',
                                  our_address='1200:1/1', hub_address='1200:1/2',
                                  is_active=True)
            db.session.add(net)
            db.session.commit()
            self.user_id = user.id
            self.net_id = net.id

    def _client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.user_id)
            sess['_fresh'] = True
        return client

    def test_crash_checkbox_on_fresh_compose_triggers_immediate_delivery(self):
        client = self._client()
        with patch('threading.Thread', _SyncThread), \
             patch('anetbbs.echomail.poller.trigger_immediate_delivery') as fake_trigger:
            resp = client.post('/netmail/compose', data={
                'to_address': '1200:1/99',
                'to_name': 'Remote Sysop',
                'subject': 'Urgent report',
                'body': 'This needs to go out now.',
                'is_crash': '1',
            }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        fake_trigger.assert_called_once()
        _call_app, kwargs = fake_trigger.call_args[0][0], fake_trigger.call_args[1]
        self.assertEqual(kwargs.get('network_id'), self.net_id)
        self.assertEqual(kwargs.get('to_address'), '1200:1/99')

    def test_without_crash_checkbox_no_immediate_delivery_triggered(self):
        client = self._client()
        with patch('threading.Thread', _SyncThread), \
             patch('anetbbs.echomail.poller.trigger_immediate_delivery') as fake_trigger:
            resp = client.post('/netmail/compose', data={
                'to_address': '1200:1/98',
                'to_name': 'Remote Sysop',
                'subject': 'Not urgent',
                'body': 'Whenever is fine.',
            }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        fake_trigger.assert_not_called()


class HatchLocalFileCrashTests(unittest.TestCase):
    """hatch_local_file() must set is_crash/is_hold on every queued row
    and, when is_crash, fire an immediate delivery attempt at each
    distinct affected peer."""

    def setUp(self):
        import tempfile
        self._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        self.addCleanup(
            lambda: setattr(cfg_mod.TestingConfig,
                            'SQLALCHEMY_DATABASE_URI', self._orig_db_uri))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'hatch_crash.db'))
        with self.app.app_context():
            from anetbbs.models import db
            db.create_all()

        # A real (tiny) file on disk -- hatch_local_file() crc32s/stats it.
        self._file_path = str(Path(self._tmp.name) / 'payload.bin')
        with open(self._file_path, 'wb') as f:
            f.write(b'hello world')

    def _make_area_with_subs(self, peer_addresses, tag='HATCHCRASH'):
        from anetbbs.models import db, EchomailNetwork, FileArea, FileEchoSubscription
        net = EchomailNetwork(name=f'{tag}Net', network_type='binkp',
                              our_address='1200:1/1', hub_address=peer_addresses[0],
                              is_active=True)
        db.session.add(net)
        db.session.commit()
        area = FileArea(tag=tag, name=f'{tag} Area', network_id=net.id,
                        storage_path=self._tmp.name)
        db.session.add(area)
        db.session.commit()
        for addr in peer_addresses:
            db.session.add(FileEchoSubscription(
                file_area_id=area.id, peer_address=addr, is_active=True))
        db.session.commit()
        return area

    def test_default_call_sets_no_flavor_and_does_not_trigger(self):
        from anetbbs.echomail.tic import hatch_local_file
        from anetbbs.models import HatchQueue

        with self.app.app_context():
            area = self._make_area_with_subs(['1200:1/2'])
            with patch('threading.Thread', _SyncThread), \
                 patch('anetbbs.echomail.poller.trigger_immediate_delivery') as fake_trigger:
                n = hatch_local_file(area, self._file_path, 'payload.bin')

            self.assertEqual(n, 1)
            row = HatchQueue.query.filter_by(filename='payload.bin').first()
            self.assertFalse(row.is_crash)
            self.assertFalse(row.is_hold)
            fake_trigger.assert_not_called()

    def test_is_hold_row_flag_set_without_triggering_delivery(self):
        from anetbbs.echomail.tic import hatch_local_file
        from anetbbs.models import HatchQueue

        with self.app.app_context():
            area = self._make_area_with_subs(['1200:1/3'], tag='HATCHHOLD')
            with patch('threading.Thread', _SyncThread), \
                 patch('anetbbs.echomail.poller.trigger_immediate_delivery') as fake_trigger:
                hatch_local_file(area, self._file_path, 'held.bin', is_hold=True)

            row = HatchQueue.query.filter_by(filename='held.bin').first()
            self.assertTrue(row.is_hold)
            self.assertFalse(row.is_crash)
            fake_trigger.assert_not_called()

    def test_is_crash_sets_row_flag_and_triggers_immediate_delivery_per_peer(self):
        from anetbbs.echomail.tic import hatch_local_file
        from anetbbs.models import HatchQueue

        with self.app.app_context():
            area = self._make_area_with_subs(
                ['1200:1/4', '1200:1/5'], tag='HATCHURGENT')
            with patch('threading.Thread', _SyncThread), \
                 patch('anetbbs.echomail.poller.trigger_immediate_delivery') as fake_trigger:
                n = hatch_local_file(area, self._file_path, 'urgent.bin',
                                     is_crash=True)

            self.assertEqual(n, 2)
            rows = HatchQueue.query.filter_by(filename='urgent.bin').all()
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(r.is_crash for r in rows))

            triggered_peers = {
                c.kwargs.get('to_address') for c in fake_trigger.call_args_list
            }
            self.assertEqual(triggered_peers, {'1200:1/4', '1200:1/5'})


if __name__ == '__main__':
    unittest.main()
