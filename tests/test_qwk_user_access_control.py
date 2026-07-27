"""Regression test: web/qwk_user.py's per-user QWK download/upload
bypassed all echo-area access control. _build_qwk_blob() and upload()
both queried EchoArea.query.filter_by(is_active=True).all() with no
gating at all, unlike every other echomail entry point in this codebase
(web/echomail.py's evaluate_access()/min_access_level/is_sysop_only
checks) -- any logged-in user's /qwk/download included messages from
sysop-only/restricted areas, and /qwk/upload let them post into those
same areas via REP import.

Fixed via a shared _qwk_accessible_areas(user) helper both routes now
use, applying the same evaluate_access() predicate as everywhere else.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class QwkUserAccessControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.qwk_user_access_control_test.db')
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

    def test_sysop_only_area_excluded_for_ordinary_user(self):
        from anetbbs.models import db, User, EchoArea, EchomailNetwork
        from anetbbs.web.qwk_user import _qwk_accessible_areas

        with self.app.app_context():
            net = EchomailNetwork(name='QwkAccessNet', network_type='qwk')
            db.session.add(net)
            db.session.commit()
            public_area = EchoArea(network_id=net.id, tag='PUBLIC.QWK',
                                   name='Public', is_active=True,
                                   min_access_level=0, is_sysop_only=False)
            sysop_area = EchoArea(network_id=net.id, tag='SYSOP.QWK',
                                  name='Sysop Only', is_active=True,
                                  min_access_level=0, is_sysop_only=True)
            db.session.add_all([public_area, sysop_area])
            db.session.commit()

            user = User(username='qwkordinary', email='qwkord@example.com',
                       password_hash='x', access_level=10, is_admin=False)
            db.session.add(user)
            db.session.commit()

            areas = _qwk_accessible_areas(user)
            tags = {a.tag for a in areas}
            self.assertIn('PUBLIC.QWK', tags)
            self.assertNotIn('SYSOP.QWK', tags,
                             'a sysop-only area must not be reachable via QWK '
                             'for an ordinary user')

    def test_high_min_access_level_area_excluded_below_threshold(self):
        from anetbbs.models import db, User, EchoArea, EchomailNetwork
        from anetbbs.web.qwk_user import _qwk_accessible_areas

        with self.app.app_context():
            net = EchomailNetwork(name='QwkAccessLevelNet', network_type='qwk')
            db.session.add(net)
            db.session.commit()
            restricted = EchoArea(network_id=net.id, tag='ELEVATED.QWK',
                                  name='Elevated', is_active=True,
                                  min_access_level=50, is_sysop_only=False)
            db.session.add(restricted)
            db.session.commit()

            low_user = User(username='qwklowlevel', email='qwklow@example.com',
                            password_hash='x', access_level=10, is_admin=False)
            db.session.add(low_user)
            db.session.commit()

            areas = _qwk_accessible_areas(low_user)
            self.assertNotIn('ELEVATED.QWK', {a.tag for a in areas})

    def test_admin_sees_everything(self):
        from anetbbs.models import db, User, EchoArea, EchomailNetwork
        from anetbbs.web.qwk_user import _qwk_accessible_areas

        with self.app.app_context():
            net = EchomailNetwork(name='QwkAdminNet', network_type='qwk')
            db.session.add(net)
            db.session.commit()
            sysop_area = EchoArea(network_id=net.id, tag='ADMINVIEW.QWK',
                                  name='Sysop Only', is_active=True,
                                  is_sysop_only=True)
            db.session.add(sysop_area)
            db.session.commit()

            admin = User(username='qwkadmin', email='qwkadmin@example.com',
                        password_hash='x', is_admin=True)
            db.session.add(admin)
            db.session.commit()

            areas = _qwk_accessible_areas(admin)
            self.assertIn('ADMINVIEW.QWK', {a.tag for a in areas})


if __name__ == '__main__':
    unittest.main()
