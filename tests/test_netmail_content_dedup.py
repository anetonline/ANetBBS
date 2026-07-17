"""Regression tests for the content-based netmail dedup fallback in
anetbbs/echomail/poller.py:_import_netmail().

Real-world trigger: a live FTN peer (binkd) was observed resending the
same "Area Management Request" / "List of Available Areas" netmail on
every poll (~10 minute cadence), each time with a freshly regenerated
MSGID -- defeating the pre-existing exact-MSGID dedup check, which is
the *only* dedup that existed before this fix. Sender, subject, and body
were byte-identical across every resend. This fallback treats a new
inbound netmail as a duplicate if a message with matching
network+direction+from_name+from_address+subject+body already arrived
within the last _CONTENT_DEDUP_WINDOW_HOURS, even when MSGID doesn't
match anything on file (or doesn't match between the two copies).
"""
import os
import sys
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
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


class _BaseTestCase(unittest.TestCase):
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

    def _make_network(self, app, name='TestNet'):
        from anetbbs.models import db, EchomailNetwork
        with app.app_context():
            net = EchomailNetwork(name=name, network_type='binkp',
                                  our_address='1:1/1')
            db.session.add(net)
            db.session.commit()
            return net.id


class ContentDedupTests(_BaseTestCase):
    def test_same_content_different_msgid_is_deduped(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'contentdup.db'))
        net_id = self._make_network(app)
        from anetbbs.models import EchomailNetwork, NetmailMessage
        from anetbbs.echomail.poller import _import_netmail
        with app.app_context():
            net = EchomailNetwork.query.get(net_id)
            first = {
                'msg_id': 'aaaa-1111', 'to_name': 'sysop', 'to_address': '',
                'from_name': 'SBBSecho', 'from_address': '1:9/9',
                'subject': 'Area Management Request',
                'body': 'List of Available Areas\n...',
            }
            self.assertEqual(_import_netmail(net, first), 1)
            # Same sender/subject/body, but the peer regenerated MSGID --
            # this is the exact real-world failure mode.
            second = dict(first, msg_id='bbbb-2222')
            self.assertEqual(_import_netmail(net, second), 0)
            self.assertEqual(NetmailMessage.query.count(), 1)

    def test_different_body_is_not_deduped(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'diffbody.db'))
        net_id = self._make_network(app)
        from anetbbs.models import EchomailNetwork, NetmailMessage
        from anetbbs.echomail.poller import _import_netmail
        with app.app_context():
            net = EchomailNetwork.query.get(net_id)
            first = {
                'msg_id': 'aaaa-1111', 'to_name': 'sysop', 'to_address': '',
                'from_name': 'SBBSecho', 'from_address': '1:9/9',
                'subject': 'Area Management Request', 'body': 'first body',
            }
            second = {
                'msg_id': 'bbbb-2222', 'to_name': 'sysop', 'to_address': '',
                'from_name': 'SBBSecho', 'from_address': '1:9/9',
                'subject': 'Area Management Request', 'body': 'different body',
            }
            self.assertEqual(_import_netmail(net, first), 1)
            self.assertEqual(_import_netmail(net, second), 1)
            self.assertEqual(NetmailMessage.query.count(), 2)

    def test_different_sender_is_not_deduped(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'diffsender.db'))
        net_id = self._make_network(app)
        from anetbbs.models import EchomailNetwork, NetmailMessage
        from anetbbs.echomail.poller import _import_netmail
        with app.app_context():
            net = EchomailNetwork.query.get(net_id)
            first = {
                'msg_id': 'aaaa-1111', 'to_name': 'sysop', 'to_address': '',
                'from_name': 'SBBSecho', 'from_address': '1:9/9',
                'subject': 'Hello', 'body': 'same body',
            }
            second = {
                'msg_id': 'bbbb-2222', 'to_name': 'sysop', 'to_address': '',
                'from_name': 'SomeoneElse', 'from_address': '1:9/9',
                'subject': 'Hello', 'body': 'same body',
            }
            self.assertEqual(_import_netmail(net, first), 1)
            self.assertEqual(_import_netmail(net, second), 1)
            self.assertEqual(NetmailMessage.query.count(), 2)

    def test_different_network_is_not_deduped(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'diffnet.db'))
        net1_id = self._make_network(app, name='NetOne')
        net2_id = self._make_network(app, name='NetTwo')
        from anetbbs.models import EchomailNetwork, NetmailMessage
        from anetbbs.echomail.poller import _import_netmail
        with app.app_context():
            net1 = EchomailNetwork.query.get(net1_id)
            net2 = EchomailNetwork.query.get(net2_id)
            msg = {
                'msg_id': 'aaaa-1111', 'to_name': 'sysop', 'to_address': '',
                'from_name': 'SBBSecho', 'from_address': '1:9/9',
                'subject': 'Area Management Request', 'body': 'shared body',
            }
            self.assertEqual(_import_netmail(net1, dict(msg, msg_id='a1')), 1)
            self.assertEqual(_import_netmail(net2, dict(msg, msg_id='a2')), 1)
            self.assertEqual(NetmailMessage.query.count(), 2)

    def test_outbound_rows_do_not_count_as_dedup_matches(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'outbound.db'))
        net_id = self._make_network(app)
        from anetbbs.models import db, EchomailNetwork, NetmailMessage
        from anetbbs.echomail.poller import _import_netmail
        with app.app_context():
            net = EchomailNetwork.query.get(net_id)
            # Simulate a pre-existing OUTBOUND netmail with the exact same
            # sender/subject/body fields the dedup filter checks -- must
            # not suppress an inbound message that happens to match.
            db.session.add(NetmailMessage(
                network_id=net.id, from_name='SBBSecho', from_address='1:9/9',
                to_name='sysop', to_address='', subject='Area Management Request',
                body='shared body', direction='outbound', status='sent',
                created_at=datetime.utcnow(),
            ))
            db.session.commit()
            inbound = {
                'msg_id': 'aaaa-1111', 'to_name': 'sysop', 'to_address': '',
                'from_name': 'SBBSecho', 'from_address': '1:9/9',
                'subject': 'Area Management Request', 'body': 'shared body',
            }
            self.assertEqual(_import_netmail(net, inbound), 1)
            self.assertEqual(
                NetmailMessage.query.filter_by(direction='inbound').count(), 1)

    def test_stale_match_outside_window_is_not_deduped(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'stale.db'))
        net_id = self._make_network(app)
        from anetbbs.models import db, EchomailNetwork, NetmailMessage
        from anetbbs.echomail.poller import (
            _import_netmail, _CONTENT_DEDUP_WINDOW_HOURS,
        )
        with app.app_context():
            net = EchomailNetwork.query.get(net_id)
            first = {
                'msg_id': 'aaaa-1111', 'to_name': 'sysop', 'to_address': '',
                'from_name': 'SBBSecho', 'from_address': '1:9/9',
                'subject': 'Area Management Request', 'body': 'shared body',
            }
            self.assertEqual(_import_netmail(net, first), 1)
            db.session.commit()
            # Push the existing row's received_at back outside the dedup
            # window, simulating a genuinely new, unrelated resend long
            # after the original -- should NOT be treated as a duplicate.
            old_row = NetmailMessage.query.filter_by(msgid='aaaa-1111').first()
            old_row.received_at = (
                datetime.utcnow() - timedelta(hours=_CONTENT_DEDUP_WINDOW_HOURS + 1))
            db.session.commit()
            second = dict(first, msg_id='bbbb-2222')
            self.assertEqual(_import_netmail(net, second), 1)
            self.assertEqual(NetmailMessage.query.count(), 2)


if __name__ == '__main__':
    unittest.main()
