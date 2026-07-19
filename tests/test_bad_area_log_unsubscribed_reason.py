"""Regression test: echomail dropped because a locally-known area is
unsubscribed/inactive used to vanish with only a logger.debug() line --
no persisted record at all, unlike the 'unknown area' case (BadAreaLog +
admin notification). Reported live: a sysop's QWK network (DOVE-Net)
showed "2 dropped (loop/unknown/unsub)" in the poll log with no way to
tell which of the three reasons actually applied, or which area was
affected. Root-caused: for QWK networks specifically, an unrecognized
tag can NEVER be the cause (unknown areas auto-create for QWK -- see
_import_message), so by elimination the drop must be the
is_subscribed/is_active check, which previously had zero visibility.

BadAreaLog now has a `reason` column ('unknown' | 'unsubscribed') so both
cases share the same sysop review queue (/admin/echomail/bad_areas),
each upserted/re-classified by anetbbs.echomail.poller._record_bad_area().
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class BadAreaLogUnsubscribedReasonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.bad_area_reason_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_qwk_message_for_unsubscribed_area_is_recorded_with_reason(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea, BadAreaLog
        from anetbbs.echomail.poller import _import_message

        with self.app.app_context():
            net = EchomailNetwork(name='DOVE-Net-Test', network_type='qwk')
            db.session.add(net)
            db.session.commit()
            # Area exists locally but the sysop isn't (or no longer is)
            # subscribed -- exactly the DOVE-Net scenario.
            area = EchoArea(network_id=net.id, tag='2010',
                            name='Some Conference',
                            is_subscribed=False, is_active=True)
            db.session.add(area)
            db.session.commit()

            rc = _import_message(net, {
                'area_tag': '2010',
                'from_name': 'Someone',
                'subject': 'Test message',
                'body': 'body text',
                'msg_id': '<msg1@test>',
            })
            self.assertEqual(rc, -1, 'message for an unsubscribed area must be dropped')

            row = BadAreaLog.query.filter_by(network_id=net.id, tag='2010').first()
            self.assertIsNotNone(row,
                                 'a BadAreaLog row must now be recorded for '
                                 'unsubscribed-area drops, not just unknown-area ones')
            self.assertEqual(row.reason, 'unsubscribed')
            self.assertEqual(row.count, 1)

    def test_repeat_drops_increment_count_not_duplicate_rows(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea, BadAreaLog
        from anetbbs.echomail.poller import _import_message

        with self.app.app_context():
            net = EchomailNetwork(name='DOVE-Net-Test2', network_type='qwk')
            db.session.add(net)
            db.session.commit()
            area = EchoArea(network_id=net.id, tag='2020', name='Another Conf',
                            is_subscribed=False, is_active=True)
            db.session.add(area)
            db.session.commit()

            for i in range(3):
                _import_message(net, {
                    'area_tag': '2020', 'from_name': f'Someone{i}',
                    'subject': f'Test {i}', 'body': 'x',
                    'msg_id': f'<msg{i}@test>',
                })

            rows = BadAreaLog.query.filter_by(network_id=net.id, tag='2020').all()
            self.assertEqual(len(rows), 1, 'repeat drops for the same tag must '
                                           'upsert one row, not create duplicates')
            self.assertEqual(rows[0].count, 3)
            self.assertEqual(rows[0].reason, 'unsubscribed')

    def test_binkp_unknown_area_still_recorded_as_reason_unknown(self):
        """Guards the pre-existing 'unknown area' path (BinkP/FTN, where
        unknown tags do NOT auto-create) still tags its BadAreaLog rows
        with the new reason column correctly, not left at some blank/
        unexpected default."""
        from anetbbs.models import db, EchomailNetwork, BadAreaLog
        from anetbbs.echomail.poller import _import_message

        with self.app.app_context():
            net = EchomailNetwork(name='BinkpTestNet', network_type='binkp')
            db.session.add(net)
            db.session.commit()

            rc = _import_message(net, {
                'area_tag': 'SOME.UNKNOWN.TAG',
                'from_name': 'Someone',
                'subject': 'Test message',
                'body': 'body text',
                'msg_id': '<msg2@test>',
            })
            self.assertEqual(rc, -1)

            row = BadAreaLog.query.filter_by(
                network_id=net.id, tag='SOME.UNKNOWN.TAG').first()
            self.assertIsNotNone(row)
            self.assertEqual(row.reason, 'unknown')

    def test_qwk_never_hits_unknown_reason_since_areas_auto_create(self):
        """Confirms the reasoning used to diagnose Jerry's DOVE-Net report:
        for QWK networks, an unrecognized tag auto-creates the area rather
        than dropping -- so no BadAreaLog row with reason='unknown' can
        ever be produced for a QWK network via that path."""
        from anetbbs.models import db, EchomailNetwork, EchoArea, BadAreaLog
        from anetbbs.echomail.poller import _import_message

        with self.app.app_context():
            net = EchomailNetwork(name='QwkAutoCreateNet', network_type='qwk')
            db.session.add(net)
            db.session.commit()

            rc = _import_message(net, {
                'area_tag': '9999',
                'from_name': 'Someone',
                'subject': 'Test message',
                'body': 'body text',
                'msg_id': '<msg3@test>',
            })
            self.assertEqual(rc, 1, 'QWK areas auto-create -- the message imports')

            area = EchoArea.query.filter_by(network_id=net.id, tag='9999').first()
            self.assertIsNotNone(area)
            self.assertTrue(area.is_subscribed)

            self.assertIsNone(
                BadAreaLog.query.filter_by(network_id=net.id, tag='9999').first(),
                'no BadAreaLog row should be created when the area auto-creates')


if __name__ == '__main__':
    unittest.main()
