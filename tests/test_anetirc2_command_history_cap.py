"""Regression test: ANetIRC's command-history list (self._hist, used by
Up/Down arrow recall in the chat input line) had no size cap at all --
unlike self.lines (capped at _MAX_LINES in _add()), a long-running IRC
session that just kept chatting/issuing commands grew self._hist without
bound for the life of the connection. Fixed with the same trim-to-cap
pattern _add() already uses, at _MAX_HIST entries (keeping the most
recent).
"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.core  # noqa: F401  (resolves a circular import if anetirc2 is imported first)
from anetbbs.features.anetirc2 import ANetIRC, _MAX_HIST


class _FakeSession:
    def __init__(self):
        self.written = []

    async def write(self, text):
        self.written.append(text)


def _make_client():
    tmp = tempfile.NamedTemporaryFile(suffix='.cfg', delete=False)
    tmp.close()
    return ANetIRC(_FakeSession(), tmp.name)


class CommandHistoryCapTests(unittest.TestCase):
    def test_history_never_exceeds_max_hist(self):
        client = _make_client()
        client.irc.connected = False  # command() no-ops without a live connection

        async def _drive():
            for i in range(_MAX_HIST + 50):
                client._inp = f'line {i}'
                client._cur = len(client._inp)
                await client._chat_key('ENTER')

        asyncio.run(_drive())
        self.assertLessEqual(len(client._hist), _MAX_HIST)

    def test_history_keeps_the_most_recent_entries(self):
        client = _make_client()
        client.irc.connected = False

        async def _drive():
            for i in range(_MAX_HIST + 50):
                client._inp = f'line {i}'
                client._cur = len(client._inp)
                await client._chat_key('ENTER')

        asyncio.run(_drive())
        # The oldest entries must have been dropped, not the newest.
        self.assertNotIn('line 0', client._hist)
        self.assertIn(f'line {_MAX_HIST + 49}', client._hist)


if __name__ == '__main__':
    unittest.main()
