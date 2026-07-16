"""Regression test for a real live bug: sending a DM (or room message,
/me, /broadcast) that needed to be split into multiple wire chunks
always got the 2nd+ chunk rejected by the bridge with "Rate limit:
please slow down." -- reported live ("still getting this rate error
when dming... even though it's not [too long]").

The bridge's rate limiter (mrc/bridge/main.py's _rate_limit_ok) allows
at most one send_message/server_cmd/direct_message per connection every
message_rate_seconds (default 0.5s). _split_for_wire() correctly
accounts for a decorated display handle's prefix/suffix overhead
eating into the 140-char budget (a message well under 140 chars can
still need to split if the sender's styled handle is long), but every
multi-chunk send loop fired all its chunks back-to-back with zero
delay between them -- guaranteeing the bridge's own rate limiter
would reject every chunk after the first, no matter how short the
original message actually was.

Fixed by adding a small delay (WIRE_CHUNK_DELAY, intentionally a bit
above the bridge's own default) before every chunk after the first, in
all four places a message can be split: room chat, /me, /broadcast,
and DMs (/msg, /r).
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.core  # noqa: F401  (resolves a circular import if mrc_chat is imported first)
from anetbbs.features.mrc_chat import MRCChat, WIRE_CHUNK_DELAY


class _FakeSession:
    def __init__(self):
        self.user = {'username': 'tester'}
        self.written = []

    async def write(self, text):
        self.written.append(text)


def _make_chat(handle='StingRay'):
    chat = MRCChat(_FakeSession())
    chat._split_screen = False
    chat._handle = handle
    chat.sent = []

    async def _fake_send_json(obj):
        chat.sent.append(obj)
    chat._send_json = _fake_send_json
    return chat


def _run(coro):
    return asyncio.run(coro)


class WireChunkDelayTests(unittest.TestCase):
    def test_multichunk_dm_sleeps_between_chunks_not_before_first(self):
        chat = _make_chat()
        # A long overhead (simulating a decorated display handle) forces
        # a split well under the raw 140-char hub limit -- exactly the
        # reported scenario, not an artificially huge message.
        chat._dm_overhead = 100
        body = 'x' * 80  # cap = max(10, 140-100) = 40 -> must split

        with patch('anetbbs.features.mrc_chat.asyncio.sleep', new=AsyncMock()) as mock_sleep:
            _run(chat._send_dm('Winzlo', body))

        self.assertGreater(len(chat.sent), 1,
                           'test setup must actually force a multi-chunk send')
        # One sleep call before each chunk after the first.
        self.assertEqual(mock_sleep.call_count, len(chat.sent) - 1)
        for call in mock_sleep.call_args_list:
            self.assertEqual(call.args[0], WIRE_CHUNK_DELAY)

    def test_single_chunk_dm_never_sleeps(self):
        chat = _make_chat()
        with patch('anetbbs.features.mrc_chat.asyncio.sleep', new=AsyncMock()) as mock_sleep:
            _run(chat._send_dm('Winzlo', 'short message, no split needed'))
        self.assertEqual(len(chat.sent), 1)
        mock_sleep.assert_not_called()

    def test_multichunk_broadcast_sleeps_between_chunks(self):
        chat = _make_chat()
        long_text = 'word ' * 60  # forces a real split at the flat 140 cap
        with patch('anetbbs.features.mrc_chat.asyncio.sleep', new=AsyncMock()) as mock_sleep:
            _run(chat._handle_slash(f'/broadcast {long_text}'))
        broadcasts = [s for s in chat.sent if s.get('type') == 'server_cmd']
        self.assertGreater(len(broadcasts), 1)
        self.assertEqual(mock_sleep.call_count, len(broadcasts) - 1)

    def test_multichunk_me_sleeps_between_chunks(self):
        chat = _make_chat()
        long_text = 'word ' * 60
        with patch('anetbbs.features.mrc_chat.asyncio.sleep', new=AsyncMock()) as mock_sleep:
            _run(chat._handle_slash(f'/me {long_text}'))
        self.assertGreater(len(chat.sent), 1)
        self.assertEqual(mock_sleep.call_count, len(chat.sent) - 1)


if __name__ == '__main__':
    unittest.main()
