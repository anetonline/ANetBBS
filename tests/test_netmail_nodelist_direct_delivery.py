"""Direct delivery against real models/SQLite, with only transport mocked.

Use a minimal Flask app: routing and queue tests do not need background
services, seed data, or the web application's unrelated integrations.
"""
import json
import ast
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from flask import Flask

from anetbbs.models import (db, BinkPNode, EchomailNetwork, EchomailPollLog,
                            NetmailMessage, Nodelist, NodelistEntry)
from anetbbs.echomail import direct_netmail as direct, poller, tosser


class EndpointParsingTests(unittest.TestCase):
    def endpoint(self, flags, **kwargs):
        return direct.nodelist_endpoint(SimpleNamespace(
            flags=json.dumps(flags), keyword_type=kwargs.get('keyword_type'),
            phone=kwargs.get('phone', '-Unpublished-')))

    def test_current_and_legacy_endpoints(self):
        cases = [
            ({'INA': 'recipient.example', 'IBN': True}, {}, 'recipient.example', 24554),
            ({'INA': 'recipient.example', 'IBN': '24555'}, {}, 'recipient.example', 24555),
            ({'INA': 'unused.example', 'IBN': 'override.example:12345'}, {},
             'override.example', 12345),
            ({'IBN': 'recipient.example'}, {}, 'recipient.example', 24554),
            ({'ina': 'recipient.example', 'ibn': True}, {}, 'recipient.example', 24554),
            ({'INA': '2001:db8::1', 'IBN': True}, {}, '2001:db8::1', 24554),
            ({'IBN': '[2001:db8::1]:24555'}, {}, '2001:db8::1', 24555),
            ({'IBN': True}, {'phone': '000-203-0-113-1'}, '203.0.113.1', 24554),
            ({'IBN': True}, {'phone': 'legacy.example'}, 'legacy.example', 24554),
        ]
        for flags, kwargs, host, port in cases:
            with self.subTest(flags=flags, kwargs=kwargs):
                endpoint = self.endpoint(flags, **kwargs)
                self.assertIsNotNone(endpoint)
                self.assertEqual((endpoint.host, endpoint.port), (host, port))

    def test_unusable_endpoints_are_rejected(self):
        for flags, kwargs in [
            ({'INA': 'recipient.example'}, {}),
            ({'INA': 'recipient.example', 'ITN': True}, {}),
            ({'IBN': True}, {}),
            ({'INA': 'recipient.example', 'IBN': '0'}, {}),
            ({'INA': 'recipient.example', 'IBN': '65536'}, {}),
            ({'IBN': 'recipient.example:bad'}, {}),
            ({'IBN': 'https://recipient.example'}, {}),
            ({'INA': 'recipient.example\r\n', 'IBN': True}, {}),
            ({'INA': True, 'IBN': True}, {}),
            ({'INA': 'recipient.example', 'IBN': True}, {'keyword_type': 'Down'}),
            ({'INA': 'recipient.example', 'IBN': True}, {'keyword_type': 'Hold'}),
            ({'INA': 'recipient.example', 'IBN': True, 'Down': True}, {}),
        ]:
            with self.subTest(flags=flags, kwargs=kwargs):
                self.assertIsNone(self.endpoint(flags, **kwargs))
        for malformed in ('not json', '[]', 'null', '42'):
            with self.subTest(malformed=malformed):
                self.assertIsNone(direct.nodelist_endpoint(SimpleNamespace(
                    flags=malformed, keyword_type=None, phone='')))


class DirectDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = Flask(__name__)
        self.app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///' + str(
            Path(self.tmp.name) / 'netmail.db'), ECHOMAIL_DATA_DIR=self.tmp.name)
        db.init_app(self.app)
        self.context = self.app.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)
        self.addCleanup(db.session.remove)
        db.create_all()
        self.network = EchomailNetwork(
            name='FidoNet', ftn_domain='fidonet', network_type='binkp', is_active=True,
            our_address='1:123/3003', hub_address='1:123/0',
            binkp_host='hub.example', binkp_password='UPSTREAM-SECRET',
            packet_password='UPSTREAM-PACKET')
        db.session.add(self.network)
        db.session.commit()
        self.nodelist = self.add_nodelist()
        self.add_entry()

    def add_nodelist(self, domain='fidonet', **kwargs):
        nl = Nodelist(domain=domain, release_date=kwargs.get('release_date', date.today()))
        db.session.add(nl)
        db.session.commit()
        return nl

    def add_entry(self, address='1:123/999', nodelist=None, flags=None, **kwargs):
        z, net, node, point = direct.address_key(address)
        entry = NodelistEntry(
            nodelist_id=(nodelist or self.nodelist).id,
            zone=z, net=net, node=node, point=point,
            flags=json.dumps(flags if flags is not None else {
                'INA': 'recipient.example', 'IBN': '24555'}), **kwargs)
        db.session.add(entry)
        db.session.commit()
        return entry

    def message(self, **kwargs):
        values = dict(network_id=self.network.id, direction='outbound', status='queued',
                      is_crash=True, from_address='1:123/3003', to_address='1:123/999',
                      from_name='StingRay', to_name='Recipient', subject='Direct test',
                      body='Recipient-specific test', owner_resolved=True)
        values.update(kwargs)
        nm = NetmailMessage(**values)
        db.session.add(nm)
        db.session.commit()
        return nm

    def send(self, nm, result=None, side_effect=None):
        client = MagicMock()
        client.poll.return_value = result or {'sent': 1, 'received': []}
        client.poll.side_effect = side_effect
        with patch('anetbbs.echomail.binkp.BinkPClient', return_value=client) as ctor:
            poller.trigger_immediate_delivery(
                self.app, network_id=self.network.id, to_address=nm.to_address,
                netmail_id=nm.id)
        db.session.expire_all()
        return ctor, client

    def test_nodelisted_recipient_dials_direct_with_correct_identity_and_no_hub_secrets(self):
        nm = self.message()
        ctor, client = self.send(nm)
        args = ctor.call_args.kwargs
        self.assertEqual((args['host'], args['port']), ('recipient.example', 24555))
        self.assertEqual(args['our_address'], self.network.our_address)
        self.assertEqual(args['hub_address'], nm.to_address)
        self.assertEqual(args['domain'], 'fidonet')
        self.assertEqual(args['password'], '')
        self.assertEqual(args['packet_password'], '')
        self.assertTrue(args['default_crash'])
        self.assertEqual(len(client.poll.call_args.kwargs['outbound_messages']), 1)
        self.assertEqual(nm.status, 'sent')
        self.assertTrue(nm.is_sent)
        self.assertIsNotNone(nm.sent_at)
        self.assertIsNone(nm.delivery_started_at)
        log = EchomailPollLog.query.one()
        self.assertEqual(log.messages_sent, 1)
        self.assertEqual(log.status, 'success')
        self.assertIn('ROUTE DIRECT', log.transcript)
        self.assertIn('recipient.example:24555', log.transcript)

    def test_configured_node_overrides_nodelist_and_uses_its_own_password(self):
        node = BinkPNode(name='Recipient', network_id=self.network.id,
                         ftn_address='1:123/999.0@fidonet', password='NODE-SECRET',
                         binkp_host='configured.example', binkp_port=12345, binkp_tls=True)
        db.session.add(node)
        db.session.commit()
        ctor, _ = self.send(self.message())
        args = ctor.call_args.kwargs
        self.assertEqual((args['host'], args['port']), ('configured.example', 12345))
        self.assertEqual(args['password'], 'NODE-SECRET')
        self.assertTrue(args['use_tls'])
        self.assertEqual(args['packet_password'], '')
        self.assertEqual(EchomailPollLog.query.one().node_id, node.id)

    def test_configured_password_survives_nodelist_host_lookup(self):
        db.session.add(BinkPNode(name='Recipient', network_id=self.network.id,
                                ftn_address='1:123/999', password='NODE-SECRET'))
        db.session.commit()
        ctor, _ = self.send(self.message())
        self.assertEqual(ctor.call_args.kwargs['host'], 'recipient.example')
        self.assertEqual(ctor.call_args.kwargs['password'], 'NODE-SECRET')

    def test_actual_hub_recipient_uses_hub_credentials(self):
        ctor, _ = self.send(self.message(to_address=self.network.hub_address))
        args = ctor.call_args.kwargs
        self.assertEqual(args['host'], 'hub.example')
        self.assertEqual(args['password'], 'UPSTREAM-SECRET')
        self.assertEqual(args['packet_password'], 'UPSTREAM-PACKET')

    def test_hub_credentials_are_kept_when_only_its_hostname_comes_from_nodelist(self):
        self.network.binkp_host = None
        db.session.commit()
        self.add_entry(address=self.network.hub_address)
        ctor, _ = self.send(self.message(to_address=self.network.hub_address))
        self.assertEqual(ctor.call_args.kwargs['host'], 'recipient.example')
        self.assertEqual(ctor.call_args.kwargs['password'], 'UPSTREAM-SECRET')

    def test_point_and_qualified_addresses_remain_exact(self):
        self.add_entry(address='1:123/999.7', flags={'INA': 'point.example', 'IBN': True})
        ctor, _ = self.send(self.message(to_address='1:123/999.7@fidonet'))
        self.assertEqual(ctor.call_args.kwargs['host'], 'point.example')
        self.assertEqual(ctor.call_args.kwargs['hub_address'], '1:123/999.7')

    def test_unlisted_point_does_not_dial_boss_as_if_it_were_the_point(self):
        nm = self.message(to_address='1:123/999.8')
        ctor, _ = self.send(nm)
        ctor.assert_not_called()
        self.assertEqual(nm.status, 'queued')

    def test_no_cross_network_node_or_nodelist_lookup(self):
        other = EchomailNetwork(name='OtherNet', ftn_domain='other', network_type='binkp')
        db.session.add(other)
        db.session.commit()
        db.session.add(BinkPNode(name='Wrong network', network_id=other.id,
                                ftn_address='1:123/999', binkp_host='wrong.example',
                                password='WRONG-SECRET'))
        other_list = self.add_nodelist('other')
        self.add_entry(nodelist=other_list, flags={'INA': 'wrong-list.example', 'IBN': True})
        ctor, _ = self.send(self.message())
        self.assertEqual(ctor.call_args.kwargs['host'], 'recipient.example')
        self.assertEqual(ctor.call_args.kwargs['password'], '')

    def test_explicit_wrong_domain_is_rejected(self):
        nm = self.message(to_address='1:123/999@other')
        ctor, _ = self.send(nm)
        ctor.assert_not_called()
        self.assertIn('domain', nm.error_message)

    def test_removed_recipient_is_not_resurrected_from_an_older_nodelist(self):
        self.add_nodelist(release_date=date.today() + timedelta(days=7))
        nm = self.message()
        ctor, _ = self.send(nm)
        ctor.assert_not_called()
        self.assertEqual(nm.status, 'queued')
        self.assertFalse(nm.is_sent)
        self.assertIn('No usable BinkP endpoint', nm.error_message)
        self.assertEqual(EchomailPollLog.query.one().status, 'error')

    def test_missing_nodelist_does_not_fall_back_to_hub(self):
        db.session.delete(self.nodelist)
        db.session.commit()
        nm = self.message()
        ctor, _ = self.send(nm)
        ctor.assert_not_called()
        self.assertIn('No nodelist', nm.error_message)

    def test_unacknowledged_and_failed_sends_stay_queued(self):
        for result, error in [({'sent': 0, 'received': []}, None),
                              (None, ConnectionRefusedError('refused'))]:
            with self.subTest(error=error):
                nm = self.message()
                self.send(nm, result=result, side_effect=error)
                self.assertEqual(nm.status, 'queued')
                self.assertFalse(nm.is_sent)
                self.assertIsNone(nm.sent_at)
                self.assertIsNone(nm.delivery_started_at)
                self.assertTrue(nm.error_message)

    def test_retry_clears_previous_error(self):
        nm = self.message(error_message='Previous failure')
        self.send(nm)
        self.assertEqual(nm.status, 'sent')
        self.assertIsNone(nm.error_message)

    def test_sent_draft_inbound_hold_and_normal_mail_are_not_direct_dialed(self):
        for values in [dict(status='sent'), dict(status='draft'), dict(direction='inbound'),
                       dict(is_hold=True), dict(is_crash=False)]:
            with self.subTest(values=values):
                ctor, _ = self.send(self.message(**values))
                ctor.assert_not_called()

    def test_reentrant_send_cannot_claim_the_same_message(self):
        nm = self.message()
        calls = []
        def in_flight(**kwargs):
            with self.app.app_context():
                row = db.session.get(NetmailMessage, nm.id)
                self.assertEqual(row.status, 'sending')
                direct.send_queued_netmail(self.app, nm.id)
                calls.append(row.status)
            return {'sent': 1, 'received': []}
        ctor, client = self.send(nm, side_effect=in_flight)
        ctor.assert_called_once()
        self.assertEqual(calls, ['sending'])

    def test_expired_claim_recovers_but_live_claim_is_preserved(self):
        stale = self.message(status='sending', delivery_started_at=datetime.utcnow()-timedelta(hours=1))
        live = self.message(status='sending', delivery_started_at=datetime.utcnow())
        self.assertEqual(direct.recover_interrupted(self.network.id), 1)
        db.session.expire_all()
        self.assertEqual(stale.status, 'queued')
        self.assertEqual(live.status, 'sending')

    def test_scheduled_direct_retry_works_on_a_network_hosted_by_this_bbs(self):
        self.network.hub_address = self.network.our_address
        db.session.commit()
        nm = self.message()
        with patch('anetbbs.echomail.binkp.BinkPClient') as ctor, \
                patch.object(poller, '_run_client') as hub:
            ctor.return_value.poll.return_value = {'sent': 1, 'received': []}
            poller._do_poll(self.app, self.network)
        hub.assert_not_called()
        db.session.expire_all()
        self.assertEqual(nm.status, 'sent')
        self.assertIsNotNone(self.network.last_poll_at)

    def test_disconnect_after_got_does_not_requeue_delivered_mail(self):
        nm = self.message()
        client = MagicMock(acknowledged_messages=1)
        client.poll.side_effect = ConnectionError('receive side closed after GOT')
        with patch('anetbbs.echomail.binkp.BinkPClient', return_value=client):
            direct.send_queued_netmail(self.app, nm.id)
        db.session.expire_all()
        self.assertEqual(nm.status, 'sent')
        self.assertTrue(nm.is_sent)
        self.assertIsNotNone(nm.sent_at)
        self.assertEqual(EchomailPollLog.query.one().messages_sent, 1)

    def test_bad_returned_netmail_does_not_undo_delivery(self):
        nm = self.message()
        def invalid_import(*args, **kwargs):
            db.session.add(NetmailMessage(body='Missing required names', owner_resolved=True))
            db.session.flush()
        with patch.object(poller, '_import_netmail', side_effect=invalid_import), \
                self.assertLogs(direct.logger, level='ERROR'):
            self.send(nm, result={'sent': 1, 'received': [{'body': 'bad'}]})
        self.assertEqual(nm.status, 'sent')
        self.assertTrue(nm.is_sent)
        self.assertEqual(NetmailMessage.query.count(), 1)

    def test_web_compose_passes_the_exact_message_id_to_the_direct_sender(self):
        from flask_login import LoginManager
        from anetbbs.models import User
        from anetbbs.web.netmail import netmail_bp
        from anetbbs.features import rate_limit
        rate_limit._buckets.clear()
        self.addCleanup(rate_limit._buckets.clear)
        self.app.config.update(SECRET_KEY='test', TESTING=True)
        login = LoginManager(self.app)
        login.user_loader(lambda user_id: db.session.get(User, int(user_id)))
        self.app.register_blueprint(netmail_bp)
        user = User(username='stingray', email='stingray@example.test', password_hash='x',
                    is_admin=True, access_level=100)
        db.session.add(user)
        db.session.commit()
        client = self.app.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = str(user.id)
            session['_fresh'] = True

        class SyncThread:
            def __init__(self, target, **kwargs):
                self.target = target
            def start(self):
                self.target()

        with patch('threading.Thread', SyncThread), \
                patch.object(poller, 'trigger_immediate_delivery') as trigger:
            response = client.post('/netmail/compose', data={
                'to_address': '1:123/999', 'to_name': 'Recipient',
                'subject': 'Direct', 'body': 'Test', 'is_crash': '1'})
        self.assertEqual(response.status_code, 302)
        row = NetmailMessage.query.one()
        self.assertEqual(trigger.call_args.kwargs, {
            'network_id': self.network.id, 'to_address': row.to_address, 'netmail_id': row.id})

    def test_existing_sqlite_database_gets_an_idempotent_lease_column_migration(self):
        # Load the actual migration functions without importing the web
        # server's eventlet/Socket.IO startup or unrelated integrations.
        source = Path(__file__).resolve().parents[1] / 'anetbbs' / 'web_app.py'
        tree = ast.parse(source.read_text())
        names = {'_lightweight_migrate', '_seed_default_hub_identity',
                 '_backfill_binkp_node_network_id'}
        functions = ast.Module(body=[node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in names], type_ignores=[])
        namespace = {'db': db, 'datetime': datetime, 'os': os, '__name__': 'anetbbs.web_app'}
        exec(compile(functions, str(source), 'exec'), namespace)
        network_id = self.network.id
        db.session.remove()
        from sqlalchemy import inspect, text
        with db.engine.begin() as conn:
            conn.execute(text('ALTER TABLE netmail_messages DROP COLUMN delivery_started_at'))
        for _ in range(2):
            namespace['_lightweight_migrate'](self.app)
            self.assertIn('delivery_started_at', {
                col['name'] for col in inspect(db.engine).get_columns('netmail_messages')})
        self.network = db.session.get(EchomailNetwork, network_id)
        ctor, _ = self.send(self.message())
        ctor.assert_called_once()

    def test_scheduled_poll_sends_crash_direct_and_normal_mail_via_hub(self):
        crash = self.message()
        normal = self.message(is_crash=False)
        with patch('anetbbs.echomail.binkp.BinkPClient') as ctor, \
                patch.object(poller, '_run_client', return_value={'sent': 1, 'received': []}) as hub:
            ctor.return_value.poll.return_value = {'sent': 1, 'received': []}
            poller._do_poll(self.app, self.network)
        self.assertEqual(ctor.call_args.kwargs['host'], 'recipient.example')
        hub_outbound = hub.call_args.args[1]
        self.assertEqual([msg._nm.id for msg in hub_outbound], [normal.id])
        db.session.expire_all()
        self.assertEqual(crash.status, 'sent')
        self.assertEqual(normal.status, 'sent')

    def test_failed_direct_mail_is_excluded_from_scheduled_hub_packet(self):
        crash = self.message()
        with patch('anetbbs.echomail.binkp.BinkPClient') as ctor, \
                patch.object(poller, '_run_client', return_value={'sent': 0, 'received': []}) as hub:
            ctor.return_value.poll.return_value = {'sent': 0, 'received': []}
            poller._do_poll(self.app, self.network)
        self.assertEqual(hub.call_args.args[1], [])
        db.session.expire_all()
        self.assertEqual(crash.status, 'queued')

    def test_network_default_direct_also_resolves_recipient(self):
        self.network.default_direct = True
        db.session.commit()
        ctor, _ = self.send(self.message(is_crash=False))
        self.assertEqual(ctor.call_args.kwargs['host'], 'recipient.example')

    def test_hub_polling_in_cannot_pick_up_another_recipients_direct_mail(self):
        crash = self.message()
        normal = self.message(is_crash=False)
        hub_mail = self.message(to_address=self.network.hub_address)
        pending = tosser.get_pending_netmail_for_network(self.network.id, include_hold=True)
        self.assertEqual({m.id for m in pending}, {normal.id, hub_mail.id})
        self.assertNotIn(crash, pending)

    def test_returned_netmail_imported_with_anonymous_trust_and_echo_not_imported(self):
        with patch.object(poller, '_import_netmail', return_value=1) as import_nm:
            self.send(self.message(), result={'sent': 1, 'received': [
                {'area_tag': None, 'body': 'reply'}, {'area_tag': 'UNEXPECTED.ECHO'}]})
        import_nm.assert_called_once()
        self.assertEqual(import_nm.call_args.kwargs, {'trust': 'insecure'})
        self.assertEqual(EchomailPollLog.query.one().messages_received, 1)

    def test_actual_returned_netmail_is_saved_as_unverified_on_the_same_network(self):
        nm = self.message()
        self.send(nm, result={'sent': 1, 'received': [{
            'from_name': 'Remote', 'from_address': '1:123/999',
            'to_name': 'StingRay', 'to_address': self.network.our_address,
            'subject': 'Reply', 'body': 'Returned during direct session',
            'msg_id': '1:123/999 direct-test'}]})
        reply = NetmailMessage.query.filter_by(direction='inbound').one()
        self.assertEqual(reply.trust, 'insecure')
        self.assertEqual(reply.network_id, self.network.id)
        self.assertEqual(reply.body, 'Returned during direct session')
        self.assertEqual(nm.status, 'sent')


class DirectProtocolTests(unittest.TestCase):
    def test_challenge_authentication_does_not_emit_a_bare_cram_option(self):
        import hashlib
        import hmac
        from anetbbs.echomail.binkp import CMD_NUL, CMD_ADR, CMD_PWD, CMD_OK
        challenge = 'b7d37f80598093bdfcb0e897fd0b4676'
        for password in ('', 'configured-secret'):
            with self.subTest(password_configured=bool(password)):
                frames = [(True, bytes([CMD_NUL]) +
                           f'OPT CRAM-MD5-{challenge}'.encode()),
                          (True, bytes([CMD_ADR]) + b'1:123/999'),
                          (True, bytes([CMD_OK]))]
                client = self.client(frames)
                client.password = password
                client._handshake()
                sent = [call.args for call in client._send_cmd.call_args_list]
                self.assertNotIn((CMD_NUL, 'OPT CRAM-MD5'), sent)
                expected = hmac.new((password or '-').encode(),
                                    bytes.fromhex(challenge), hashlib.md5).hexdigest()
                self.assertIn((CMD_PWD, 'CRAM-MD5-' + expected), sent)

    def client(self, frames, strict=True):
        from anetbbs.echomail.binkp import BinkPClient
        client = BinkPClient('recipient.example', 24554, '1:123/3003', '1:123/999',
                             require_remote_address=strict)
        client._send_cmd = MagicMock()
        client._recv_frame_logged = MagicMock(side_effect=frames)
        return client

    def test_direct_handshake_requires_recipient_aka(self):
        from anetbbs.echomail.binkp import CMD_ADR, CMD_OK
        frames = [(True, bytes([CMD_ADR]) + b'1:123/0 1:123/999.0@fidonet'),
                  (True, bytes([CMD_OK]))]
        self.client(frames)._handshake()

    def test_wrong_system_or_missing_adr_cannot_accept_direct_mail(self):
        from anetbbs.echomail.binkp import CMD_ADR, CMD_OK
        for frames in [[(True, bytes([CMD_ADR]) + b'1:123/888'),
                        (True, bytes([CMD_OK]))], [(True, bytes([CMD_OK]))]]:
            with self.subTest(frames=frames):
                with self.assertRaisesRegex(ConnectionError, 'expected recipient'):
                    client = self.client(frames)
                    client._handshake()
                from anetbbs.echomail.binkp import CMD_PWD
                self.assertNotIn(CMD_PWD, [call.args[0] for call in client._send_cmd.call_args_list])

    def test_legacy_hub_handshake_keeps_warning_only_behavior(self):
        from anetbbs.echomail.binkp import CMD_ADR, CMD_OK
        self.client([(True, bytes([CMD_ADR]) + b'1:123/888'),
                     (True, bytes([CMD_OK]))], strict=False)._handshake()

    def test_real_client_preserves_got_receipt_if_receive_later_fails(self):
        client = self.client([])
        with tempfile.TemporaryDirectory() as data_dir, \
                patch.object(client, '_connect'), patch.object(client, '_handshake'), \
                patch.object(client, '_disconnect'), \
                patch.object(client, '_send_messages', return_value=1), \
                patch.object(client, '_send_outbound_dir_files', return_value=(0, [])), \
                patch.object(client, '_send_freq_requests', return_value=([], [])), \
                patch.object(client, '_receive_messages', side_effect=ConnectionError('closed')):
            with self.assertRaises(ConnectionError):
                client.poll(outbound_messages=[object()], data_dir=data_dir)
        self.assertEqual(client.acknowledged_messages, 1)


if __name__ == '__main__':
    unittest.main()
