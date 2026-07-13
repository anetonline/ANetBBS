"""Tests for MRC Phase F (feature-parity rework): input-history recall
on Ctrl+Up/Down in anetbbs/features/mrc_chat.py.

Plain Up/Down stay bound to chat-scroll (this file's existing, already-
shipped convention) -- the locked-in design decision for this feature
was a *different* binding, Ctrl+Up/Down, so the two don't collide. That
requires _read_escape_seq to actually preserve the modifier + final
letter for a CSI cursor sequence (e.g. ESC [ 1 ; 5 A) instead of
truncating it to 2 bytes the way the pre-existing PgUp/PgDn handling
does -- covered here alongside the higher-level recall logic.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.core  # noqa: F401  (resolves a circular import if mrc_chat is imported first)
from anetbbs.features.mrc_chat import MRCChat


class _QueuedReader:
    """Feeds pre-queued single-byte reads, like a real asyncio
    StreamReader would for a terminal session -- used to drive
    _read_chat_line/_read_escape_seq end-to-end."""
    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    async def read(self, n=1):
        if self._pos >= len(self._data):
            return b''
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk


class _FakeSession:
    def __init__(self, reader_bytes=b''):
        self.user = {'username': 'tester'}
        self.written = []
        self.reader = _QueuedReader(reader_bytes)

    async def write(self, text):
        self.written.append(text)


def _make_chat(reader_bytes=b''):
    chat = MRCChat(_FakeSession(reader_bytes))
    chat._split_screen = False
    chat._handle = 'StingRay'
    return chat


def _run(coro):
    return asyncio.run(coro)


class ReadEscapeSeqTests(unittest.TestCase):
    def test_plain_up_arrow_returns_two_bytes(self):
        chat = _make_chat()
        reader = _QueuedReader(b'[A')
        seq = _run(chat._read_escape_seq(reader))
        self.assertEqual(seq, b'[A')

    def test_pgup_still_truncated_to_legacy_two_bytes(self):
        chat = _make_chat()
        reader = _QueuedReader(b'[5~')
        seq = _run(chat._read_escape_seq(reader))
        self.assertEqual(seq, b'[5')

    def test_ctrl_up_returns_full_modified_sequence(self):
        chat = _make_chat()
        reader = _QueuedReader(b'[1;5A')
        seq = _run(chat._read_escape_seq(reader))
        self.assertEqual(seq, b'[1;5A')

    def test_ctrl_down_returns_full_modified_sequence(self):
        chat = _make_chat()
        reader = _QueuedReader(b'[1;5B')
        seq = _run(chat._read_escape_seq(reader))
        self.assertEqual(seq, b'[1;5B')

    def test_shift_up_returns_full_modified_sequence_distinct_from_ctrl(self):
        chat = _make_chat()
        reader = _QueuedReader(b'[1;2A')
        seq = _run(chat._read_escape_seq(reader))
        self.assertEqual(seq, b'[1;2A')
        self.assertNotIn(b';5', seq)


class HistoryRecallLogicTests(unittest.TestCase):
    def test_no_history_is_a_no_op(self):
        chat = _make_chat()
        chat._input_buf = list('hello')
        _run(chat._history_recall(older=True))
        self.assertEqual(''.join(chat._input_buf), 'hello')
        self.assertIsNone(chat._history_pos)

    def test_ctrl_up_jumps_to_newest_entry_first(self):
        chat = _make_chat()
        chat._input_history.extend(['first', 'second', 'third'])
        chat._input_buf = list('draft')
        _run(chat._history_recall(older=True))
        self.assertEqual(''.join(chat._input_buf), 'third')
        self.assertEqual(chat._history_draft, 'draft')

    def test_repeated_ctrl_up_walks_further_back(self):
        chat = _make_chat()
        chat._input_history.extend(['first', 'second', 'third'])
        _run(chat._history_recall(older=True))
        _run(chat._history_recall(older=True))
        self.assertEqual(''.join(chat._input_buf), 'second')
        _run(chat._history_recall(older=True))
        self.assertEqual(''.join(chat._input_buf), 'first')

    def test_ctrl_up_stops_at_oldest_entry(self):
        chat = _make_chat()
        chat._input_history.extend(['first', 'second'])
        for _ in range(5):
            _run(chat._history_recall(older=True))
        self.assertEqual(''.join(chat._input_buf), 'first')

    def test_ctrl_down_past_newest_restores_draft(self):
        chat = _make_chat()
        chat._input_history.extend(['first', 'second'])
        chat._input_buf = list('my unsent draft')
        _run(chat._history_recall(older=True))   # -> 'second'
        _run(chat._history_recall(older=False))  # -> back to draft
        self.assertEqual(''.join(chat._input_buf), 'my unsent draft')
        self.assertIsNone(chat._history_pos)

    def test_ctrl_down_with_no_active_recall_is_a_no_op(self):
        chat = _make_chat()
        chat._input_history.extend(['first'])
        chat._input_buf = list('typing')
        _run(chat._history_recall(older=False))
        self.assertEqual(''.join(chat._input_buf), 'typing')


class ChatLoopHistoryAppendTests(unittest.TestCase):
    def test_submitted_line_appended_to_history(self):
        chat = _make_chat()
        lines = iter(['hello world', None])

        async def fake_read_chat_line():
            return next(lines)
        chat._read_chat_line = fake_read_chat_line
        chat._connected = True

        async def fake_handle_slash(line):
            return True
        chat._handle_slash = fake_handle_slash

        async def fake_send_json(obj):
            pass
        chat._send_json = fake_send_json

        _run(chat._chat_loop())
        self.assertEqual(list(chat._input_history), ['hello world'])

    def test_consecutive_duplicate_not_appended_twice(self):
        chat = _make_chat()
        lines = iter(['same', 'same', None])

        async def fake_read_chat_line():
            return next(lines)
        chat._read_chat_line = fake_read_chat_line
        chat._connected = True

        async def fake_send_json(obj):
            pass
        chat._send_json = fake_send_json

        _run(chat._chat_loop())
        self.assertEqual(list(chat._input_history), ['same'])

    def test_history_navigation_state_resets_on_submit(self):
        chat = _make_chat()
        chat._input_history.extend(['old'])
        chat._history_pos = 0
        chat._history_draft = 'leftover'
        lines = iter(['new line', None])

        async def fake_read_chat_line():
            return next(lines)
        chat._read_chat_line = fake_read_chat_line
        chat._connected = True

        async def fake_send_json(obj):
            pass
        chat._send_json = fake_send_json

        _run(chat._chat_loop())
        self.assertIsNone(chat._history_pos)
        self.assertEqual(chat._history_draft, '')


if __name__ == '__main__':
    unittest.main()
