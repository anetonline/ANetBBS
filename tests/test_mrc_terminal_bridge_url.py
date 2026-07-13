"""Regression test for a live-reported bug: terminal MRC connected fine
via the web client but never connected via the terminal (SSH/telnet)
client, "flashes an error but too quick to see."

Root cause: MRCChat.show_menu() (anetbbs/features/mrc_chat.py) is called
from ChatManager.show_menu() (anetbbs/features/chat.py) with no Flask
app context active -- unlike _chat_flags() a few lines above it in the
same file, which correctly wraps its own DB read in `with _app().
app_context():`. Every `current_app.config.get(...)` call inside
show_menu() therefore raised RuntimeError, silently swallowed by a bare
`except Exception: pass`, so the resolved bridge_url was ALWAYS the
hardcoded DEFAULT_BRIDGE_URL (port 8080) regardless of the real
MRC_BRIDGE_PORT config value (WEB_PORT+1, 5001 by default) -- a
permanent, silent misconfiguration for every terminal MRC session on
every install, not something specific to one sysop's setup.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.core  # noqa: F401  (resolves a circular import if mrc_chat is imported first)
from anetbbs.features.mrc_chat import MRCChat


class _FakeSession:
    def __init__(self):
        self.user = {'username': 'tester'}
        self.written = []

    async def write(self, text):
        self.written.append(text)


def _run(coro):
    return asyncio.run(coro)


class TerminalBridgeUrlResolutionTests(unittest.TestCase):
    def test_show_menu_resolves_configured_bridge_port_not_hardcoded_default(self):
        """With a real (non-default) MRC_BRIDGE_PORT set in app config,
        show_menu() must build a bridge_url using that port -- not
        silently fall back to the hardcoded 8080 default because no app
        context was available to read config from."""
        chat = MRCChat(_FakeSession())

        captured = {}

        async def _fake_connect_and_chat(bridge_url, handle, room):
            captured['bridge_url'] = bridge_url

        chat._connect_and_chat = _fake_connect_and_chat

        # Patch the base Config class, not ProductionConfig specifically --
        # MRC_BRIDGE_PORT is defined once on Config and inherited by
        # Production/Development/TestingConfig alike, and other test
        # files running earlier in a full suite leave FLASK_ENV set to
        # 'testing' in this same process, so _app() may resolve any of
        # them depending on run order. Patching the shared base class is
        # the only way this assertion holds regardless of which one
        # get_config() actually returns.
        import anetbbs.config as cfg_mod
        orig = cfg_mod.Config.MRC_BRIDGE_PORT
        cfg_mod.Config.MRC_BRIDGE_PORT = 5001
        try:
            _run(chat.show_menu())
        finally:
            cfg_mod.Config.MRC_BRIDGE_PORT = orig

        self.assertIn('bridge_url', captured, 'show_menu() must call _connect_and_chat')
        self.assertIn(':5001', captured['bridge_url'],
                       f"expected the configured port 5001 in {captured['bridge_url']!r}, "
                       "not a hardcoded fallback")
        self.assertNotIn(':8080', captured['bridge_url'],
                          'must not silently fall back to the stale hardcoded default '
                          'when a real app context and config are available')


if __name__ == '__main__':
    unittest.main()
