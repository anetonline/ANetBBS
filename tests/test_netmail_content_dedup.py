"""Regression tests for the content-based netmail dedup fallback in
anetbbs/echomail/poller.py:_import_netmail().

Real-world trigger: a live FTN peer (SBBSecho, transported over binkd)
was observed resending the same "Area Management Request" / "List of
Available Areas" netmail on every poll (~10 minute cadence), each time
with a freshly regenerated MSGID -- defeating the pre-existing
exact-MSGID dedup check, which is the *only* dedup that existed before
this fix.

v1.0b2.143 shipped a first version of this fallback matching on
network+direction+from_name+from_address+subject+body. Confirmed live
that this was NOT sufficient -- the flood continued creating a new row
every ~10 minutes even with that fix deployed, proving the body isn't
actually byte-identical across regenerations (most likely a generated
timestamp embedded in the message text itself). v1.0b2.145 drops the
body comparison: sender+subject+network within the dedup window is
already a strong-enough signal for automated administrative netmail
like this, and is cheaper (no TEXT-column comparison, which matters
under eventlet -- see the received_at index added in the same release).
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

    def test_different_body_same_sender_subject_is_still_deduped(self):
        """The actual real-world fix: confirmed live that SBBSecho's
        resent "Area Management Request"/"List of Available Areas"
        netmail was STILL creating a new row every ~10 minutes even with
        the original (body-inclusive) version of this dedup fallback --
        proving the body isn't byte-identical across regenerations (most
        likely a generated timestamp embedded in the text itself, not
        just the MSGID kludge). The fix drops the body comparison
        entirely: sender+subject+network within the window is enough for
        automated administrative netmail like this, and it's also
        cheaper (no TEXT-column comparison)."""
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
            self.assertEqual(_import_netmail(net, second), 0)
            self.assertEqual(NetmailMessage.query.count(), 1)

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

    def test_areafix_netmail_with_repeated_subject_is_not_deduped(self):
        """Real live bug: a downstream node resent an AreaFix "%help"
        command to us with the SAME subject line its client had already
        used for an earlier, unrelated test message from the same
        address within the dedup window. The content-based dedup
        fallback silently swallowed the AreaFix command -- zero error,
        just "Imported 0 messages" -- because it treated a robot
        command the same as an informational broadcast. AreaFix/FileFix
        netmail must always import and dispatch, regardless of subject
        reuse; only the exact-MSGID dedup should ever apply to it."""
        app = _fresh_app(str(Path(self._tmp.name) / 'areafix_dedup.db'))
        net_id = self._make_network(app)
        from anetbbs.models import EchomailNetwork, NetmailMessage
        from anetbbs.echomail.poller import _import_netmail
        with app.app_context():
            net = EchomailNetwork.query.get(net_id)
            first = {
                'msg_id': 'aaaa-1111', 'to_name': 'areafix',
                'to_address': '1200:1/1',
                'from_name': 'Craig Hendricks', 'from_address': '1200:1/4',
                'subject': 'Whyf6ou8N45LQvNc', 'body': 'first request',
            }
            second = {
                'msg_id': 'bbbb-2222', 'to_name': 'areafix',
                'to_address': '1200:1/1',
                'from_name': 'Craig Hendricks', 'from_address': '1200:1/4',
                'subject': 'Whyf6ou8N45LQvNc', 'body': '%help',
            }
            self.assertEqual(_import_netmail(net, first), 1)
            self.assertEqual(_import_netmail(net, second), 1,
                            'a robot-addressed netmail must import even '
                            'when sender+subject+network match an earlier '
                            'unrelated message -- it is a distinct command')
            # Both inbound commands must be stored -- the AreaFix bot also
            # fires for each one (dispatched from within _import_netmail())
            # and sends its own outbound reply netmail, so the inbound
            # count is the meaningful assertion here, not the raw total.
            self.assertEqual(
                NetmailMessage.query.filter_by(direction='inbound').count(), 2)

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
