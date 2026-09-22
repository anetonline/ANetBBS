"""Regression tests for a real bug + new feature in
anetbbs/features/mrc_chat.py, found live (2026-09-21):

1. Bug: the terminal bell (\\x07) used to be concatenated directly
   into the string passed to _emit(), which stores it verbatim in
   self._scrollback (and, in split-screen mode, self._display_lines).
   _redraw_chat_area() repaints the last N stored lines on every new
   message, terminal resize, /scroll, sidebar toggle, etc -- so a
   caller heard a bell every time a mentioned/DM/CTCP-reply line was
   still on screen, not just once when it first arrived. Fixed by
   never storing the bell: every call site now rings it as a
   separate, non-stored write right after _emit() returns.

   These tests prove the fix at its root: the bell byte is never
   present in the stored _scrollback entry at all (see
   BellNotStoredInScrollbackTests below) -- a direct, conclusive check
   that a future redraw structurally cannot reproduce it, without
   needing to fully simulate split-screen terminal geometry to trigger
   a live second redraw (an existing test in test_mrc_terminal_mentions.py,
   test_status_bar_shows_mention_count_once_nonzero, shows that's
   genuinely awkward against a fake session with no real window size).

2. Feature: a new per-user "Use sound" toggle (/sound on|off), saved
   server-side the same way /shield already is -- mirrors uMRC's own
   "Use sound" setting.
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
    chat._split_screen = False  # simplest _emit() path
    chat._handle = handle
    chat.sent = []

    async def _fake_send_json(obj):
        chat.sent.append(obj)
    chat._send_json = _fake_send_json
    return chat


def _run(coro):
    return asyncio.run(coro)


def _written_text(chat):
    return ''.join(str(w) for w in chat.session.written)


class BellNotStoredInScrollbackTests(unittest.TestCase):
    """The root-cause proof: the bell must never end up in the stored
    scrollback entry, regardless of whether it's rung. Everything here
    uses the default _use_sound=True, so the bell DOES ring once (via
    session.written) -- the point is it's a live write, not stored
    text."""

    def test_ctcp_reply_bell_not_stored(self):
        chat = _make_chat()
        _run(chat._handle_event({
            'type': 'mrc_message', 'from_user': 'Wanderer',
            'message': '[CTCP-REPLY] Wanderer VERSION uMRC/1.06',
        }))
        self.assertNotIn('\x07', chat._scrollback[-1])
        self.assertIn('\x07', _written_text(chat))

    def test_room_mention_bell_not_stored(self):
        chat = _make_chat('StingRay')
        _run(chat._handle_event({
            'type': 'mrc_message', 'from_user': 'Wanderer',
            'from_site': 'otherbbs', 'from_room': 'lobby',
            'message': '[Wanderer]@otherbbs hey StingRay check this out',
        }))
        self.assertNotIn('\x07', chat._scrollback[-1])
        self.assertIn('\x07', _written_text(chat))

    def test_pm_bell_not_stored(self):
        chat = _make_chat('StingRay')
        _run(chat._handle_event({
            'type': 'mrc_message', 'from_user': 'Wanderer',
            'from_site': 'otherbbs', 'from_room': 'lobby',
            'message': '|15* |08(|15Wanderer|08/|14DirectMsg|08) |07hello there',
        }))
        self.assertNotIn('\x07', chat._scrollback[-1])
        self.assertIn('\x07', _written_text(chat))

    def test_typed_action_event_mention_bell_not_stored(self):
        chat = _make_chat('StingRay')
        _run(chat._handle_event({
            'type': 'action', 'user': 'Wanderer', 'bbs': 'otherbbs',
            'room': 'lobby', 'body': 'waves at StingRay',
        }))
        self.assertNotIn('\x07', chat._scrollback[-1])
        self.assertIn('\x07', _written_text(chat))

    def test_typed_chat_event_mention_bell_not_stored(self):
        chat = _make_chat('StingRay')
        _run(chat._handle_event({
            'type': 'chat', 'user': 'Wanderer', 'bbs': 'otherbbs',
            'room': 'lobby', 'body': 'hey StingRay check this out',
        }))
        self.assertNotIn('\x07', chat._scrollback[-1])
        self.assertIn('\x07', _written_text(chat))

    def test_typed_action_event_no_mention_no_bell(self):
        chat = _make_chat('StingRay')
        _run(chat._handle_event({
            'type': 'action', 'user': 'Wanderer', 'bbs': 'otherbbs',
            'room': 'lobby', 'body': 'waves at everyone',
        }))
        self.assertNotIn('\x07', _written_text(chat))


class SoundDisabledSuppressesBellTests(unittest.TestCase):
    """With the new toggle off, the message must still be shown --
    only the bell is suppressed."""

    def test_room_mention_no_bell_when_sound_off(self):
        chat = _make_chat('StingRay')
        chat._use_sound = False
        _run(chat._handle_event({
            'type': 'mrc_message', 'from_user': 'Wanderer',
            'from_site': 'otherbbs', 'from_room': 'lobby',
            'message': '[Wanderer]@otherbbs hey StingRay check this out',
        }))
        text = _written_text(chat)
        self.assertNotIn('\x07', text)
        self.assertIn('check this out', text)

    def test_typed_chat_event_no_bell_when_sound_off(self):
        chat = _make_chat('StingRay')
        chat._use_sound = False
        _run(chat._handle_event({
            'type': 'chat', 'user': 'Wanderer', 'bbs': 'otherbbs',
            'room': 'lobby', 'body': 'hey StingRay check this out',
        }))
        text = _written_text(chat)
        self.assertNotIn('\x07', text)
        self.assertIn('check this out', text)

    def test_pm_no_bell_when_sound_off(self):
        chat = _make_chat('StingRay')
        chat._use_sound = False
        _run(chat._handle_event({
            'type': 'mrc_message', 'from_user': 'Wanderer',
            'from_site': 'otherbbs', 'from_room': 'lobby',
            'message': '|15* |08(|15Wanderer|08/|14DirectMsg|08) |07hello there',
        }))
        text = _written_text(chat)
        self.assertNotIn('\x07', text)
        self.assertIn('hello there', text)


class ApplyPrefsSoundTests(unittest.TestCase):
    def test_joined_event_populates_use_sound(self):
        chat = _make_chat()
        _run(chat._handle_event({
            'type': 'joined', 'handle': 'StingRay', 'room': 'lobby',
            'prefs': {'use_sound': False},
        }))
        self.assertFalse(chat._use_sound)

    def test_prefs_updated_event_syncs_use_sound(self):
        chat = _make_chat()
        chat._use_sound = False
        _run(chat._handle_event({
            'type': 'prefs_updated',
            'prefs': {'use_sound': True},
        }))
        self.assertTrue(chat._use_sound)

    def test_default_use_sound_is_true(self):
        chat = _make_chat()
        self.assertTrue(chat._use_sound)

    def test_non_bool_use_sound_ignored(self):
        chat = _make_chat()
        _run(chat._handle_event({
            'type': 'prefs_updated', 'prefs': {'use_sound': 'yes'},
        }))
        self.assertTrue(chat._use_sound)  # unchanged, not coerced


class SoundCommandTests(unittest.TestCase):
    def test_sound_on_sends_set_prefs(self):
        chat = _make_chat()
        _run(chat._handle_slash('/sound on'))
        self.assertEqual(chat.sent, [{'type': 'set_prefs', 'use_sound': True}])

    def test_sound_off_sends_set_prefs(self):
        chat = _make_chat()
        _run(chat._handle_slash('/sound off'))
        self.assertEqual(chat.sent, [{'type': 'set_prefs', 'use_sound': False}])

    def test_sound_no_arg_shows_status_no_wire_traffic(self):
        chat = _make_chat()
        chat._use_sound = True
        _run(chat._handle_slash('/sound'))
        self.assertEqual(chat.sent, [])
        self.assertIn('on', _written_text(chat).lower())


if __name__ == '__main__':
    unittest.main()
