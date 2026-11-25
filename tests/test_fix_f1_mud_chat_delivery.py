"""F-1: MUD chat is delivered between players.

Before the fix, say/yell/tell/emote were appended to a shared log that
nothing ever read, and the log grew forever.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from anetbbs.features.mud import mp


class MudChatDelivery(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='mudchat-'))
        p = mock.patch.object(mp, 'SAVE_DIR', self.tmp)
        p.start()
        self.addCleanup(p.stop)

    def _deliver(self, tail, me, room):
        return [t for t in (mp.deliverable(e, me, room) for e in tail.poll()) if t]

    def test_room_scoping_yell_and_tell(self):
        alice, bob, carol = mp.ChatTail(), mp.ChatTail(), mp.ChatTail()
        alice.send('alice', 'say', 'hello', room_slug='tavern')
        alice.send('alice', 'emote', 'waves', room_slug='tavern')
        alice.send('alice', 'yell', 'anyone?', room_slug='tavern')
        alice.send('alice', 'tell', 'psst', target='bob', room_slug='tavern')
        b = self._deliver(bob, 'bob', 'tavern')
        c = self._deliver(carol, 'carol', 'forest')
        self.assertEqual(b, ['alice says, "hello"', 'alice waves',
                             'alice yells, "anyone?"', 'alice tells you, "psst"'])
        self.assertEqual(c, ['alice yells, "anyone?"'])
        self.assertEqual(self._deliver(alice, 'alice', 'tavern'), [])   # not echoed to sender

    def test_new_session_does_not_replay_history(self):
        mp.ChatTail().send('alice', 'yell', 'old news')
        late = mp.ChatTail()
        self.assertEqual(late.poll(), [])
        mp.ChatTail().send('alice', 'yell', 'fresh')
        self.assertEqual([e[2] for e in late.poll()], ['fresh'])

    def test_partial_line_kept_for_next_poll(self):
        t = mp.ChatTail()
        path = mp._chat_path()
        with open(path, 'a') as f:
            f.write('["a", "yell", "half')
        self.assertEqual(t.poll(), [])
        with open(path, 'a') as f:
            f.write('", "", ""]\n')
        self.assertEqual([e[2] for e in t.poll()], ['half'])

    def test_log_is_bounded_and_readers_recover(self):
        t = mp.ChatTail()
        with mock.patch.object(mp, 'CHAT_LOG_MAX_BYTES', 200):
            for i in range(20):
                mp.ChatTail().send('a', 'yell', f'm{i}' * 5)
                t.poll()
            self.assertLess(mp._chat_path().stat().st_size, 400)
            mp.ChatTail().send('a', 'yell', 'after-rotate')
            self.assertIn('after-rotate', [e[2] for e in t.poll()])

    def test_escape_sequences_stripped(self):
        t = mp.ChatTail()
        mp.ChatTail().send('evil\x1b[2J', 'yell', 'hi\x1b]0;x\x07')
        (text,) = self._deliver(t, 'bob', 'x')
        self.assertNotIn('\x1b', text)


if __name__ == '__main__':
    unittest.main()
