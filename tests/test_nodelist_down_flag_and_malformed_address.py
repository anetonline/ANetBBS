"""Regression tests for two real gaps found in a full echomail-
subsystem audit of anetbbs/echomail/nodelist.py's generate_nodelist():

1. No Down/Hold concept at all -- a node that hasn't been seen in a
   long time (or never since being registered) was indistinguishable
   in the generated nodelist from one polled five minutes ago. Fixed
   with FTS-5000's Down keyword-flag, based on BinkPNode.last_seen_at
   staleness (_DOWN_AFTER_DAYS).

2. A node whose ftn_address doesn't parse as expected was silently
   dropped from the generated file with no log line at all. Fixed by
   logging a warning before skipping it.
"""
import logging
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class NodelistDownFlagTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.nodelist_down_flag_test.db')
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

    def test_recently_seen_node_has_no_down_flag(self):
        from datetime import datetime
        from anetbbs.models import db, BinkPNode
        from anetbbs.echomail.nodelist import generate_nodelist

        with self.app.app_context():
            db.session.add(BinkPNode(
                name='FreshNode', ftn_address='5000:1/11', password='x',
                is_active=True, last_seen_at=datetime.utcnow()))
            db.session.commit()
            content = generate_nodelist(5000, 1, 1, 'TestHub', 'Loc', 'Sysop')

        node_line = next(l for l in content.splitlines() if 'FreshNode' in l)
        self.assertNotIn('Down', node_line)

    def test_stale_node_gets_down_flag(self):
        from datetime import datetime, timedelta
        from anetbbs.models import db, BinkPNode
        from anetbbs.echomail.nodelist import generate_nodelist

        with self.app.app_context():
            db.session.add(BinkPNode(
                name='StaleNode', ftn_address='5001:1/11', password='x',
                is_active=True,
                last_seen_at=datetime.utcnow() - timedelta(days=30)))
            db.session.commit()
            content = generate_nodelist(5001, 1, 1, 'TestHub', 'Loc', 'Sysop')

        node_line = next(l for l in content.splitlines() if 'StaleNode' in l)
        self.assertIn('Down', node_line)

    def test_never_seen_node_gets_down_flag(self):
        """last_seen_at IS NULL (registered but never actually
        connected) must also read as stale, not accidentally treated
        as freshly seen."""
        from anetbbs.models import db, BinkPNode
        from anetbbs.echomail.nodelist import generate_nodelist

        with self.app.app_context():
            db.session.add(BinkPNode(
                name='NeverSeenNode', ftn_address='5002:1/11', password='x',
                is_active=True, last_seen_at=None))
            db.session.commit()
            content = generate_nodelist(5002, 1, 1, 'TestHub', 'Loc', 'Sysop')

        node_line = next(l for l in content.splitlines() if 'NeverSeenNode' in l)
        self.assertIn('Down', node_line)

    def test_down_flag_is_positioned_as_a_keyword_field(self):
        """FTS-5000 keyword-flags are a field in the comma-separated
        entry, not embedded inside another field's value."""
        from datetime import datetime, timedelta
        from anetbbs.models import db, BinkPNode
        from anetbbs.echomail.nodelist import generate_nodelist

        with self.app.app_context():
            db.session.add(BinkPNode(
                name='FieldCheckNode', ftn_address='5003:1/11', password='x',
                is_active=True,
                last_seen_at=datetime.utcnow() - timedelta(days=30)))
            db.session.commit()
            content = generate_nodelist(5003, 1, 1, 'TestHub', 'Loc', 'Sysop')

        node_line = next(l for l in content.splitlines() if 'FieldCheckNode' in l)
        fields = node_line.split(',')
        self.assertIn('Down', fields)


class NodelistMalformedAddressLoggingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.nodelist_malformed_addr_test.db')
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

    def test_malformed_address_is_skipped_and_logged(self):
        from anetbbs.models import db, BinkPNode
        from anetbbs.echomail import nodelist as nodelist_mod

        with self.app.app_context():
            db.session.add(BinkPNode(
                name='BrokenAddrNode', ftn_address='not-a-real-address',
                password='x', is_active=True))
            db.session.commit()

            with self.assertLogs(nodelist_mod.logger, level='WARNING') as log_ctx:
                content = nodelist_mod.generate_nodelist(
                    5004, 1, 1, 'TestHub', 'Loc', 'Sysop')

        self.assertNotIn('BrokenAddrNode', content,
                         'a node with an unparseable address must still be '
                         'omitted from the actual nodelist output')
        self.assertTrue(
            any('BrokenAddrNode' in msg for msg in log_ctx.output),
            'skipping a node due to a malformed address must be logged, '
            'not silent')


if __name__ == '__main__':
    unittest.main()
