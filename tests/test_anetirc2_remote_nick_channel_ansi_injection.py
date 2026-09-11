"""Regression test: anetirc2.py's _strip() (mIRC/ANSI/control stripping)
was only ever applied to IRC MESSAGE BODIES (the `trailing` parameter),
never to `src` -- the server-supplied nick/prefix parsed off the front
of a raw protocol line. `src` is attacker-controlled: a user can point
this client at any IRC server they choose (or sit behind a MITM on an
unverified, non-TLS connection), and a rogue/compromised server can put
whatever bytes it likes in a NICK/PRIVMSG/JOIN prefix.

Found in a deep review of this audit round's real scope: draw_chat()
already runs `_strip(ln.text)` on every line's BODY before rendering,
but a PRIVMSG's `nick` field is stored and rendered separately
(`f"[{ts}] <{ln.nick}> "` in draw_chat) with no stripping at all, and
the USERS panel (self.irc.users, from JOIN/NICK/353) is rendered raw
too (draw_users). A rogue server could set a nick to a raw ANSI/CSI
escape sequence and have it render straight to a real BBS user's
terminal -- screen clears, spoofed prompts, cursor tricks -- the same
bug class already fixed for message bodies here and for identity
fields elsewhere in this audit round (mrc_chat.py, wall.py). Same
applies to a self-JOIN's channel name (stored into self.channel, shown
unstripped in the status bar) and a NICK confirmation's new nick
(can become our OWN self.nick, also shown unstripped in the status
bar).

Fixed by stripping `src` once, right where it's parsed in _handle(),
plus the JOIN channel and NICK new-nick values, so every downstream use
inherits a clean value.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.core  # noqa: F401  (resolves a circular import if anetirc2 is imported first)
from anetbbs.features.anetirc2 import _IRC


class _FakeClient:
    def __init__(self):
        self.lines = []
        self.dirty_status = False
        self.dirty_users = False
        self.dirty_chat = False

    def _add(self, ln):
        self.lines.append(ln)

    def _sys(self, text):
        self.lines.append(text)


def _make_irc(nick='ourname', channel='#chan'):
    irc = _IRC(_FakeClient())
    irc.nick = nick
    irc.channel = channel
    irc.connected = True
    return irc


_EVIL_NICK = 'evil\x1b[2J\x1b[Hnick'  # a real CSI erase-screen + cursor-home, mid-nick


class RemoteNickChannelAnsiInjectionTests(unittest.TestCase):
    def test_privmsg_nick_field_is_stripped(self):
        irc = _make_irc()
        raw = f':{_EVIL_NICK}!user@host PRIVMSG #chan :hello there'
        asyncio.run(irc._handle(raw))
        # The one added _Line for this PRIVMSG carries the nick in its
        # own `.nick` field, separate from the (already-safe) body.
        added = [ln for ln in irc.client.lines if not isinstance(ln, str)]
        self.assertEqual(len(added), 1)
        self.assertNotIn('\x1b', added[0].nick)
        self.assertIn('evilnick', added[0].nick.lower())

    def test_join_adds_stripped_nick_to_users_list(self):
        irc = _make_irc()
        raw = f':{_EVIL_NICK}!user@host JOIN #chan'
        asyncio.run(irc._handle(raw))
        self.assertEqual(len(irc.users), 1)
        self.assertNotIn('\x1b', irc.users[0])

    def test_self_join_channel_name_is_stripped(self):
        irc = _make_irc(nick='ourname')
        evil_chan = '#chan\x1b[2J\x1b[H'
        raw = f':ourname!user@host JOIN {evil_chan}'
        asyncio.run(irc._handle(raw))
        self.assertNotIn('\x1b', irc.channel)

    def test_nick_change_target_is_stripped(self):
        irc = _make_irc(nick='oldnick')
        irc.users = ['oldnick']
        raw = f':oldnick!user@host NICK :{_EVIL_NICK}'
        asyncio.run(irc._handle(raw))
        self.assertNotIn('\x1b', irc.nick)
        self.assertNotIn('\x1b', irc.users[0])

    def test_names_list_353_nicks_are_stripped(self):
        # 353 (RPL_NAMREPLY) is the PRIMARY way self.users gets
        # populated -- sent automatically right after joining any
        # channel -- so this is actually the most exploitable path into
        # the raw-rendered USERS panel, not just a secondary one.
        irc = _make_irc(nick='ourname')
        raw = f':irc.example.org 353 ourname = #chan :ourname regular {_EVIL_NICK}'
        asyncio.run(irc._handle(raw))
        self.assertTrue(irc.users)
        self.assertTrue(all('\x1b' not in u for u in irc.users))
        self.assertIn('evilnick', [u.lower() for u in irc.users
                                    if 'evilnick' in u.lower()][0])


if __name__ == '__main__':
    unittest.main()
