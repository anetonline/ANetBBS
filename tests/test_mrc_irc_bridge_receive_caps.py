"""Regression tests for two real gaps found in a security/performance
audit of the MRC<->IRC bridge (anetbbs/features/mrc_irc_bridge.py):

1. _MrcLeg (the bridge's own connection to the upstream MRC bridge
   service) called aiohttp's ws_connect() with no explicit
   max_msg_size, relying on aiohttp's library default rather than a
   limit chosen for what this protocol actually needs.

2. _IrcLeg (the bridge's connection out to a real IRC server) called
   asyncio.open_connection() with no explicit `limit=`, AND its run()
   loop's except clause only caught (OSError, ConnectionError) around
   reader.readline() -- asyncio.LimitOverrunError (raised when a line
   exceeds the stream limit with no terminator) is not a subclass of
   either, so it used to propagate out of run() uncaught instead of
   disconnecting cleanly like every other failure mode there does.

Fixed with an explicit, modest limit on each connection plus handling
LimitOverrunError as a normal disconnect.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.features import mrc_irc_bridge as mod


class MrcLegMaxMsgSizeTests(unittest.IsolatedAsyncioTestCase):
    async def test_connect_passes_an_explicit_max_msg_size(self):
        leg = mod._MrcLeg(ws_url='ws://127.0.0.1:9000/ws', room='lobby',
                          handle='tester')

        fake_ws = object()

        class _FakeSession:
            def __init__(self):
                self.ws_connect_kwargs = None

            async def ws_connect(self, url, **kwargs):
                self.ws_connect_kwargs = kwargs
                return fake_ws

            async def close(self):
                pass

        fake_session = _FakeSession()
        with patch.object(mod.aiohttp, 'ClientSession', return_value=fake_session), \
             patch.object(mod._MrcLeg, '_send_json', new=AsyncMock()):
            await leg.connect()

        self.assertIn('max_msg_size', fake_session.ws_connect_kwargs)
        self.assertGreater(fake_session.ws_connect_kwargs['max_msg_size'], 0)
        self.assertLess(fake_session.ws_connect_kwargs['max_msg_size'],
                        4 * 1024 * 1024,
                        'must be an explicit, deliberately chosen cap, '
                        "not just aiohttp's own 4 MiB default")


class _EndlessNoNewlineReader:
    """Simulates a peer sending an endless stream with no '\\n' at all
    -- StreamReader.readline() raises asyncio.LimitOverrunError once
    this exceeds the configured limit."""
    def __init__(self, limit):
        self._limit = limit
        self.call_count = 0

    async def readline(self):
        self.call_count += 1
        if self.call_count > 3:
            raise asyncio.LimitOverrunError(
                'Separator not found, and chunk exceed the limit',
                self._limit)
        return b'X' * 100  # no '\n' -- a real StreamReader would keep
                            # accumulating instead of returning like this;
                            # simulated directly via the raise above once
                            # the limit is "exceeded".


class IrcLegLimitOverrunTests(unittest.IsolatedAsyncioTestCase):
    async def test_connect_passes_an_explicit_limit(self):
        leg = mod._IrcLeg(server='irc.example.com', port=6667, use_ssl=False,
                          nick='tester', channel='#test')

        captured = {}

        async def _fake_open_connection(*args, **kwargs):
            captured.update(kwargs)
            return AsyncMock(), AsyncMock()

        with patch.object(mod.asyncio, 'open_connection',
                          side_effect=_fake_open_connection), \
             patch.object(mod._IrcLeg, '_send', new=AsyncMock()):
            await leg.connect()

        self.assertIn('limit', captured)
        self.assertGreater(captured['limit'], 0)

    async def test_limit_overrun_disconnects_cleanly_instead_of_propagating(self):
        leg = mod._IrcLeg(server='irc.example.com', port=6667, use_ssl=False,
                          nick='tester', channel='#test')
        leg.connected = True
        leg.reader = _EndlessNoNewlineReader(limit=8192)

        # Must return normally (the loop catches LimitOverrunError and
        # breaks) -- before the fix this raised out of run() uncaught.
        await asyncio.wait_for(leg.run(), timeout=5)

        self.assertFalse(leg.connected)
        self.assertGreaterEqual(leg.reader.call_count, 1)


if __name__ == '__main__':
    unittest.main()
