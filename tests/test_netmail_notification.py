"""Regression tests for inbound netmail notification (Jerry: "new netmail
doesn't show a notification"). Two structural gaps existed before this fix:

1. Neither inbound netmail import path (poller.py's _import_netmail, used
   by the QWK/poll-response path; binkp_server.py's _import_pkt_payload,
   used by the real-time BinkP listener) ever called notify() -- so no
   Notification row was ever created for netmail, unlike PMs and InterBBS
   IMs, and the in-app bell / terminal "You have new" banner (both of
   which read the generic Notification table) never lit up for it.
2. The BinkP listener path additionally never resolved to_user_id at all
   (it was simply absent from the NetmailMessage(...) kwargs), so even a
   naive "notify if to_user_id is set" fix would have silently done
   nothing for BinkP-received netmail specifically.

Both paths now share one recipient-resolution helper,
anetbbs/echomail/routing.py:resolve_netmail_recipient() -- this file tests
that helper directly (DB-level, real queries) plus the notify() wiring in
poller.py's _import_netmail. binkp_server.py's _import_pkt_payload isn't
covered here (it requires constructing a real FTS-0001 packet, which
_build_ftn_packet doesn't support for netmail-shaped messages) but shares
the exact same resolve_netmail_recipient() call, so this coverage extends
to it too.
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


class ResolveNetmailRecipientTests(_BaseTestCase):
    def _make_network(self, app, default_recipient=None):
        from anetbbs.models import db, EchomailNetwork
        with app.app_context():
            net = EchomailNetwork(name='TestNet', network_type='binkp',
                                  our_address='1:1/1',
                                  default_recipient=default_recipient)
            db.session.add(net)
            db.session.commit()
            return net.id

    def test_matches_by_aka_address(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'aka.db'))
        net_id = self._make_network(app)
        from anetbbs.models import db, User, UserAka, EchomailNetwork
        from anetbbs.echomail.routing import resolve_netmail_recipient
        with app.app_context():
            u = User(username='alice', email='alice@example.com', password_hash='x')
            db.session.add(u)
            db.session.commit()
            db.session.add(UserAka(user_id=u.id, address='1:1/2'))
            db.session.commit()

            net = EchomailNetwork.query.get(net_id)
            found = resolve_netmail_recipient('someone else', '1:1/2', net)
            self.assertIsNotNone(found)
            self.assertEqual(found.id, u.id)

    def test_matches_by_username_when_no_aka(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'uname.db'))
        net_id = self._make_network(app)
        from anetbbs.models import db, User, EchomailNetwork
        from anetbbs.echomail.routing import resolve_netmail_recipient
        with app.app_context():
            u = User(username='Bob', email='bob@example.com', password_hash='x')
            db.session.add(u)
            db.session.commit()
            net = EchomailNetwork.query.get(net_id)
            # Case-insensitive, and to_address doesn't match any AKA.
            found = resolve_netmail_recipient('bob', '1:1/99', net)
            self.assertIsNotNone(found)
            self.assertEqual(found.id, u.id)

    def test_matches_by_display_name(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'dname.db'))
        net_id = self._make_network(app)
        from anetbbs.models import db, User, EchomailNetwork
        from anetbbs.echomail.routing import resolve_netmail_recipient
        with app.app_context():
            u = User(username='carol99', display_name='Carol Danvers',
                     email='carol@example.com', password_hash='x')
            db.session.add(u)
            db.session.commit()
            net = EchomailNetwork.query.get(net_id)
            found = resolve_netmail_recipient('Carol Danvers', '', net)
            self.assertIsNotNone(found)
            self.assertEqual(found.id, u.id)

    def test_falls_back_to_default_recipient(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'default.db'))
        net_id = self._make_network(app, default_recipient='sysop')
        from anetbbs.models import db, User, EchomailNetwork
        from anetbbs.echomail.routing import resolve_netmail_recipient
        with app.app_context():
            u = User(username='sysop', email='sysop@example.com', password_hash='x')
            db.session.add(u)
            db.session.commit()
            net = EchomailNetwork.query.get(net_id)
            # Nothing matches "unknownperson" -- should fall back.
            found = resolve_netmail_recipient('unknownperson', '', net)
            self.assertIsNotNone(found)
            self.assertEqual(found.id, u.id)

    def test_returns_none_when_nothing_matches(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'none.db'))
        net_id = self._make_network(app)  # no default_recipient
        from anetbbs.models import EchomailNetwork
        from anetbbs.echomail.routing import resolve_netmail_recipient
        with app.app_context():
            net = EchomailNetwork.query.get(net_id)
            self.assertIsNone(resolve_netmail_recipient('nobody', '9:9/9', net))


class ImportNetmailNotificationTests(_BaseTestCase):
    def _make_network(self, app):
        from anetbbs.models import db, EchomailNetwork
        with app.app_context():
            net = EchomailNetwork(name='TestNet', network_type='binkp',
                                  our_address='1:1/1')
            db.session.add(net)
            db.session.commit()
            return net.id

    def test_notification_created_when_recipient_resolves(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'notify.db'))
        net_id = self._make_network(app)
        from anetbbs.models import db, User, EchomailNetwork, Notification, NetmailMessage
        from anetbbs.echomail.poller import _import_netmail
        with app.app_context():
            u = User(username='dave', email='dave@example.com', password_hash='x')
            db.session.add(u)
            db.session.commit()

            net = EchomailNetwork.query.get(net_id)
            rc = _import_netmail(net, {
                'msg_id': 'abc123', 'to_name': 'dave', 'to_address': '',
                'from_name': 'Erin', 'from_address': '1:1/9',
                'subject': 'Hello there', 'body': 'test message',
            })
            db.session.commit()
            self.assertEqual(rc, 1)

            nm = NetmailMessage.query.filter_by(msgid='abc123').first()
            self.assertIsNotNone(nm)
            self.assertEqual(nm.to_user_id, u.id)

            notif = Notification.query.filter_by(user_id=u.id, kind='netmail').first()
            self.assertIsNotNone(notif, 'expected a Notification row for the resolved recipient')
            self.assertIn('Erin', notif.title)
            self.assertEqual(notif.target_url, f'/netmail/{nm.id}')
            self.assertFalse(notif.is_read)

    def test_no_notification_when_recipient_unresolved(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'nonotify.db'))
        net_id = self._make_network(app)
        from anetbbs.models import EchomailNetwork, Notification
        from anetbbs.echomail.poller import _import_netmail
        with app.app_context():
            net = EchomailNetwork.query.get(net_id)
            rc = _import_netmail(net, {
                'msg_id': 'xyz789', 'to_name': 'nosuchuser', 'to_address': '',
                'from_name': 'Frank', 'from_address': '1:1/9',
                'subject': 'Hi', 'body': 'test',
            })
            self.assertEqual(rc, 1)
            self.assertEqual(Notification.query.count(), 0)

    def test_duplicate_msg_id_skipped(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'dup.db'))
        net_id = self._make_network(app)
        from anetbbs.models import db, User, EchomailNetwork, Notification
        from anetbbs.echomail.poller import _import_netmail
        with app.app_context():
            u = User(username='gina', email='gina@example.com', password_hash='x')
            db.session.add(u)
            db.session.commit()
            net = EchomailNetwork.query.get(net_id)
            msg = {'msg_id': 'dup1', 'to_name': 'gina', 'to_address': '',
                   'from_name': 'Hank', 'from_address': '1:1/9',
                   'subject': 'First', 'body': 'test'}
            self.assertEqual(_import_netmail(net, msg), 1)
            db.session.commit()
            self.assertEqual(_import_netmail(net, msg), 0)
            db.session.commit()
            # Only one notification, not two, for the re-delivered duplicate.
            self.assertEqual(
                Notification.query.filter_by(user_id=u.id, kind='netmail').count(), 1)


if __name__ == '__main__':
    unittest.main()
