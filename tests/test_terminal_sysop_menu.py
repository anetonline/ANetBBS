"""Regression tests for the broadened terminal sysop menu
(anetbbs/features/bbs_ui.py: _sysop_menu + its ~14 new categories),
added alongside the Node Monitor and MSP picker in the same pass.

Same reasoning as tests/test_ebook_terminal_menu.py and
tests/test_terminal_node_monitor.py: the lightbar screens themselves
aren't practically unit-testable without a full session mock. What IS
covered here: (1) every category referenced in _sysop_menu resolves to
a real bound method (guards against the monkey-patch-shadowing mistake
this codebase has hit before -- see feedback_bbs_ui_monkeypatch memory),
(2) the schedule-text parser/serializer used by the Events category
(a pure function, fully testable), and (3) DB-level mutations a few of
the category action handlers perform.
"""
import os
import sys
import shutil
import tempfile
import types
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


class SysopMenuWiringTests(unittest.TestCase):
    """Every category in _sysop_menu's `categories` list must resolve to
    a real, callable bound method -- this is exactly the failure mode
    the codebase's monkey-patch-shadowing bug produced before (a
    bottom-of-file `BBSMenuUI.<name> = <name>` assignment silently
    overwriting an unrelated same-named class-body method)."""

    EXPECTED_CATEGORY_METHODS = [
        'sysop_users', 'sysop_boards', 'sysop_echomail', 'sysop_games',
        'sysop_wall', 'sysop_file_queue', 'sysop_events', 'sysop_rss_admin',
        'sysop_login_modules', 'sysop_notifications', 'sysop_registry',
        'sysop_callers', 'sysop_node_monitor', 'sysop_status',
    ]

    def test_all_category_methods_exist_and_are_callable(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        for name in self.EXPECTED_CATEGORY_METHODS:
            method = getattr(BBSMenuUI, name, None)
            self.assertIsNotNone(method, f'BBSMenuUI.{name} is missing')
            self.assertTrue(callable(method), f'BBSMenuUI.{name} is not callable')

    def test_sysop_menu_itself_still_resolves(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        self.assertTrue(callable(getattr(BBSMenuUI, 'sysop_menu', None)))

    def test_sysop_cfg_tool_method_exists(self):
        # Not in EXPECTED_CATEGORY_METHODS above -- unlike every other
        # entry there, this one is only CONDITIONALLY added to
        # _sysop_menu's categories list (SSH sessions only), so its
        # wiring gets its own dedicated tests below rather than being
        # lumped in with the always-present categories.
        from anetbbs.features.bbs_ui import BBSMenuUI
        self.assertTrue(callable(getattr(BBSMenuUI, 'sysop_cfg_tool', None)))

    def test_sub_screen_helpers_exist(self):
        # Screens reachable only from inside a category (not directly off
        # the top-level menu) -- also worth guarding against shadowing.
        from anetbbs.features.bbs_ui import BBSMenuUI
        for name in ('sysop_bulletins', 'sysop_echomail_networks',
                     'sysop_qwk_requests', 'sysop_bad_areas',
                     'sysop_webhooks', 'sysop_broadcast_compose',
                     'sysop_motd', 'sysop_pages', '_sysop_echomail_areas'):
            self.assertTrue(callable(getattr(BBSMenuUI, name, None)),
                            f'BBSMenuUI.{name} is missing or not callable')


class ScheduleTextParsingTests(unittest.TestCase):
    """_schedule_to_text / _parse_schedule_text (Events category) --
    pure functions, no DB/session needed."""

    def test_round_trip_daily(self):
        from anetbbs.features.bbs_ui import _parse_schedule_text, _schedule_to_text
        sched, err = _parse_schedule_text('daily 03:30')
        self.assertIsNone(err)
        self.assertEqual(sched, {'kind': 'daily', 'time': '03:30'})
        self.assertEqual(_schedule_to_text(sched), 'daily 03:30')

    def test_round_trip_hourly(self):
        from anetbbs.features.bbs_ui import _parse_schedule_text
        sched, err = _parse_schedule_text('hourly 5')
        self.assertIsNone(err)
        self.assertEqual(sched, {'kind': 'hourly', 'minute': 5})

    def test_hourly_rejects_out_of_range(self):
        from anetbbs.features.bbs_ui import _parse_schedule_text
        sched, err = _parse_schedule_text('hourly 60')
        self.assertIsNone(sched)
        self.assertIsNotNone(err)

    def test_round_trip_weekly(self):
        from anetbbs.features.bbs_ui import _parse_schedule_text
        sched, err = _parse_schedule_text('weekly 6 04:30')
        self.assertIsNone(err)
        self.assertEqual(sched, {'kind': 'weekly', 'day': 6, 'time': '04:30'})

    def test_round_trip_interval(self):
        from anetbbs.features.bbs_ui import _parse_schedule_text
        sched, err = _parse_schedule_text('interval 30')
        self.assertIsNone(err)
        self.assertEqual(sched, {'kind': 'interval', 'minutes': 30})

    def test_unknown_kind_errors(self):
        from anetbbs.features.bbs_ui import _parse_schedule_text
        sched, err = _parse_schedule_text('monthly 1')
        self.assertIsNone(sched)
        self.assertIsNotNone(err)

    def test_empty_errors(self):
        from anetbbs.features.bbs_ui import _parse_schedule_text
        sched, err = _parse_schedule_text('')
        self.assertIsNone(sched)
        self.assertIsNotNone(err)


class CategoryActionDbTests(unittest.TestCase):
    """DB-level round trip for a representative sample of the new
    category action handlers' mutations (same logic the terminal
    handlers perform, exercised directly against the models)."""

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

    def test_wall_post_soft_delete(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'wall.db'))
        from anetbbs.models import db, WallPost
        with app.app_context():
            p = WallPost(username='alice', line1='hello')
            db.session.add(p)
            db.session.commit()
            pid = p.id

            # Same mutation as _sysop_wall's _delete() action.
            row = WallPost.query.get(pid)
            row.is_deleted = True
            db.session.commit()

            self.assertTrue(WallPost.query.get(pid).is_deleted)
            self.assertEqual(
                WallPost.query.filter_by(is_deleted=False).count(), 0)

    def test_scheduled_event_toggle_and_fire(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'events.db'))
        from anetbbs.models import db, ScheduledEvent
        from anetbbs.events.runner import fire
        with app.app_context():
            ev = ScheduledEvent(name='test noop', handler_key='noop',
                                is_enabled=True)
            db.session.add(ev)
            db.session.commit()
            eid = ev.id

            # Same mutation as _sysop_events's _toggle() action.
            row = ScheduledEvent.query.get(eid)
            row.is_enabled = not row.is_enabled
            db.session.commit()
            self.assertFalse(ScheduledEvent.query.get(eid).is_enabled)

            # Same call as _sysop_events's _run_now() action.
            ok, out = fire(app, eid)
            self.assertTrue(ok)
            refreshed = ScheduledEvent.query.get(eid)
            self.assertEqual(refreshed.last_status, 'ok')
            self.assertIsNotNone(refreshed.last_run_at)

    def test_motd_entry_create_and_toggle(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'motd.db'))
        from anetbbs.models import db, MotdEntry
        with app.app_context():
            db.session.add(MotdEntry(text='Welcome!', weight=1, is_active=True))
            db.session.commit()
            m = MotdEntry.query.filter_by(text='Welcome!').first()
            self.assertIsNotNone(m)
            self.assertTrue(m.is_active)

            m.is_active = not m.is_active
            db.session.commit()
            self.assertFalse(MotdEntry.query.get(m.id).is_active)

    def test_sysop_page_reply_uses_push_message_not_db(self):
        """The Notifications > Sysop Pages 'reply' action pushes via the
        in-process sysop_paging inbox (both ends are terminal sessions
        in the same process for a terminal-originated reply) -- confirm
        that path still round-trips."""
        from anetbbs.features import sysop_paging
        sysop_paging.push_message(7, 'sysop', 'page reply text')
        msgs = sysop_paging.pop_messages(7)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]['text'], 'page reply text')


class SessionIsSshHelperTests(unittest.TestCase):
    """_session_is_ssh() -- pure function, gates the anetbbs-cfg
    launcher to SSH-only sessions (Jerry's explicit security
    requirement: the tool edits security levels/echomail credentials,
    and telnet carries all of that in plaintext)."""

    def _fake_session(self, protocol):
        presence = types.SimpleNamespace(protocol=protocol) if protocol else None
        return types.SimpleNamespace(presence=presence)

    def test_ssh_session_is_true(self):
        from anetbbs.features.bbs_ui import _session_is_ssh
        self.assertTrue(_session_is_ssh(self._fake_session('ssh')))

    def test_telnet_session_is_false(self):
        from anetbbs.features.bbs_ui import _session_is_ssh
        self.assertFalse(_session_is_ssh(self._fake_session('telnet')))

    def test_rlogin_session_is_false(self):
        from anetbbs.features.bbs_ui import _session_is_ssh
        self.assertFalse(_session_is_ssh(self._fake_session('rlogin')))

    def test_no_presence_at_all_is_false(self):
        # Defensive: a session that never went through the normal
        # login/presence-assignment path (shouldn't happen in
        # practice) must fail CLOSED, not open.
        from anetbbs.features.bbs_ui import _session_is_ssh
        self.assertFalse(_session_is_ssh(self._fake_session(None)))


class SysopCfgToolGatingTests(unittest.TestCase):
    """_sysop_cfg_tool's OWN defensive re-check -- confirms it refuses
    to even attempt a launch on a non-SSH session, independent of
    _sysop_menu's category-list gating (covered separately above via
    SessionIsSshHelperTests). Two layers, tested independently,
    matching the function's own docstring reasoning: the menu simply
    never offers a key that reaches this function from telnet, and
    this function doesn't trust that alone."""

    class _FakeSession:
        def __init__(self, protocol):
            self.presence = types.SimpleNamespace(protocol=protocol) if protocol else None
            self.written = []

        async def write(self, data):
            self.written.append(data)

        async def read_line(self, prompt=''):
            return ''

    def test_telnet_session_is_refused_before_any_launch_attempt(self):
        import asyncio
        from anetbbs.features.bbs_ui import BBSMenuUI

        fake_self = types.SimpleNamespace(session=self._FakeSession('telnet'))
        asyncio.run(BBSMenuUI.sysop_cfg_tool(fake_self))
        joined = ''.join(fake_self.session.written)
        self.assertIn('SSH', joined)


if __name__ == '__main__':
    unittest.main()
