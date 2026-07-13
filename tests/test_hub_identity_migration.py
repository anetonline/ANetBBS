"""Tests for multi-hub-identity support's schema/migration plumbing
(anetbbs/models.py's HubIdentity + hub_identity_id columns, and
anetbbs/web_app.py's _seed_default_hub_identity()).

Phase 0 of the multi-hub-identity feature: adds a real HubIdentity
relationship in place of the old singleton assumptions (one
REGISTRY_MODE_ENABLED boolean, hardcoded zone=1200/net=1/hub_node=1,
one global QWK_HUB_ID, one singleton NetworkJoinConfig row). This file
only covers the schema/migration layer -- nothing downstream (BinkP
listener auth, QWK hub, nodelist generation, admin UI) reads
hub_identity_id yet, so an install with a single hub identity (the
common case) must behave identically to before this change.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class HubIdentityMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.hub_identity_migration_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'
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

    def test_exactly_one_default_identity_after_boot(self):
        """create_app() already ran the seed logic once in setUpClass
        via _lightweight_migrate(). Confirm it left exactly one row."""
        from anetbbs.models import HubIdentity
        with self.app.app_context():
            defaults = HubIdentity.query.filter_by(is_default=True).all()
            self.assertEqual(len(defaults), 1)
            self.assertEqual(defaults[0].name, 'ANotherNetwork')
            self.assertEqual(defaults[0].binkp_zone, 1200)
            self.assertFalse(
                defaults[0].is_active,
                'seeded default identity must start inactive -- turning on '
                'REGISTRY_MODE_ENABLED alone must not make ANotherNetwork '
                'look like a live, configured hub. Matches the seeded '
                'ANotherNetwork EchomailNetwork rows, which also start '
                'is_active=False until the sysop configures real credentials.')
            self.assertEqual(defaults[0].binkp_net, 1)
            self.assertEqual(defaults[0].binkp_hub_node, 1)

    def test_migration_is_idempotent_no_duplicate_default(self):
        """Running the seed/backfill pass again (as happens on every app
        boot in production) must not create a second default row."""
        from anetbbs.models import db, HubIdentity
        from anetbbs.web_app import _lightweight_migrate
        with self.app.app_context():
            before_id = HubIdentity.query.filter_by(is_default=True).one().id
            _lightweight_migrate(self.app)
            _lightweight_migrate(self.app)
            defaults = HubIdentity.query.filter_by(is_default=True).all()
            self.assertEqual(len(defaults), 1)
            self.assertEqual(defaults[0].id, before_id)

    def test_new_echomail_network_defaults_to_default_identity(self):
        """Every existing call site that builds an EchomailNetwork
        without passing hub_identity_id (admin routes, seed data,
        other tests) must keep working -- the Python-side column
        default should resolve it automatically."""
        from anetbbs.models import db, EchomailNetwork, HubIdentity
        with self.app.app_context():
            default_id = HubIdentity.query.filter_by(is_default=True).one().id
            net = EchomailNetwork(name='Plain Old Network', network_type='binkp')
            db.session.add(net)
            db.session.commit()
            self.assertEqual(net.hub_identity_id, default_id)

    def test_new_binkp_node_defaults_to_default_identity(self):
        from anetbbs.models import db, BinkPNode, HubIdentity
        with self.app.app_context():
            default_id = HubIdentity.query.filter_by(is_default=True).one().id
            node = BinkPNode(name='Test Node', ftn_address='1:9999/1', password='x')
            db.session.add(node)
            db.session.commit()
            self.assertEqual(node.hub_identity_id, default_id)

    def test_new_qwk_node_defaults_to_default_identity(self):
        from anetbbs.models import db, QWKNode, HubIdentity
        with self.app.app_context():
            default_id = HubIdentity.query.filter_by(is_default=True).one().id
            node = QWKNode(packet_id='TESTQ2', name='Test QWK Node', password='x')
            db.session.add(node)
            db.session.commit()
            self.assertEqual(node.hub_identity_id, default_id)

    def test_network_join_config_get_zero_arg_still_works(self):
        """NetworkJoinConfig.get() with no arguments must keep resolving
        to the default identity's row -- this is the call signature
        every pre-multi-hub-identity call site (including many tests)
        already uses."""
        from anetbbs.models import NetworkJoinConfig, HubIdentity
        with self.app.app_context():
            default_id = HubIdentity.query.filter_by(is_default=True).one().id
            cfg = NetworkJoinConfig.get()
            self.assertEqual(cfg.hub_identity_id, default_id)
            # Calling again must return the same row, not create a new one.
            cfg2 = NetworkJoinConfig.get()
            self.assertEqual(cfg.id, cfg2.id)

    def test_backfill_covers_rows_created_before_column_existed(self):
        """Simulates an upgrading install: a row that predates the
        hub_identity_id column (literal NULL, as a bare ALTER TABLE ADD
        COLUMN would leave it) must get backfilled to the default
        identity the next time the migration runs -- not just newly
        constructed rows, which the Python-side default already covers
        on its own."""
        from anetbbs.models import db, BinkPNode, HubIdentity
        from anetbbs.web_app import _lightweight_migrate
        with self.app.app_context():
            default_id = HubIdentity.query.filter_by(is_default=True).one().id
            node = BinkPNode(name='Legacy Node', ftn_address='1:9999/2', password='x')
            db.session.add(node)
            db.session.commit()
            # Force it back to NULL, simulating a pre-migration row.
            db.session.execute(db.text(
                'UPDATE binkp_nodes SET hub_identity_id = NULL WHERE id = :id'),
                {'id': node.id})
            db.session.commit()
            db.session.refresh(node)
            self.assertIsNone(node.hub_identity_id)

            _lightweight_migrate(self.app)

            db.session.refresh(node)
            self.assertEqual(node.hub_identity_id, default_id)


if __name__ == '__main__':
    unittest.main()
