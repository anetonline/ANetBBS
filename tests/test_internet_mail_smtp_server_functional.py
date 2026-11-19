"""Functional tests for anetbbs/mail/smtp_server.py: drive a real raw
SMTP conversation against serve() bound to a non-privileged dev port,
confirming the actual wire protocol (not just the parsing helpers in
isolation) produces the right InternetMail rows and rejections.

DNSBL is mocked to "not listed" in these tests -- real network DNS
lookups have no place in a fast, offline test suite; the fail-open
behavior itself is covered by the lower-level unit tests in this same
file.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod

_PORT = 12525


class _BaseSmtpFunctionalTest(unittest.TestCase):
    _db_name = '.internet_mail_smtp_functional_test.db'

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / cls._db_name)
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'
        os.environ['SMTP_LISTEN_HOST'] = '127.0.0.1'
        os.environ['SMTP_LISTEN_PORT'] = str(_PORT)
        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def setUp(self):
        # smtp_server._flask_app() caches its Flask app at module level
        # (intentionally, for production -- same pattern finger_server.py
        # uses). Each test class here points TestingConfig.SQLALCHEMY_
        # DATABASE_URI at its own db file, so a cache left over from an
        # earlier class in this same pytest process would silently point
        # the server at the WRONG file. Reset it before every test.
        from anetbbs.mail import smtp_server
        if hasattr(smtp_server._flask_app, '_app'):
            del smtp_server._flask_app._app

        from anetbbs.models import db, MailConfig
        with self.app.app_context():
            cfg = MailConfig.get()
            cfg.enabled = True
            cfg.domain = 'bbs.a-net.fyi'
            cfg.max_message_size = 1024  # small, deliberately -- see oversized test
            db.session.commit()

    def _make_user(self, username, email_local_part):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User(username=username, email=f'{username}@example.com',
                    password_hash='x', email_enabled=True,
                    email_local_part=email_local_part)
            db.session.add(u)
            db.session.commit()
            return u.id

    def _run(self, coro):
        return asyncio.run(self._with_server(coro))

    async def _with_server(self, client_coro):
        from anetbbs.mail import smtp_server
        with mock.patch('anetbbs.mail.smtp_server._dnsbl_check',
                        return_value=False):
            task = asyncio.ensure_future(smtp_server.serve())
            try:
                # Give the listener a moment to actually bind before the
                # client connects.
                await asyncio.sleep(0.1)
                if task.done():
                    task.result()  # surface a bind failure immediately
                return await client_coro()
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def _converse(self, lines):
        """Send each line in `lines`, collecting one response line per
        send. Returns the list of decoded response lines (greeting
        first)."""
        reader, writer = await asyncio.open_connection('127.0.0.1', _PORT)
        responses = []
        try:
            responses.append(
                (await asyncio.wait_for(reader.readline(), timeout=5)).decode())
            for line in lines:
                writer.write((line + '\r\n').encode())
                await writer.drain()
                responses.append(
                    (await asyncio.wait_for(reader.readline(), timeout=5)).decode())
        finally:
            writer.close()
        return responses


class SmtpServerBasicDeliveryTests(_BaseSmtpFunctionalTest):
    _db_name = '.internet_mail_smtp_basic_delivery_test.db'

    def test_full_conversation_delivers_a_real_internet_mail_row(self):
        from anetbbs.models import InternetMail, Notification
        with self.app.app_context():
            from anetbbs.models import MailConfig, db
            MailConfig.get().max_message_size = 25 * 1024 * 1024
            db.session.commit()
        user_id = self._make_user('stingray', 'stingray')

        async def scenario():
            return await self._converse([
                'EHLO mail.example.com',
                'MAIL FROM:<friend@example.com>',
                'RCPT TO:<stingray@bbs.a-net.fyi>',
                'DATA',
                'Subject: Hello\r\n'
                '\r\n'
                'Test body line one.\r\n'
                '.',
                'QUIT',
            ])

        responses = self._run(scenario)
        self.assertTrue(responses[0].startswith('220'))
        self.assertTrue(responses[3].startswith('250'), responses[3])  # RCPT TO
        self.assertTrue(responses[4].startswith('354'), responses[4])  # DATA
        self.assertTrue(responses[5].startswith('250'), responses[5])  # accepted

        with self.app.app_context():
            row = InternetMail.query.filter_by(user_id=user_id).first()
            self.assertIsNotNone(row)
            self.assertEqual(row.direction, 'inbound')
            self.assertEqual(row.from_addr, 'friend@example.com')
            self.assertEqual(row.to_addr, 'stingray@bbs.a-net.fyi')
            self.assertIn('Test body line one.', row.body)
            notification = Notification.query.filter_by(
                user_id=user_id, kind='internet_mail').one()
            self.assertEqual(notification.target_url, f'/mail/{row.id}')
            self.assertEqual(notification.title, 'New Internet Mail')


class SmtpServerUnknownRecipientTests(_BaseSmtpFunctionalTest):
    _db_name = '.internet_mail_smtp_unknown_recipient_test.db'

    def test_unknown_recipient_rejected_with_550_and_writes_no_row(self):
        from anetbbs.models import InternetMail
        self._make_user('stingray', 'stingray')

        async def scenario():
            return await self._converse([
                'EHLO mail.example.com',
                'MAIL FROM:<friend@example.com>',
                'RCPT TO:<nosuchuser@bbs.a-net.fyi>',
            ])

        responses = self._run(scenario)
        self.assertTrue(responses[3].startswith('550'), responses[3])

        with self.app.app_context():
            self.assertEqual(InternetMail.query.count(), 0)

    def test_wrong_domain_also_rejected(self):
        self._make_user('stingray2', 'stingray2')

        async def scenario():
            return await self._converse([
                'EHLO mail.example.com',
                'MAIL FROM:<friend@example.com>',
                'RCPT TO:<stingray2@some-other-domain.com>',
            ])

        responses = self._run(scenario)
        self.assertTrue(responses[3].startswith('550'), responses[3])


class SmtpServerOversizedMessageTests(_BaseSmtpFunctionalTest):
    _db_name = '.internet_mail_smtp_oversized_test.db'

    def test_oversized_body_rejected_with_552_and_writes_no_row(self):
        from anetbbs.models import InternetMail
        # setUp() already pins MailConfig.max_message_size to 1024 bytes.
        self._make_user('stingray3', 'stingray3')

        async def scenario():
            big_body = 'X' * 4096
            return await self._converse([
                'EHLO mail.example.com',
                'MAIL FROM:<friend@example.com>',
                'RCPT TO:<stingray3@bbs.a-net.fyi>',
                'DATA',
                f'Subject: Too big\r\n\r\n{big_body}\r\n.',
                'QUIT',
            ])

        responses = self._run(scenario)
        self.assertTrue(responses[5].startswith('552'), responses[5])

        with self.app.app_context():
            self.assertEqual(InternetMail.query.count(), 0)


class DnsblFailOpenTests(unittest.TestCase):
    """Lower-level unit test (no real server/socket) for the fail-open
    guarantee itself: a DNSBL that times out or errors must never block
    legitimate mail."""

    def test_resolver_timeout_is_treated_as_not_listed(self):
        import dns.resolver
        from anetbbs.mail.smtp_server import _dnsbl_check

        async def scenario():
            with mock.patch('dns.asyncresolver.Resolver.resolve',
                            side_effect=dns.resolver.LifetimeTimeout()):
                return await _dnsbl_check('203.0.113.5', ['zen.spamhaus.org'])

        self.assertFalse(asyncio.run(scenario()))

    def test_nxdomain_is_treated_as_not_listed(self):
        import dns.resolver
        from anetbbs.mail.smtp_server import _dnsbl_check

        async def scenario():
            with mock.patch('dns.asyncresolver.Resolver.resolve',
                            side_effect=dns.resolver.NXDOMAIN()):
                return await _dnsbl_check('203.0.113.5', ['zen.spamhaus.org'])

        self.assertFalse(asyncio.run(scenario()))

    def test_positive_hit_is_treated_as_listed(self):
        from anetbbs.mail.smtp_server import _dnsbl_check

        async def scenario():
            with mock.patch('dns.asyncresolver.Resolver.resolve',
                            return_value=[mock.Mock(address='127.0.0.2')]):
                return await _dnsbl_check('203.0.113.5', ['zen.spamhaus.org'])

        self.assertTrue(asyncio.run(scenario()))

    def test_ipv6_peer_is_not_checked(self):
        from anetbbs.mail.smtp_server import _dnsbl_check

        async def scenario():
            with mock.patch('dns.asyncresolver.Resolver.resolve') as mocked:
                result = await _dnsbl_check('2001:db8::1', ['zen.spamhaus.org'])
                mocked.assert_not_called()
                return result

        self.assertFalse(asyncio.run(scenario()))


if __name__ == '__main__':
    unittest.main()
