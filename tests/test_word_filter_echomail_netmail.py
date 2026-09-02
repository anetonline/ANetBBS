"""Regression test for a real Medium finding from a security/
performance audit (2026-09-02): the sysop-configured word-filter
blocklist (WordFilter rows, applied via features/word_filter.apply())
is applied consistently to boards (web/boards.py), PMs, oneliners, and
shoutbox, but was never applied to echomail or netmail composition
anywhere -- web or terminal. Since echomail/netmail can go out to the
entire FTN network (potentially many external BBSes over BinkP), this
is a wider-blast-radius gap than the surfaces already covered.

Fixed at all 5 local-compose call sites: web/echomail.py's compose()
and netmail_compose() (QWK), web/netmail.py's compose() (FTN), and the
two terminal composers (features/bbs_ui.py, features/petscii_ui.py).
This file drives the three WEB routes end-to-end (client.post) and
confirms the terminal composers structurally -- the fix is an
identical few-line pattern copied to each site (same reasoning
test_ansi_subject_tag.py's own docstring already documents for that
sibling multi-site fix: "the risk here is a wiring typo, not
per-surface logic divergence").
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class WordFilterEchomailNetmailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.word_filter_echomail_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def setUp(self):
        from anetbbs.features import word_filter as wf_mod
        from anetbbs.models import db, WordFilter
        wf_mod.invalidate()
        self.addCleanup(wf_mod.invalidate)
        with self.app.app_context():
            WordFilter.query.delete()
            db.session.commit()

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def _make_user(self, username):
        from anetbbs.models import db, User
        with self.app.app_context():
            user = User(username=username, email=f'{username}@example.com',
                       password_hash='x', access_level=100, is_admin=True)
            db.session.add(user)
            db.session.commit()
            return user.id

    def _seed_filter(self, pattern='badword'):
        from anetbbs.models import db, WordFilter
        with self.app.app_context():
            db.session.add(WordFilter(pattern=pattern, replacement='****',
                                      is_active=True))
            db.session.commit()

    def test_echomail_compose_applies_word_filter(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea, EchomailMessage
        self._seed_filter('badword')
        user_id = self._make_user('wfechouser')
        with self.app.app_context():
            net = EchomailNetwork(name='WFEchoNet', network_type='binkp',
                                  is_active=True, our_address='9:9/1')
            db.session.add(net)
            db.session.flush()
            area = EchoArea(network_id=net.id, tag='WF.ECHO', name='WF Echo Test',
                            is_active=True, is_subscribed=True,
                            is_sysop_only=False, min_access_level=10)
            db.session.add(area)
            db.session.commit()
            area_id = area.id

        client = self._client_as(user_id)
        resp = client.post(f'/echomail/{area_id}/compose', data={
            'area_id': str(area_id), 'to_name': 'All',
            'subject': 'a badword in the subject',
            'body': 'body text with a badword in it too',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            msg = EchomailMessage.query.filter_by(area_id=area_id).first()
            self.assertIsNotNone(msg)
            self.assertNotIn('badword', msg.subject)
            self.assertNotIn('badword', msg.body)
            self.assertIn('****', msg.subject)
            self.assertIn('****', msg.body)

    def test_echomail_netmail_compose_qwk_applies_word_filter(self):
        from anetbbs.models import db, EchomailNetwork, EchomailMessage
        self._seed_filter('badword')
        user_id = self._make_user('wfqwkuser')
        with self.app.app_context():
            net = EchomailNetwork(name='WFQwkNet', network_type='qwk',
                                  is_active=True, our_address='9:9/2')
            db.session.add(net)
            db.session.commit()
            net_id = net.id

        client = self._client_as(user_id)
        resp = client.post('/echomail/netmail/compose', data={
            'network_id': str(net_id),
            'to_name': 'Someone', 'to_address': '',
            'subject': 'a badword here', 'body': 'and a badword here too',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            msg = EchomailMessage.query.filter_by(network_id=net_id).first()
            self.assertIsNotNone(msg)
            self.assertNotIn('badword', msg.subject)
            self.assertNotIn('badword', msg.body)

    def test_netmail_compose_ftn_applies_word_filter(self):
        from anetbbs.models import db, EchomailNetwork, NetmailMessage
        self._seed_filter('badword')
        user_id = self._make_user('wfnetmailuser')
        with self.app.app_context():
            net = EchomailNetwork(name='WFNetmailNet', network_type='binkp',
                                  is_active=True, our_address='1:114/1')
            db.session.add(net)
            db.session.commit()

        client = self._client_as(user_id)
        resp = client.post('/netmail/compose', data={
            'to_address': '1:1/1', 'to_name': 'Someone',
            'subject': 'a badword here', 'body': 'and a badword here too',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            msg = (NetmailMessage.query
                  .filter_by(to_address='1:1/1')
                  .order_by(NetmailMessage.id.desc()).first())
            self.assertIsNotNone(msg)
            self.assertNotIn('badword', msg.subject)
            self.assertNotIn('badword', msg.body)

    def test_terminal_composers_call_word_filter_apply(self):
        """The two terminal composers (features/bbs_ui.py's ANSI
        composer, features/petscii_ui.py's PETSCII composer) are large
        interactive functions with real read_line/anedit-editor
        machinery threaded through -- not practically drivable
        end-to-end in a unit test just to reach this one fix (same
        reasoning several other tests in this suite already document
        for hard-to-drive terminal flows). Verified structurally
        instead: both must call word_filter's apply() before
        constructing their EchomailMessage."""
        import inspect
        from anetbbs.features import bbs_ui, petscii_ui

        bbs_ui_src = inspect.getsource(bbs_ui)
        em_idx = bbs_ui_src.index("em = EchomailMessage(")
        # Bounded window immediately before the construction site -- the
        # file also has an EARLIER, unrelated word_filter reference (the
        # already-fixed board-post composer), so an unbounded rindex()
        # from 0 would false-positive even with this call site's own fix
        # reverted.
        nearby = bbs_ui_src[max(0, em_idx - 600):em_idx]
        self.assertIn('word_filter', nearby,
                      'bbs_ui.py must call word_filter.apply() '
                      'immediately before constructing the EchomailMessage')

        petscii_src = inspect.getsource(petscii_ui)
        msg_idx = petscii_src.index("msg = EchomailMessage(")
        nearby2 = petscii_src[max(0, msg_idx - 600):msg_idx]
        self.assertIn('word_filter', nearby2,
                      'petscii_ui.py must call word_filter.apply() '
                      'immediately before constructing the EchomailMessage')


if __name__ == '__main__':
    unittest.main()
