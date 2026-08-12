"""Regression tests for anetbbs/core/mods_override.py's
call_core_override() -- the generic loader that lets a sysop drop a
full replacement Python file at data/mods/core/<name>.py to override
one of ANetBBS's own core screens, the same idea Synchronet already
gives sysops for login.js/logon.js in its own mods/ tree. Covers: an
override present and valid wins; a missing override falls back to
stock; a present-but-broken override (syntax error, missing function,
runtime exception) degrades to stock instead of taking the caller
down.
"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.core.mods_override import call_core_override


class CallCoreOverrideTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_dir = Path(self._tmp.name)
        (self.data_dir / 'mods' / 'core').mkdir(parents=True)

        class _FakeApp:
            config = {'DATA_DIR': str(self.data_dir)}

        self._patcher = patch('anetbbs.features.bbs_ui._app', return_value=_FakeApp())
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def _write_override(self, name, code):
        (self.data_dir / 'mods' / 'core' / f'{name}.py').write_text(code)

    async def _stock(self):
        return 'STOCK'

    def test_no_override_file_calls_stock(self):
        result = asyncio.run(
            call_core_override('login_menu', 'render_login_menu', self._stock))
        self.assertEqual(result, 'STOCK')

    def test_valid_override_wins_over_stock(self):
        self._write_override('login_menu', (
            "async def render_login_menu(session, bbs_name):\n"
            "    return 'OVERRIDE:' + bbs_name\n"
        ))
        result = asyncio.run(
            call_core_override('login_menu', 'render_login_menu', self._stock,
                                'a-session', 'My BBS'))
        self.assertEqual(result, 'OVERRIDE:My BBS')

    def test_override_file_exists_but_missing_function_falls_back(self):
        self._write_override('login_menu', "x = 1\n")
        result = asyncio.run(
            call_core_override('login_menu', 'render_login_menu', self._stock))
        self.assertEqual(result, 'STOCK')

    def test_override_with_syntax_error_falls_back(self):
        self._write_override('login_menu', "def broken(:\n")
        result = asyncio.run(
            call_core_override('login_menu', 'render_login_menu', self._stock))
        self.assertEqual(result, 'STOCK')

    def test_override_that_raises_at_runtime_falls_back(self):
        self._write_override('login_menu', (
            "async def render_login_menu(*a):\n"
            "    raise RuntimeError('boom')\n"
        ))
        result = asyncio.run(
            call_core_override('login_menu', 'render_login_menu', self._stock))
        self.assertEqual(result, 'STOCK')

    def test_stock_not_invoked_when_override_succeeds(self):
        # Guards against the "eagerly-built coroutine never awaited"
        # class of bug -- stock_fn must be a zero-arg callable the
        # loader only invokes when it actually needs the fallback.
        self._write_override('login_menu', (
            "async def render_login_menu(*a):\n"
            "    return 'OVERRIDE'\n"
        ))
        called = {'stock': False}

        async def _stock():
            called['stock'] = True
            return 'STOCK'

        result = asyncio.run(
            call_core_override('login_menu', 'render_login_menu', _stock))
        self.assertEqual(result, 'OVERRIDE')
        self.assertFalse(called['stock'])

    def test_different_mod_name_does_not_pick_up_unrelated_override(self):
        self._write_override('login_menu', (
            "async def render_login_menu(*a):\n"
            "    return 'OVERRIDE'\n"
        ))
        result = asyncio.run(
            call_core_override('some_other_screen', 'render', self._stock))
        self.assertEqual(result, 'STOCK')


if __name__ == '__main__':
    unittest.main()
