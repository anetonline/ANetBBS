"""Regression test for a real Medium-severity finding from a security/
performance audit (2026-08-31): anetbbs/echomail/poller.py's
_import_message() (the echomail import path) had NO fallback dedup
when msg_id is missing/empty -- unlike _import_netmail(), which
already has a content-based fallback (sender+subject+area within a
time window) for exactly this case, added after a real live incident
where a peer regenerated/omitted MSGID on resend and defeated the
exact-MSGID check (see test_netmail_content_dedup.py for that
incident's full writeup). A tosser that doesn't guarantee a MSGID
kludge on every echomail post would have the same message re-imported
on EVERY poll, forever -- echomail never got the equivalent fix.
"""
import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_DATA_DIR = Path(__file__).resolve().parents[1] / 'data'


def _snapshot_data_dir():
    if not _DATA_DIR.is_dir():
        return set()
    return set(_DATA_DIR.iterdir())


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


class EchomailContentDedupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._data_dir_before = _snapshot_data_dir()
        import anetbbs.config as cfg_mod
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._orig_flask_env = os.environ.get('FLASK_ENV')

    @classmethod
    def tearDownClass(cls):
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        if cls._orig_flask_env is None:
            os.environ.pop('FLASK_ENV', None)
        else:
            os.environ['FLASK_ENV'] = cls._orig_flask_env
        for entry in _snapshot_data_dir() - cls._data_dir_before:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _make_area(self, app, tag='TESTAREA'):
        from anetbbs.models import db, EchomailNetwork, EchoArea
        with app.app_context():
            net = EchomailNetwork(name='TestNet', network_type='binkp',
                                  our_address='1:1/1')
            db.session.add(net)
            db.session.commit()
            area = EchoArea(network_id=net.id, tag=tag, name=tag,
                            is_active=True, is_subscribed=True)
            db.session.add(area)
            db.session.commit()
            return net.id, area.id

    def test_missing_msgid_resend_is_deduped_by_content(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'echodup.db'))
        net_id, area_id = self._make_area(app)
        from anetbbs.models import EchomailNetwork, EchomailMessage
        from anetbbs.echomail.poller import _import_message
        with app.app_context():
            net = EchomailNetwork.query.get(net_id)
            first = {
                'area_tag': 'TESTAREA', 'from_name': 'Mystic',
                'to_name': 'All', 'subject': 'Weekly stats',
                'body': 'first body',
            }
            self.assertEqual(_import_message(net, first), 1)
            # Same sender/subject, no msg_id at all on either poll --
            # the exact real-world failure mode (a tosser that never
            # guarantees MSGID).
            second = dict(first, body='regenerated body text, different')
            self.assertEqual(_import_message(net, second), 0)
            self.assertEqual(EchomailMessage.query.count(), 1)

    def test_different_subject_is_not_deduped(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'echonodup.db'))
        net_id, area_id = self._make_area(app)
        from anetbbs.models import EchomailNetwork, EchomailMessage
        from anetbbs.echomail.poller import _import_message
        with app.app_context():
            net = EchomailNetwork.query.get(net_id)
            first = {
                'area_tag': 'TESTAREA', 'from_name': 'Mystic',
                'to_name': 'All', 'subject': 'Weekly stats', 'body': 'x',
            }
            second = {
                'area_tag': 'TESTAREA', 'from_name': 'Mystic',
                'to_name': 'All', 'subject': 'Monthly stats', 'body': 'y',
            }
            self.assertEqual(_import_message(net, first), 1)
            self.assertEqual(_import_message(net, second), 1)
            self.assertEqual(EchomailMessage.query.count(), 2)

    def test_real_msgid_present_still_uses_exact_match_not_content_fallback(self):
        """Confirms the new content-fallback only kicks in when msg_id
        is absent -- a legitimately different message that happens to
        share sender+subject, but carries a real distinct msg_id, must
        NOT be silently dropped as a false-positive dedup."""
        app = _fresh_app(str(Path(self._tmp.name) / 'echomsgid.db'))
        net_id, area_id = self._make_area(app)
        from anetbbs.models import EchomailNetwork, EchomailMessage
        from anetbbs.echomail.poller import _import_message
        with app.app_context():
            net = EchomailNetwork.query.get(net_id)
            first = {
                'area_tag': 'TESTAREA', 'from_name': 'Mystic',
                'to_name': 'All', 'subject': 'Weekly stats', 'body': 'x',
                'msg_id': '1:1/1 aaaa1111',
            }
            second = {
                'area_tag': 'TESTAREA', 'from_name': 'Mystic',
                'to_name': 'All', 'subject': 'Weekly stats', 'body': 'y',
                'msg_id': '1:1/1 bbbb2222',
            }
            self.assertEqual(_import_message(net, first), 1)
            self.assertEqual(_import_message(net, second), 1)
            self.assertEqual(EchomailMessage.query.count(), 2)


if __name__ == '__main__':
    unittest.main()
