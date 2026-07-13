"""Tests for MRC Phase F (feature-parity rework): /dlchatlog transcript
download in anetbbs/features/mrc_chat.py.

Mirrors BBSMenuUI._ebook_download's tempfile-then-send_file pattern
(anetbbs/features/bbs_ui.py) -- these tests patch
anetbbs.features.xfer.send_file/available_protocols (the module the
method does a deferred `from .xfer import ...` against at call time)
rather than exercising a real ZMODEM handshake.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.core  # noqa: F401  (resolves a circular import if mrc_chat is imported first)
from anetbbs.features.mrc_chat import MRCChat


class _FakeSession:
    def __init__(self):
        self.user = {'username': 'tester'}
        self.written = []

    async def write(self, text):
        self.written.append(text)


def _make_chat(split_screen=False):
    chat = MRCChat(_FakeSession())
    chat._split_screen = split_screen
    chat._handle = 'StingRay'
    return chat


def _run(coro):
    return asyncio.run(coro)


class DlChatLogCommandTests(unittest.TestCase):
    def test_empty_scrollback_shows_message_no_transfer_attempted(self):
        chat = _make_chat()
        with patch('anetbbs.features.xfer.available_protocols') as mock_protos:
            _run(chat._handle_slash('/dlchatlog'))
            mock_protos.assert_not_called()
        joined = '\n'.join(chat.session.written)
        self.assertIn('Nothing to download', joined)

    def test_no_protocol_available_shows_error(self):
        chat = _make_chat()
        chat._scrollback.append('hello there')
        with patch('anetbbs.features.xfer.available_protocols', return_value=[]):
            _run(chat._handle_slash('/dlchatlog'))
        joined = '\n'.join(chat.session.written)
        self.assertIn('No file-transfer protocol available', joined)

    def test_aliases_all_trigger_download(self):
        for alias in ('dlchatlog', 'dlchat', 'transcript'):
            chat = _make_chat()
            chat._scrollback.append('hi')
            with patch('anetbbs.features.xfer.available_protocols', return_value=[]):
                _run(chat._handle_slash(f'/{alias}'))
            joined = '\n'.join(chat.session.written)
            self.assertIn('No file-transfer protocol available', joined)

    def test_successful_download_sends_stripped_scrollback_and_cleans_up(self):
        chat = _make_chat(split_screen=False)
        chat._scrollback.append('\x1b[1;36mHello\x1b[0m |15World')

        captured_path = {}

        async def fake_send_file(session, filepath, protocol='zmodem'):
            captured_path['path'] = filepath
            with open(filepath, encoding='utf-8') as f:
                captured_path['content'] = f.read()
            self.assertTrue(os.path.exists(filepath))
            return True

        with patch('anetbbs.features.xfer.available_protocols', return_value=['zmodem']), \
             patch('anetbbs.features.xfer.send_file', side_effect=fake_send_file):
            _run(chat._handle_slash('/dlchatlog'))

        # ANSI + pipe codes stripped from the downloaded text
        self.assertIn('Hello World', captured_path['content'].replace('\r\n', ''))
        # Temp file cleaned up after send
        self.assertFalse(os.path.exists(captured_path['path']))

    def test_split_screen_exited_and_restored_around_transfer(self):
        chat = _make_chat(split_screen=True)
        chat._scrollback.append('hi')

        calls = []

        async def fake_exit():
            calls.append('exit')
            chat._split_screen = False
        async def fake_enter():
            calls.append('enter')
            chat._split_screen = True
        async def fake_redraw():
            calls.append('redraw')

        chat._exit_split_screen = fake_exit
        chat._enter_split_screen = fake_enter
        chat._redraw_chat_area = fake_redraw

        async def fake_send_file(session, filepath, protocol='zmodem'):
            # Split-screen should be off DURING the transfer
            self.assertFalse(chat._split_screen)
            return True

        with patch('anetbbs.features.xfer.available_protocols', return_value=['zmodem']), \
             patch('anetbbs.features.xfer.send_file', side_effect=fake_send_file):
            _run(chat._handle_slash('/dlchatlog'))

        self.assertEqual(calls, ['exit', 'enter', 'redraw'])

    def test_non_split_screen_never_touches_split_screen_methods(self):
        chat = _make_chat(split_screen=False)
        chat._scrollback.append('hi')
        chat._exit_split_screen = AsyncMock()
        chat._enter_split_screen = AsyncMock()

        async def fake_send_file(session, filepath, protocol='zmodem'):
            return True

        with patch('anetbbs.features.xfer.available_protocols', return_value=['zmodem']), \
             patch('anetbbs.features.xfer.send_file', side_effect=fake_send_file):
            _run(chat._handle_slash('/dlchatlog'))

        chat._exit_split_screen.assert_not_called()
        chat._enter_split_screen.assert_not_called()


if __name__ == '__main__':
    unittest.main()
