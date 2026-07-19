"""Feature test for the admin-facing half of the poll-in-progress FR:
the Poll Logs page (/admin/echomail/logs) and its transcript view
(/admin/echomail/logs/<id>/transcript) must show a 'running' poll
distinctly and never treat it like a hard failure or a 404, even though
its transcript may still be empty (checkpoints are best-effort, not
per-frame -- see binkp_server.py's _flush_transcript_checkpoint).
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class PollLogInProgressAdminUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.poll_log_progress_test.db')
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

    def _admin_client(self):
        from anetbbs.models import db, User
        with self.app.app_context():
            admin = User.query.filter_by(username='polllogprogresstest').first()
            if not admin:
                admin = User(username='polllogprogresstest', is_admin=True,
                            access_level=255,
                            email='polllogprogresstest@example.com')
                admin.set_password('x')
                db.session.add(admin)
                db.session.commit()
            admin_id = admin.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(admin_id)
            sess['_fresh'] = True
        return client

    def _make_network(self):
        from anetbbs.models import db, EchomailNetwork
        with self.app.app_context():
            net = EchomailNetwork(name='PollProgressNet', network_type='binkp')
            db.session.add(net)
            db.session.commit()
            return net.id

    def test_running_poll_shows_in_progress_not_error(self):
        from datetime import datetime
        from anetbbs.models import db, EchomailPollLog
        net_id = self._make_network()
        with self.app.app_context():
            log = EchomailPollLog(network_id=net_id, poll_type='both',
                                  started_at=datetime.utcnow(), status='running')
            db.session.add(log)
            db.session.commit()

        client = self._admin_client()
        resp = client.get('/admin/echomail/logs')
        html = resp.data.decode()
        self.assertEqual(resp.status_code, 200)
        self.assertIn('in progress', html)
        self.assertIn('data-status="running"', html)
        # The old fallback rendering ("~ running") must not be what a
        # sysop sees for this specific, common status -- that's what
        # made an in-progress poll look uncomfortably close to a real
        # unknown/error state before this feature existed.
        self.assertNotIn('~ running', html)

    def test_running_poll_with_no_transcript_yet_does_not_404(self):
        """Checkpoints are best-effort (a couple of natural points in the
        session), not per-frame -- a running poll can legitimately have
        no transcript yet. That must show a friendly in-progress message,
        not a 404 that looks like the poll never existed."""
        from datetime import datetime
        from anetbbs.models import db, EchomailPollLog
        net_id = self._make_network()
        with self.app.app_context():
            log = EchomailPollLog(network_id=net_id, poll_type='both',
                                  started_at=datetime.utcnow(), status='running',
                                  transcript=None)
            db.session.add(log)
            db.session.commit()
            log_id = log.id

        client = self._admin_client()
        resp = client.get(f'/admin/echomail/logs/{log_id}/transcript')
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode()
        self.assertIn('still in progress', html)
        # Must NOT literally print the word "None" from an un-guarded
        # {{ log.transcript }} render -- confirmed this would happen
        # before the template was updated to branch on this case.
        self.assertNotIn('>None<', html)

    def test_running_poll_with_partial_transcript_shows_it_with_partial_badge(self):
        from datetime import datetime
        from anetbbs.models import db, EchomailPollLog
        net_id = self._make_network()
        with self.app.app_context():
            log = EchomailPollLog(network_id=net_id, poll_type='both',
                                  started_at=datetime.utcnow(), status='running',
                                  transcript='[12:00:00.000] >> CMD NUL: SYS Test\n')
            db.session.add(log)
            db.session.commit()
            log_id = log.id

        client = self._admin_client()
        resp = client.get(f'/admin/echomail/logs/{log_id}/transcript')
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode()
        self.assertIn('partial', html.lower())
        self.assertIn('CMD NUL', html)

    def test_completed_poll_with_no_transcript_still_404s(self):
        """Guard against overcorrecting: a genuinely completed poll with
        no transcript (e.g. predates this feature, or a QWK poll) must
        keep 404ing exactly as before -- only 'running' gets the new
        friendly-message treatment."""
        from datetime import datetime
        from anetbbs.models import db, EchomailPollLog
        net_id = self._make_network()
        with self.app.app_context():
            log = EchomailPollLog(network_id=net_id, poll_type='both',
                                  started_at=datetime.utcnow(), status='success',
                                  transcript=None)
            db.session.add(log)
            db.session.commit()
            log_id = log.id

        client = self._admin_client()
        resp = client.get(f'/admin/echomail/logs/{log_id}/transcript')
        self.assertEqual(resp.status_code, 404)


if __name__ == '__main__':
    unittest.main()
