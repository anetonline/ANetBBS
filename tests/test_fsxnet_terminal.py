"""Tests for the fsxNet IBOL/IBLC terminal screens
(anetbbs/features/fsxnet_ibol.py, anetbbs/features/fsxnet_iblc.py).

Covers the pure-logic pieces directly (pipe/Ctrl-A color translation,
ANSI-injection stripping, BBS-directory aggregation) and a smoke test
of each entry point's "feature disabled" path against a minimal fake
session -- the same self-contained _FakeSession/_FakeWriter shape
already duplicated across this test suite (e.g.
tests/test_door_games_menu_layout.py), not imported.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _FakeWriter:
    def __init__(self, sink):
        self._sink = sink

    def write(self, data):
        self._sink.append(data.decode('latin-1', errors='replace')
                          if isinstance(data, bytes) else data)

    async def drain(self):
        pass


class _FakeSession:
    def __init__(self, responses=()):
        self.user = {'id': 1, 'username': 'tester'}
        self._responses = list(responses)
        self.written = []
        self.writer = _FakeWriter(self.written)

    async def write(self, text):
        self.written.append(text)

    async def read_line(self, prompt=''):
        if prompt:
            await self.write(prompt)
        if not self._responses:
            return ''
        return self._responses.pop(0)


def _make_app(db_path):
    import anetbbs.config as cfg_mod
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    os.environ['FLASK_ENV'] = 'testing'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    return app


class IbolPipeTranslationTests(unittest.TestCase):
    def test_pipe_code_converts_to_ansi(self):
        from anetbbs.features.fsxnet_ibol import _pipe_to_ansi
        out = _pipe_to_ansi('|12red text')
        self.assertIn('\x1b[1;31m', out)
        self.assertIn('red text', out)

    def test_ctrla_code_converts_to_ansi(self):
        from anetbbs.features.fsxnet_ibol import _pipe_to_ansi
        out = _pipe_to_ansi('\x01yhello')
        self.assertIn('\x1b[33m', out)
        self.assertIn('hello', out)

    def test_injected_ansi_escape_is_stripped(self):
        """A malicious/compromised peer BBS posting a raw CSI sequence
        must never reach the local terminal verbatim -- same security
        discipline as wall.py's own _strip_untrusted()."""
        from anetbbs.features.fsxnet_ibol import _pipe_to_ansi
        out = _pipe_to_ansi('safe\x1b[2J\x1b[Hmore text')
        self.assertNotIn('\x1b[2J', out)
        self.assertIn('safe', out)
        self.assertIn('more text', out)

    def test_control_bytes_are_stripped(self):
        from anetbbs.features.fsxnet_ibol import _pipe_to_ansi
        out = _pipe_to_ansi('before\x07bell\x00null after')
        self.assertNotIn('\x07', out)
        self.assertNotIn('\x00', out)


class IblcAggregationTests(unittest.TestCase):
    def _row(self, bbs_name, address, os_name, created_at):
        class _Row:
            pass
        r = _Row()
        r.bbs_name = bbs_name
        r.address = address
        r.os = os_name
        r.created_at = created_at
        return r

    def test_aggregates_calls_count_per_bbs(self):
        from anetbbs.features.fsxnet_iblc import _aggregate_bbses
        now = datetime.utcnow()
        rows = [
            self._row('BBS A', 'a.example.com', 'Linux', now - timedelta(hours=2)),
            self._row('BBS A', 'a.example.com', 'Linux', now - timedelta(hours=1)),
            self._row('BBS B', 'b.example.com', 'Windows', now),
        ]
        agg = _aggregate_bbses(rows)
        by_name = {a['bbs_name']: a for a in agg}
        self.assertEqual(by_name['BBS A']['calls'], 2)
        self.assertEqual(by_name['BBS B']['calls'], 1)

    def test_uses_most_recent_address_and_os(self):
        """A BBS that changed address/OS between calls must show the
        MOST RECENT values, not the first-seen ones."""
        from anetbbs.features.fsxnet_iblc import _aggregate_bbses
        now = datetime.utcnow()
        rows = [
            self._row('BBS A', 'old.example.com', 'DOS', now - timedelta(days=1)),
            self._row('BBS A', 'new.example.com', 'Linux', now),
        ]
        agg = _aggregate_bbses(rows)
        self.assertEqual(agg[0]['address'], 'new.example.com')
        self.assertEqual(agg[0]['os'], 'Linux')
        self.assertEqual(agg[0]['calls'], 2)

    def test_empty_rows_produce_empty_aggregate(self):
        from anetbbs.features.fsxnet_iblc import _aggregate_bbses
        self.assertEqual(_aggregate_bbses([]), [])


class TerminalEntryPointSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import anetbbs.config as cfg_mod
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI

    @classmethod
    def tearDownClass(cls):
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_app(str(Path(self._tmp.name) / 'term.db'))
        with self.app.app_context():
            from anetbbs.models import db
            db.create_all()

    # show_oneliner_wall()/show_last_callers() read config through
    # bbs_ui._app() -- a SEPARATELY cached Flask app (keyed on
    # (FLASK_ENV, DB URI), see that function's own docstring) that
    # re-syncs its config FROM the TestingConfig class on every call,
    # not from self.app.config. Setting self.app.config[...] directly
    # has no effect on what these functions see -- must patch the
    # TestingConfig class attribute itself, same pattern
    # tests/test_chat_mrc_toggle.py already established for exactly
    # this reason (cited by name in _app()'s own docstring).

    def test_oneliner_wall_shows_disabled_message_and_returns(self):
        import anetbbs.config as cfg_mod
        from anetbbs.features.fsxnet_ibol import show_oneliner_wall
        orig = cfg_mod.TestingConfig.FSXNET_IBOL_ENABLED
        cfg_mod.TestingConfig.FSXNET_IBOL_ENABLED = False
        self.addCleanup(lambda: setattr(cfg_mod.TestingConfig, 'FSXNET_IBOL_ENABLED', orig))
        session = _FakeSession(responses=[''])

        import asyncio
        asyncio.run(asyncio.wait_for(show_oneliner_wall(session), timeout=5))

        joined = ''.join(session.written)
        self.assertIn('not enabled', joined)

    def test_oneliner_wall_shows_empty_state_then_quits(self):
        import anetbbs.config as cfg_mod
        from anetbbs.features.fsxnet_ibol import show_oneliner_wall
        orig = cfg_mod.TestingConfig.FSXNET_IBOL_ENABLED
        cfg_mod.TestingConfig.FSXNET_IBOL_ENABLED = True
        self.addCleanup(lambda: setattr(cfg_mod.TestingConfig, 'FSXNET_IBOL_ENABLED', orig))
        session = _FakeSession(responses=['Q'])

        import asyncio
        asyncio.run(asyncio.wait_for(show_oneliner_wall(session), timeout=5))

        joined = ''.join(session.written)
        self.assertIn('nothing posted yet', joined)

    def test_last_callers_shows_disabled_message_and_returns(self):
        import anetbbs.config as cfg_mod
        from anetbbs.features.fsxnet_iblc import show_last_callers
        orig = cfg_mod.TestingConfig.FSXNET_IBLC_ENABLED
        cfg_mod.TestingConfig.FSXNET_IBLC_ENABLED = False
        self.addCleanup(lambda: setattr(cfg_mod.TestingConfig, 'FSXNET_IBLC_ENABLED', orig))
        session = _FakeSession(responses=[''])

        import asyncio
        asyncio.run(asyncio.wait_for(show_last_callers(session), timeout=5))

        joined = ''.join(session.written)
        self.assertIn('not enabled', joined)

    def test_last_callers_shows_empty_state_then_quits(self):
        import anetbbs.config as cfg_mod
        from anetbbs.features.fsxnet_iblc import show_last_callers
        orig = cfg_mod.TestingConfig.FSXNET_IBLC_ENABLED
        cfg_mod.TestingConfig.FSXNET_IBLC_ENABLED = True
        self.addCleanup(lambda: setattr(cfg_mod.TestingConfig, 'FSXNET_IBLC_ENABLED', orig))
        session = _FakeSession(responses=['Q'])

        import asyncio
        asyncio.run(asyncio.wait_for(show_last_callers(session), timeout=5))

        joined = ''.join(session.written)
        self.assertIn('none yet', joined)


class LoginModuleDispatchTests(TerminalEntryPointSmokeTests):
    """fsxnet_ibol/fsxnet_iblc must be reachable as real LoginModule
    module_type values (Admin -> Logon/Logoff Modules), the same way
    'wall'/'lastcallers' already are -- Jerry's explicit ask: these
    need to work as logon events, not just as a plain BBS-menu action.
    Reuses TerminalEntryPointSmokeTests' setUp (fresh test app/DB)."""

    def test_dispatch_fsxnet_ibol_reaches_the_real_screen(self):
        import asyncio
        from anetbbs.features.login_modules import _dispatch
        session = _FakeSession(responses=[''])
        asyncio.run(asyncio.wait_for(_dispatch(session, 'fsxnet_ibol', {}), timeout=5))
        joined = ''.join(session.written)
        self.assertIn('InterBBS Oneliners', joined)

    def test_dispatch_fsxnet_iblc_reaches_the_real_screen(self):
        import asyncio
        from anetbbs.features.login_modules import _dispatch
        session = _FakeSession(responses=[''])
        asyncio.run(asyncio.wait_for(_dispatch(session, 'fsxnet_iblc', {}), timeout=5))
        joined = ''.join(session.written)
        self.assertIn('Inter-BBS Last Callers', joined)

    def test_module_types_are_registered_in_the_web_admin_dropdown(self):
        from anetbbs.web.login_modules_admin import MODULE_TYPES
        types = dict(MODULE_TYPES)
        self.assertIn('fsxnet_ibol', types)
        self.assertIn('fsxnet_iblc', types)

    def test_module_types_are_registered_in_the_cfg_tool(self):
        from anetbbs.cfg.sections.login_modules import MODULE_TYPE_CHOICES
        self.assertIn('fsxnet_ibol', MODULE_TYPE_CHOICES)
        self.assertIn('fsxnet_iblc', MODULE_TYPE_CHOICES)


class FastLogonIndependenceTests(unittest.TestCase):
    """THE critical behavior Jerry asked to confirm: posting a login to
    fsxNet IBLC must happen unconditionally, regardless of fast-logon --
    same precedent as the existing ANET_LASTCALLERS relay and the local
    CallerLog row itself, which are both written by the same hardcoded
    hook in core/session.py / web/auth.py, never through the skippable
    LoginModule/run_modules() dispatch path at all."""

    def test_fsxnet_hook_runs_before_fast_logon_is_even_computed(self):
        """Pins the actual ordering in core/session.py: the CallerLog
        block (which calls post_lastcall_to_fsxnet) must appear BEFORE
        fast_logon is read/prompted for, so there is no code path by
        which fast_logon could affect whether it runs."""
        import inspect
        from anetbbs.core import session as session_mod
        src = inspect.getsource(session_mod)
        caller_log_pos = src.index('post_lastcall_to_fsxnet')
        fast_logon_pos = src.index('fast_logon = False')
        self.assertLess(
            caller_log_pos, fast_logon_pos,
            "post_lastcall_to_fsxnet() must be called before fast_logon "
            "is ever determined -- if this ever moves after, a sysop's "
            "Fast Logon users would stop getting recorded in fsxNet "
            "Last Callers, breaking parity with the local Last Callers "
            "list and ANET_LASTCALLERS, which are never skipped either.")

    def test_web_login_hook_has_no_fast_logon_concept_at_all(self):
        """Web logins have no fast-logon prompt in the first place --
        confirms the fsxnet hook there is unconditionally reached too."""
        import inspect
        from anetbbs.web import auth as auth_mod
        src = inspect.getsource(auth_mod)
        self.assertNotIn('fast_logon', src)
        self.assertIn('post_lastcall_to_fsxnet', src)


if __name__ == '__main__':
    unittest.main()
