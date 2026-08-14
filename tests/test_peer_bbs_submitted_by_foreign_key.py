"""Regression test: PeerBbs.submitted_by_user_id (anetbbs/models.py)
was a bare Integer column with no db.ForeignKey('users.id') reference
-- every other *_user_id column in this file has one. Found in a
security/performance audit. Fixed by adding the missing ForeignKey
(nullable, matching from_user_id/to_user_id's precedent -- a peer can
be seeded/added directly by an admin with no submitting user).
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


def _fresh_app(db_path):
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    os.environ['FLASK_ENV'] = 'testing'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    return app


class PeerBbsSubmittedByForeignKeyTests(unittest.TestCase):
    def setUp(self):
        self._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        self.addCleanup(
            lambda: setattr(cfg_mod.TestingConfig,
                            'SQLALCHEMY_DATABASE_URI', self._orig_db_uri))
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'peerbbs_fk_test.db'))

    def test_schema_has_a_real_foreign_key_constraint(self):
        import sqlalchemy as sa
        from anetbbs.models import db
        with self.app.app_context():
            db.create_all()
            insp = sa.inspect(db.engine)
            fks = insp.get_foreign_keys('peer_bbses')
            matching = [
                fk for fk in fks
                if fk['referred_table'] == 'users'
                and 'submitted_by_user_id' in fk['constrained_columns']
            ]
            self.assertEqual(len(matching), 1,
                             'peer_bbses.submitted_by_user_id must have a '
                             'real FK constraint referencing users.id')

    def test_can_still_create_a_peer_with_no_submitting_user(self):
        """Guard against an over-eager fix that makes the column
        non-nullable -- an admin-seeded peer legitimately has no
        submitting user."""
        from anetbbs.models import db, PeerBbs
        with self.app.app_context():
            db.create_all()
            p = PeerBbs(name='Seeded BBS', hostname='seeded.example.com')
            db.session.add(p)
            db.session.commit()
            self.assertIsNone(p.submitted_by_user_id)

    def test_create_a_peer_with_a_real_submitting_user(self):
        from anetbbs.models import db, PeerBbs, User
        with self.app.app_context():
            db.create_all()
            u = User(username='peerbbs_submitter',
                    email='peerbbs_submitter@example.com',
                    password_hash='x')
            db.session.add(u)
            db.session.commit()
            p = PeerBbs(name='User Submitted BBS',
                       hostname='usersubmitted.example.com',
                       submitted_by_user_id=u.id)
            db.session.add(p)
            db.session.commit()
            self.assertEqual(p.submitted_by_user_id, u.id)


if __name__ == '__main__':
    unittest.main()
