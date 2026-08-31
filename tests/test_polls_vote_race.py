"""Regression test for a real Medium-severity finding from a security/
performance audit (2026-08-31): anetbbs/web/polls.py's vote() route had
a classic check-then-insert TOCTOU race -- it queried for an existing
PollVote row, and only inserted a new one if none was found. Two
concurrent vote submissions from the SAME user (a double-click, two
open tabs/devices) can both run that SELECT before either commits,
both see no existing vote, and both attempt to INSERT. The DB's own
UniqueConstraint('poll_id', 'user_id') on PollVote (see models.py)
already prevents the duplicate row from actually landing, but the
loser's commit() used to raise an unhandled IntegrityError -- a hard
500 for what should be the same graceful "vote recorded" outcome a
sequential double-submit gets.

Fixed by catching IntegrityError around the insert-path commit and
falling back to the same update-existing-vote path used when the
initial SELECT does find a row.

This race is reliably reproducible with real threads (unlike a byte-
counter race) because the vulnerable window is the STARTING state
every thread sees: with zero existing PollVote rows for this user,
every thread's initial SELECT can return None concurrently before any
of them has committed, since SQLite allows concurrent reads outside of
an active write transaction. Real N-thread concurrent submissions
reliably hit the collision.
"""
import os
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class PollVoteRaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.polls_vote_race_test.db')
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

    def _make_poll_and_user(self, username):
        from anetbbs.models import db, User, Poll, PollOption
        with self.app.app_context():
            u = User(username=username, email=f'{username}@example.com',
                    is_active=True)
            u.set_password('votertestpassword123')
            db.session.add(u)
            db.session.commit()
            user_id = u.id

            p = Poll(creator_id=user_id, question='Best BBS door?',
                     is_active=True)
            db.session.add(p)
            db.session.flush()
            opt_a = PollOption(poll_id=p.id, text='LORD', sort_order=0)
            opt_b = PollOption(poll_id=p.id, text='TradeWars', sort_order=1)
            db.session.add_all([opt_a, opt_b])
            db.session.commit()
            return user_id, p.id, opt_a.id, opt_b.id

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def test_concurrent_double_submit_never_500s_and_leaves_exactly_one_vote(self):
        """Best-effort real-thread stress test, NOT the primary guard --
        confirmed via this project's own verify-by-revert discipline
        that this doesn't reliably discriminate: with real Flask
        test-client requests over a local SQLite DB, the actual
        SELECT-then-INSERT window is far shorter than real OS thread
        wake-up latency (even with a synchronization barrier and 40
        threads), so the race window essentially never gets hit by
        real scheduling in this environment -- the same phenomenon
        already documented for the byte-counter race in
        test_file_quota.py's ConsumeQuotaAtomicIncrementTests. Kept as
        a real-world sanity check (it does confirm nothing crashes
        under load); the deterministic guard is the test below."""
        THREAD_COUNT = 40
        user_id, poll_id, opt_a_id, opt_b_id = self._make_poll_and_user(
            'pollracetester')

        client = self._client_as(user_id)
        statuses = []
        errors = []
        lock = threading.Lock()
        barrier = threading.Barrier(THREAD_COUNT)

        def _worker(i):
            try:
                option_id = opt_a_id if i % 2 == 0 else opt_b_id
                barrier.wait(timeout=5)
                resp = client.post(f'/polls/{poll_id}/vote',
                                   data={'option_id': option_id},
                                   follow_redirects=False)
                with lock:
                    statuses.append(resp.status_code)
            except Exception as exc:  # pylint: disable=broad-except
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=_worker, args=(i,))
                  for i in range(THREAD_COUNT)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertEqual(errors, [], f'worker thread(s) raised: {errors}')
        self.assertTrue(all(s < 500 for s in statuses),
                        f'a concurrent double-submit must never 500 -- got '
                        f'statuses: {statuses}')

        from anetbbs.models import PollVote
        with self.app.app_context():
            votes = PollVote.query.filter_by(
                poll_id=poll_id, user_id=user_id).all()
            self.assertEqual(
                len(votes), 1,
                f'exactly one vote must survive concurrent double-submits '
                f'from the same user -- got {len(votes)} rows, meaning the '
                "DB's own uniqueness guarantee was somehow bypassed")

    def test_toctou_window_forced_deterministically_never_500s(self):
        """The actual deterministic regression guard: forces the exact
        interleaving the race depends on -- a request whose initial
        "do I already have a vote?" check returns None even though a
        concurrent winner's vote row already exists in the DB (as if
        that winner committed in the gap between this request's SELECT
        and its own INSERT). Patches PollVote.query for exactly the
        FIRST filter_by(...).first() call in the request (simulating
        the stale read), letting every subsequent call hit the real DB
        -- so the fallback path, if present, sees the real, current
        state. Without the fix, the forced None reliably drives the
        code into an INSERT that collides with the real UniqueConstraint
        and raises an unhandled IntegrityError (a 500); with the fix,
        the collision is caught and the vote is recorded via the
        update-existing path instead."""
        from unittest.mock import patch
        from anetbbs.models import db, PollVote

        user_id, poll_id, opt_a_id, opt_b_id = self._make_poll_and_user(
            'polltoctoutester')

        with self.app.app_context():
            # The "concurrent winner" -- already committed by the time
            # this request's (patched, stale) existence check runs.
            db.session.add(PollVote(poll_id=poll_id, option_id=opt_a_id,
                                    user_id=user_id))
            db.session.commit()

        call_count = {'n': 0}

        class _StaleFirstCallQuery:
            def filter_by(self, **kwargs):
                call_count['n'] += 1
                if call_count['n'] == 1:
                    class _StaleResult:
                        def first(self):
                            return None
                    return _StaleResult()
                # Real query, resolved fresh against the live request's
                # app context (not captured before patching, which
                # would need its own app context and would otherwise
                # just recurse back into this same patched object).
                return db.session.query(PollVote).filter_by(**kwargs)

        client = self._client_as(user_id)
        # patch.object() itself reads the CURRENT value of PollVote.query
        # on entry (to restore it on exit) -- that read also needs a
        # live app context, same as any other access to a Flask-
        # SQLAlchemy model's .query descriptor.
        with self.app.app_context(), \
             patch.object(PollVote, 'query', _StaleFirstCallQuery()):
            resp = client.post(f'/polls/{poll_id}/vote',
                               data={'option_id': opt_b_id},
                               follow_redirects=False)

        self.assertLess(
            resp.status_code, 500,
            f'a forced check-then-insert race must not 500 -- got '
            f'{resp.status_code}. This is the actual TOCTOU window: the '
            'existence check went stale (forced None) while a real row '
            'already existed, so the INSERT collided with the DB '
            "UniqueConstraint and the resulting IntegrityError wasn't "
            'caught')

        with self.app.app_context():
            votes = PollVote.query.filter_by(
                poll_id=poll_id, user_id=user_id).all()
            self.assertEqual(
                len(votes), 1,
                'exactly one vote must survive the forced race -- the '
                'DB UniqueConstraint guarantees this regardless of the '
                'fix, so this is really just confirming no crash left '
                'the row count wrong via a bad partial commit')


if __name__ == '__main__':
    unittest.main()
