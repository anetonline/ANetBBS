"""Regression test: mrc_irc_bridge.py relayed text in both directions
with no real sanitization -- _strip_mirc() only ever stripped mIRC
formatting bytes, never ESC or any other C0 control byte (including
\\r/\\n).

Two distinct real bugs from that one gap:

1. ANSI/CSI escape injection -- a hostile IRC peer's nick/message body,
   or a hostile MRC-side chat message, could carry a raw ESC sequence
   straight through to whichever side renders it.

2. IRC protocol/command injection -- an MRC-side `text` or handle value
   containing a literal '\\r'/'\\n' (fully legal inside a JSON string,
   so nothing about the websocket transport prevented it) got embedded
   unfiltered into a raw `PRIVMSG <chan> :<text>` IRC line before
   _send() appends its own trailing "\\r\\n" -- letting that text
   terminate the current protocol line and inject an arbitrary
   follow-up IRC command (e.g. "...\\r\\nQUIT :pwned") through the
   bridge's own IRC connection.

Fixed by a combined _strip() (mIRC codes, then well-formed ANSI/CSI,
then a blanket C0+DEL control-byte strip) applied at both ingestion
points: _IrcLeg._handle()'s `sender`/PRIVMSG text, and _MrcLeg.run()'s
`user`/`text` extracted from the websocket JSON.
"""
import asyncio
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aiohttp

from anetbbs.features.mrc_irc_bridge import _IrcLeg, _MrcLeg


class _FakeWsMsg:
    def __init__(self, data):
        self.type = aiohttp.WSMsgType.TEXT
        self.data = json.dumps(data)


class _FakeWs:
    def __init__(self, msgs):
        self._msgs = msgs

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for m in self._msgs:
            yield m


class IrcLegSanitizationTests(unittest.TestCase):
    def test_privmsg_sender_and_text_are_stripped(self):
        received = []

        async def on_message(sender, text):
            received.append((sender, text))

        leg = _IrcLeg('irc.example.org', 6667, False, 'bridgenick', '#chan',
                      on_message=on_message)
        raw = ':evil\x1b[2J\x1b[Hnick!u@h PRIVMSG #chan :hi\x1b[31mthere'
        asyncio.run(leg._handle(raw))

        self.assertEqual(len(received), 1)
        sender, text = received[0]
        self.assertNotIn('\x1b', sender)
        self.assertNotIn('\x1b', text)
        self.assertIn('evilnick', sender.lower())
        self.assertIn('hithere', text)


class MrcLegSanitizationTests(unittest.TestCase):
    def test_crlf_and_ansi_stripped_before_reaching_callback(self):
        """The real, most severe bug: an MRC-side chat message containing
        a literal CR/LF must never survive to reach code that embeds it
        in a raw IRC protocol line (run_bridge()'s on_mrc_msg ->
        irc_leg.say() -> _send()), since that's a straight IRC
        command-injection primitive."""
        received = []

        async def on_message(user, text):
            received.append((user, text))

        leg = _MrcLeg('ws://127.0.0.1:8080/ws', 'room1', 'bridgehandle',
                      on_message=on_message)
        malicious_text = 'hello\r\nQUIT :pwned\x1b[2J'
        leg.ws = _FakeWs([_FakeWsMsg({'type': 'message', 'room': 'room1',
                                       'handle': 'attacker\r\nQUIT', 'text': malicious_text})])
        asyncio.run(leg.run())

        self.assertEqual(len(received), 1)
        user, text = received[0]
        self.assertNotIn('\r', user)
        self.assertNotIn('\n', user)
        self.assertNotIn('\r', text)
        self.assertNotIn('\n', text)
        self.assertNotIn('\x1b', text)
        # Content itself (minus the control bytes) is preserved, not
        # just blanked -- e.g. still legible in the relayed chat line.
        self.assertIn('QUIT :pwned', text)


if __name__ == '__main__':
    unittest.main()
