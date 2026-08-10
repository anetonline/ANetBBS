"""Tests for two real bugs found live on the Pi (screenshots, PETSCII
40-column new-user registration on a real C64/SyncTerm session):

  1. The 'newuser' ANSI screen (sysop-customizable welcome banner shown
     right after successful registration) displayed as literal garbage
     -- raw ESC[...m/ESC[2J escape codes and PETSCII-case-inverted
     letters. `_show_ansi_screen()` writes raw CP437/ANSI bytes directly
     to the socket, bypassing session.write()'s petscii translation
     branch entirely -- the exact same limitation already guarded
     against for the 'welcome' (login_screen()) and 'goodbye' (disconnect
     path) slots (see tests/test_petscii_ui.py's
     test_sysop_welcome_ansi_art_skipped_for_petscii), just missed for
     'newuser'. Fixed by adding the same `if self.term_mode != 'petscii'`
     guard.

  2. The security-question list and newuser-questionnaire prompts were
     written as one long unwrapped line each via session.write() and
     left to the terminal's own hardware auto-wrap, which broke mid-word
     on a 40-column screen ("elementa|ry school", "first pe|t?"). Fixed
     with new module-level helpers _prompt_width()/_wrap_text_lines() in
     session.py (same word-boundary-aware approach as
     anetbbs/features/mrc_chat.py's _word_wrap(), see
     tests/test_mrc_word_wrap.py for that one).
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.core.session import BBSSession, _prompt_width, _wrap_text_lines


class _FakeWriter:
    def __init__(self):
        self.written = bytearray()

    def write(self, data):
        self.written += data

    async def drain(self):
        pass

    def close(self):
        pass


def _make_session(**kwargs):
    writer = _FakeWriter()
    session = BBSSession(object(), writer, config={}, **kwargs)
    return session, writer


def _queue_read_line(responses):
    queue = list(responses)

    async def _read_line(prompt=''):
        if not queue:
            raise AssertionError(
                f'_queue_read_line ran out of responses (prompt={prompt!r})')
        return queue.pop(0)
    return _read_line


def _queue_read_password(responses):
    queue = list(responses)

    async def _read_password(prompt=''):
        if not queue:
            raise AssertionError(
                f'_queue_read_password ran out of responses (prompt={prompt!r})')
        return queue.pop(0)
    return _read_password


class WrapTextLinesTests(unittest.TestCase):
    """Direct unit tests for the pure helper functions."""

    def test_short_text_is_returned_as_a_single_line(self):
        self.assertEqual(_wrap_text_lines('hi there', 40), ['hi there'])

    def test_wraps_at_word_boundaries_not_mid_word(self):
        out = _wrap_text_lines(
            'What was the name of your elementary school?', 34)
        for line in out:
            self.assertLessEqual(len(line), 34, f'line too long: {line!r}')
        joined = ' '.join(out)
        for word in ('What', 'name', 'elementary', 'school?'):
            self.assertIn(word, joined)
        self.assertNotIn('elementa\nry', joined)

    def test_embedded_newline_is_a_hard_break(self):
        out = _wrap_text_lines('line one\nline two', 40)
        self.assertEqual(out, ['line one', 'line two'])

    def test_prompt_width_prefers_petscii_width_over_window_size(self):
        session = MagicMock()
        session.petscii_width = 40
        session.window_size = (80, 24)
        self.assertEqual(_prompt_width(session), 40)

    def test_prompt_width_falls_back_to_window_size(self):
        session = MagicMock(spec=['window_size'])
        session.window_size = (100, 40)
        self.assertEqual(_prompt_width(session), 100)

    def test_prompt_width_defaults_to_80(self):
        session = MagicMock(spec=[])
        self.assertEqual(_prompt_width(session), 80)


class NewUserAnsiScreenPetsciiGuardTests(unittest.TestCase):
    def _run_registration(self, session):
        session.user_manager = MagicMock()
        session.user_manager.username_exists.return_value = False
        session.user_manager.email_exists.return_value = False
        session.user_manager.create_user.return_value = 'ok'
        session.user_manager.authenticate.return_value = {'id': 1, 'username': 'newbie'}
        session.read_line = _queue_read_line(['newbie', 'newbie@example.com'])
        session.read_password = _queue_read_password(['password1', 'password1'])
        session._show_ansi_screen = AsyncMock()
        session._collect_security_questions = AsyncMock()
        session._run_newuser_questionnaire = AsyncMock()
        return asyncio.run(session.handle_registration())

    def test_newuser_ansi_art_skipped_for_petscii(self):
        session, writer = _make_session(forced_term_mode='petscii')
        result = self._run_registration(session)
        self.assertTrue(result)
        session._show_ansi_screen.assert_not_called()
        # PETSCII's real write() path case-inverts plain text (see
        # petscii_codec.py) -- "Registration successful!" arrives on the
        # wire as "rEGISTRATION SUCCESSFUL!". Compare case-insensitively
        # rather than coupling this test to that unrelated translation
        # detail.
        self.assertIn(b'registration successful!',
                      bytes(writer.written).lower(),
                      "petscii users must still get a real confirmation "
                      "even though the customizable banner is skipped")

    def test_ansi_session_still_gets_newuser_screen(self):
        # Regression guard: the petscii skip must not affect the normal path.
        session, writer = _make_session()  # default term_mode == 'ansi'
        self._run_registration(session)
        session._show_ansi_screen.assert_called_once_with('newuser')


class SecurityQuestionsWordWrapTests(unittest.TestCase):
    """Captures text passed to session.write() BEFORE petscii translation
    (case-swap, control-byte color codes, CR-only line endings -- see
    petscii_codec.py) so these tests exercise the wrap logic in
    isolation instead of coupling to that unrelated encoding pipeline."""

    def _collect(self, session, choices):
        # Width is driven by the session's own forced_width (see
        # _make_session callers below) -- petscii_width is a read-only
        # @property, so it can't be reassigned on the instance directly.
        session.user = {'id': 1}
        session.user_manager = MagicMock()
        session.read_line = _queue_read_line(choices)
        captured = []

        async def _capture_write(text):
            captured.append(text)
        session.write = _capture_write

        asyncio.run(session._collect_security_questions())
        return ''.join(captured)

    def test_every_question_line_fits_within_the_session_width(self):
        session, _writer = _make_session(forced_term_mode='petscii', forced_width=40)
        transcript = self._collect(
            session, ['1', 'answerone', '2', 'answertwo', '3', 'answerthree'])
        from anetbbs.core.session import _ANSI_ESC_RE
        for line in transcript.split('\r\n'):
            visible = _ANSI_ESC_RE.sub('', line)
            self.assertLessEqual(len(visible), 40,
                                 f'line exceeds 40 columns: {visible!r}')

    def test_long_question_text_survives_intact_across_wrapped_lines(self):
        from anetbbs.models import SECURITY_QUESTIONS
        session, _writer = _make_session(forced_term_mode='petscii', forced_width=40)
        transcript = self._collect(
            session, ['1', 'answerone', '2', 'answertwo', '3', 'answerthree'])
        from anetbbs.core.session import _ANSI_ESC_RE
        flat = _ANSI_ESC_RE.sub('', transcript).replace('\r\n', ' ')
        # Every real word of the longest bundled question must appear
        # intact -- not split mid-word by hardware auto-wrap.
        longest = max(SECURITY_QUESTIONS, key=len)
        for word in longest.replace('?', '').split():
            self.assertIn(word, flat, f'{word!r} missing/split in transcript')

    def test_80_column_session_wraps_no_more_than_40_column(self):
        session40, _w40 = _make_session(forced_term_mode='petscii', forced_width=40)
        transcript_40 = self._collect(session40, ['1', 'aa', '2', 'bb', '3', 'cc'])
        session80, _w80 = _make_session(forced_term_mode='petscii', forced_width=80)
        transcript_80 = self._collect(session80, ['1', 'aa', '2', 'bb', '3', 'cc'])
        self.assertGreaterEqual(transcript_40.count('\r\n'), transcript_80.count('\r\n'),
                                'narrower session must not produce FEWER wrapped lines')


if __name__ == '__main__':
    unittest.main()
