"""Regression tests: mrc_chat.py's _handle_event() sanitized the chat
`body` field against ANSI/control-byte injection (see _pipe_to_ansi's
own docstring) but never applied the same stripping to the identity
fields carried alongside it on the very same wire -- from_user/
from_site/from_room (real 'mrc_message' events), user/bbs/room (the
legacy 'chat'/'action'/'private' branches), the nick embedded in
USERIN:/USERLIST:/USERNICK:/USERROOM: control-message bodies, and the
'chatters'/'rooms' items list. Every one of those values gets embedded
raw into an f-string somewhere downstream (inline chat rendering,
_mention_log's 'from'/'room' fields -- rendered later by /mentions --
the nick-list sidebar via _known_users, and the persisted self._room
used on every status-bar redraw).

Found in a security/performance audit: a compromised/malicious peer
elsewhere on the wider MRC network could set their own handle, BBS
name, or room name to a raw ANSI/CSI escape sequence. mrc/bridge/'s own
MRCProtocol.parse_packet() already strips these same byte classes at
its wire-parse boundary today (out of scope here, see that module's own
_strip_untrusted()), so this is not currently reachable through the
live bridge's one real 'mrc_message' producer -- but mrc_chat.py itself
provided no independent protection for these fields the way it already
does for `body`, unlike a defense that should not depend entirely on a
separate out-of-scope service never regressing. Fixed by cleaning each
of these fields through the same _strip_pipe() helper already used for
`body`, at the point each one is extracted/stored.
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


def _make_chat(handle='StingRay'):
    chat = MRCChat(_FakeSession())
    chat._split_screen = False  # simplest _emit() path -- writes straight to session.write()
    chat._handle = handle
    return chat


def _run(coro):
    return asyncio.run(coro)


_EVIL = '\x1b[2J\x1b[HEvilNick'  # a real CSI erase-screen + cursor-home sequence, then a nick


class MrcMessageIdentityFieldSanitizationTests(unittest.TestCase):
    def test_from_user_ansi_stripped_from_rendered_output(self):
        chat = _make_chat('StingRay')
        _run(chat._handle_event({
            'type': 'mrc_message',
            'from_user': _EVIL,
            'from_site': 'otherbbs',
            'from_room': 'lobby',
            'message': f'[{_EVIL}]@otherbbs just chatting',
        }))
        rendered = ''.join(chat.session.written)
        self.assertNotIn('\x1b[2J', rendered)
        self.assertNotIn('\x1b[H', rendered)
        self.assertIn('EvilNick', rendered)

    def test_from_user_ansi_stripped_from_known_users(self):
        chat = _make_chat('StingRay')
        _run(chat._handle_event({
            'type': 'mrc_message',
            'from_user': _EVIL,
            'from_site': 'otherbbs',
            'from_room': 'lobby',
            'message': 'hello',
        }))
        for nick in chat._known_users:
            self.assertNotIn('\x1b', nick)

    def test_from_room_and_from_site_stripped_in_mention_log(self):
        chat = _make_chat('StingRay')
        _run(chat._handle_event({
            'type': 'mrc_message',
            'from_user': 'Wanderer',
            'from_site': _EVIL,
            'from_room': _EVIL,
            'message': '[Wanderer]@otherbbs hey StingRay check this out',
        }))
        self.assertEqual(chat._mention_count, 1)
        entry = chat._mention_log[0]
        self.assertNotIn('\x1b', entry['from'])
        self.assertNotIn('\x1b', entry['room'])
        self.assertIn('EvilNick', entry['from'])


class ControlMessageNickSanitizationTests(unittest.TestCase):
    def test_userin_nick_stripped(self):
        chat = _make_chat('StingRay')
        _run(chat._handle_event({
            'type': 'mrc_message', 'from_user': 'SERVER', 'to_user': 'CLIENT',
            'message': f'USERIN:{_EVIL}',
        }))
        for nick in chat._known_users:
            self.assertNotIn('\x1b', nick)
        self.assertIn('EvilNick', chat._known_users)

    def test_userlist_nick_stripped(self):
        chat = _make_chat('StingRay')
        _run(chat._handle_event({
            'type': 'mrc_message', 'from_user': 'SERVER', 'to_user': 'CLIENT',
            'message': f'USERLIST:{_EVIL},plainuser',
        }))
        for nick in chat._known_users:
            self.assertNotIn('\x1b', nick)
        self.assertIn('EvilNick', chat._known_users)
        self.assertIn('plainuser', chat._known_users)

    def test_userroom_room_stripped_and_persists_sanitized(self):
        chat = _make_chat('StingRay')
        chat._room = 'lobby'
        _run(chat._handle_event({
            'type': 'mrc_message', 'from_user': 'SERVER', 'to_user': 'CLIENT',
            'message': f'USERROOM:{_EVIL}',
        }))
        self.assertNotIn('\x1b', chat._room)
        self.assertIn('EvilNick', chat._room)
        # The announcement line itself must also come out clean, since
        # self._room gets re-embedded into the status bar on every
        # subsequent redraw too, not just this one-shot line.
        rendered = ''.join(chat.session.written)
        self.assertNotIn('\x1b[2J', rendered)


class ChattersRoomsListSanitizationTests(unittest.TestCase):
    def test_chatters_items_stripped(self):
        chat = _make_chat('StingRay')
        _run(chat._handle_event({
            'type': 'chatters',
            'items': [_EVIL, 'plainuser'],
        }))
        rendered = ''.join(chat.session.written)
        self.assertNotIn('\x1b[2J', rendered)
        self.assertIn('EvilNick', rendered)
        for nick in chat._known_users:
            self.assertNotIn('\x1b', nick)


if __name__ == '__main__':
    unittest.main()
