"""Regression tests for a test-coverage gap found in a full echomail-
subsystem audit: anetbbs/echomail/routing.py's parse_address(),
find_network_for_address(), and resolve_netmail_recipient() had no
dedicated test file, despite handling peer-supplied packet data
(addresses/names arriving over the wire, not validated anywhere
upstream) -- a realistic surface for malformed/garbage input.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class ParseAddressTests(unittest.TestCase):
    """Pure function, no DB needed."""

    def test_well_formed_address_parses(self):
        from anetbbs.echomail.routing import parse_address
        self.assertEqual(parse_address('1:114/30'), (1, 114, 30, 0))

    def test_point_address_parses(self):
        from anetbbs.echomail.routing import parse_address
        self.assertEqual(parse_address('1200:1/2.5'), (1200, 1, 2, 5))

    def test_whitespace_is_stripped(self):
        from anetbbs.echomail.routing import parse_address
        self.assertEqual(parse_address('  1:114/30  '), (1, 114, 30, 0))

    def test_empty_string_returns_none(self):
        from anetbbs.echomail.routing import parse_address
        self.assertIsNone(parse_address(''))

    def test_none_input_returns_none(self):
        from anetbbs.echomail.routing import parse_address
        self.assertIsNone(parse_address(None))

    def test_symbolic_non_ftn_address_returns_none(self):
        """A peer that sends a symbolic name instead of a real FTN
        address (e.g. a misconfigured field, or garbage from a buggy
        tosser) must not raise -- just fail to parse."""
        from anetbbs.echomail.routing import parse_address
        self.assertIsNone(parse_address('FIDONET'))

    def test_missing_slash_returns_none(self):
        from anetbbs.echomail.routing import parse_address
        self.assertIsNone(parse_address('1:114'))

    def test_missing_colon_returns_none(self):
        from anetbbs.echomail.routing import parse_address
        self.assertIsNone(parse_address('1114/30'))

    def test_non_numeric_components_return_none(self):
        from anetbbs.echomail.routing import parse_address
        self.assertIsNone(parse_address('a:b/c'))

    def test_trailing_domain_suffix_returns_none(self):
        """A '@domain' suffix (common in kludge-line addresses) is NOT
        stripped by this function -- callers that might receive one
        (e.g. from a raw kludge line) must strip it themselves first.
        Confirms current (not necessarily ideal) behavior explicitly,
        so a future change here is a deliberate decision, not a silent
        regression."""
        from anetbbs.echomail.routing import parse_address
        self.assertIsNone(parse_address('1:114/30@fidonet'))

    def test_negative_numbers_are_rejected(self):
        from anetbbs.echomail.routing import parse_address
        self.assertIsNone(parse_address('-1:114/30'))

    def test_garbage_binary_data_does_not_raise(self):
        """Simulates a badly-corrupted or non-address field arriving
        from a malformed packet -- must fail closed, never throw."""
        from anetbbs.echomail.routing import parse_address
        self.assertIsNone(parse_address('\x00\x01\x02garbage\xff'))


class FindNetworkForAddressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.routing_find_network_test.db')
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

    def test_malformed_destination_address_returns_none_not_a_crash(self):
        from anetbbs.echomail.routing import find_network_for_address
        with self.app.app_context():
            self.assertIsNone(find_network_for_address('not-a-real-address'))

    def test_empty_destination_address_returns_none(self):
        from anetbbs.echomail.routing import find_network_for_address
        with self.app.app_context():
            self.assertIsNone(find_network_for_address(''))

    def test_network_with_malformed_our_address_is_skipped_not_crashed(self):
        """A network row whose OWN our_address is garbage (bad data,
        botched migration, hand-edited DB) must be silently skipped
        during the zone-match scan, not crash the whole lookup for
        every other (well-formed) network."""
        from anetbbs.models import db, EchomailNetwork
        from anetbbs.echomail.routing import find_network_for_address
        with self.app.app_context():
            db.session.add(EchomailNetwork(
                name='BrokenAddrNet', network_type='binkp',
                our_address='garbage-not-an-address', is_active=True))
            good = EchomailNetwork(
                name='GoodNet', network_type='binkp',
                our_address='5:5/1', is_active=True)
            db.session.add(good)
            db.session.commit()

            result = find_network_for_address('5:5/2')
            self.assertEqual(result.name, 'GoodNet')


class ResolveNetmailRecipientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.routing_resolve_recipient_test.db')
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

    def test_empty_to_name_and_address_returns_none_not_a_crash(self):
        from anetbbs.models import db, EchomailNetwork
        from anetbbs.echomail.routing import resolve_netmail_recipient
        with self.app.app_context():
            net = EchomailNetwork(name='EmptyRecipientNet', network_type='binkp',
                                  our_address='8:8/1')
            db.session.add(net)
            db.session.commit()
            self.assertIsNone(resolve_netmail_recipient('', '', net))

    def test_garbage_to_address_with_no_matching_name_returns_none(self):
        from anetbbs.models import db, EchomailNetwork
        from anetbbs.echomail.routing import resolve_netmail_recipient
        with self.app.app_context():
            net = EchomailNetwork(name='GarbageAddrNet', network_type='binkp',
                                  our_address='8:9/1')
            db.session.add(net)
            db.session.commit()
            self.assertIsNone(resolve_netmail_recipient(
                'NoSuchUser', 'totally-garbage-address', net))

    def test_falls_back_to_default_recipient_when_configured(self):
        from anetbbs.models import db, EchomailNetwork, User
        from anetbbs.echomail.routing import resolve_netmail_recipient
        with self.app.app_context():
            fallback = User(username='catchall', email='catchall@example.com',
                           password_hash='x')
            db.session.add(fallback)
            net = EchomailNetwork(name='DefaultRecipientNet', network_type='binkp',
                                  our_address='8:10/1', default_recipient='catchall')
            db.session.add(net)
            db.session.commit()

            resolved = resolve_netmail_recipient('NoSuchUser', 'garbage', net)
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved.username, 'catchall')


if __name__ == '__main__':
    unittest.main()
