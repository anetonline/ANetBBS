"""Regression tests for two real bugs reported live in ANetBBS's
terminal MRC client (anetbbs/features/mrc_chat.py):

1. Topic line repeating: ROOMTOPIC: packets used to print "── Topic:
   ..." unconditionally every time one arrived, even when the topic
   hadn't actually changed. The hub can re-send ROOMTOPIC: for reasons
   unrelated to an actual topic change, so the line spammed the chat
   with the exact same text repeatedly -- confirmed live. Fixed by
   only announcing it when the topic text genuinely differs from what
   was already known.

2. Stale userlist on leave: the USERLIST: bulk-refresh handler only
   ever ADDED nicks to _known_users, never removed any -- so if a
   single user's own USEROUT: event was ever missed (dropped frame,
   ordering hiccup, or a leave reason the hub doesn't send USEROUT
   for), that stale name stuck around in the sidebar forever, since
   even a fresh, completely correct USERLIST: bulk refresh could never
   clean it up (purely additive, no way to shrink). Fixed by treating
   USERLIST: as the hub's authoritative full-room snapshot that
   REPLACES the known set, not a union into whatever was already there.
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


def _make_chat(room='lobby'):
    chat = MRCChat(_FakeSession())
    chat._handle = 'StingRay'
    chat._room = room
    return chat


def _run(coro):
    return asyncio.run(coro)


class TopicRepeatTests(unittest.TestCase):
    def _topic_announcements(self, chat):
        return [s for s in chat._scrollback if 'Topic:' in s]

    def test_first_topic_announcement_shows(self):
        chat = _make_chat(room='lobby')
        _run(chat._handle_event({'body': 'ROOMTOPIC:lobby:Welcome to the lobby'}))
        self.assertEqual(chat._topic, 'Welcome to the lobby')
        self.assertEqual(len(self._topic_announcements(chat)), 1)

    def test_identical_repeat_does_not_announce_again(self):
        chat = _make_chat(room='lobby')
        _run(chat._handle_event({'body': 'ROOMTOPIC:lobby:Welcome, kids!'}))
        _run(chat._handle_event({'body': 'ROOMTOPIC:lobby:Welcome, kids!'}))
        _run(chat._handle_event({'body': 'ROOMTOPIC:lobby:Welcome, kids!'}))
        self.assertEqual(len(self._topic_announcements(chat)), 1,
                         'an unchanged topic must not be re-announced on every packet')

    def test_genuine_change_still_announces(self):
        chat = _make_chat(room='lobby')
        _run(chat._handle_event({'body': 'ROOMTOPIC:lobby:First topic'}))
        _run(chat._handle_event({'body': 'ROOMTOPIC:lobby:Second topic'}))
        announcements = self._topic_announcements(chat)
        self.assertEqual(len(announcements), 2,
                         'a genuinely different topic must still be announced')
        self.assertIn('Second topic', announcements[-1])


class UserlistReplaceTests(unittest.TestCase):
    def test_userlist_refresh_removes_a_user_no_longer_present(self):
        chat = _make_chat()
        # Simulate an earlier state where Alice was known (e.g. from a
        # USERIN: event, or an earlier USERLIST:) but her USEROUT: was
        # missed -- she's stale.
        chat._known_users = {'Alice', 'Bob'}

        _run(chat._handle_event({'body': 'USERLIST:Bob,Carol'}))

        self.assertEqual(chat._known_users, {'Bob', 'Carol'},
                         'a fresh USERLIST: must fully replace the known '
                         'set, dropping anyone no longer present')
        self.assertNotIn('Alice', chat._known_users)

    def test_userlist_refresh_still_adds_new_users(self):
        chat = _make_chat()
        chat._known_users = {'Bob'}
        _run(chat._handle_event({'body': 'USERLIST:Bob,Carol,Dave'}))
        self.assertEqual(chat._known_users, {'Bob', 'Carol', 'Dave'})

    def test_userlist_strips_at_host_suffix(self):
        chat = _make_chat()
        chat._known_users = set()
        _run(chat._handle_event({'body': 'USERLIST:Alice@somebbs,Bob@otherbbs'}))
        self.assertEqual(chat._known_users, {'Alice', 'Bob'})

    def test_individual_userout_still_works_between_refreshes(self):
        """Sanity check the existing, already-correct single-user path
        (USEROUT:) still works -- this fix only changes the bulk
        USERLIST: handler, not the incremental one."""
        chat = _make_chat()
        chat._known_users = {'Alice', 'Bob'}
        _run(chat._handle_event({'body': 'USEROUT:Alice'}))
        self.assertEqual(chat._known_users, {'Bob'})


if __name__ == '__main__':
    unittest.main()
