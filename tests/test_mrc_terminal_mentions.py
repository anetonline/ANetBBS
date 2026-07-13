"""Regression tests for terminal MRC fixes from 2026-07-03:

1. /mentions always showed 0 -- real chat traffic arrives from the bridge
   as a 'mrc_message' event, but mention detection only ever ran inside
   dead 'chat'/'action'/'private' branches the bridge never actually sends.
2. Typing up to 140 chars could get silently truncated to ~120 on the
   receiving end -- the bridge prepends a styled display handle (or DM
   wrapper) to the message before enforcing its own hard 140-char cutoff,
   and the terminal client's own outgoing split-cap didn't account for
   that overhead the way the web client's _chatTypedLimit()/_dmTypedLimit()
   already did.
3. The mention indicator (status bar + inline highlight) used reverse-video
   red that was reportedly illegible on some terminals unless highlighted;
   switched to explicit fg/bg, same technique as the existing PAUSED badge.
4. /help and /helpserver were swapped: /help now asks the MRC hub for its
   own help (previously /helpserver's job); /helpserver now shows this
   client's local command reference (previously /help's job). /h and /?
   stay bound to the local list.
5. /mentions' output was misaligned -- a single long "time  room  from  body"
   line meant _emit()'s word-wrap (which only knows about its own added
   "HH:MM " timestamp prefix) had no idea about the extra room/from columns
   baked into the text, so wrapped continuation lines landed under the
   timestamp instead of under the body text. Fixed by giving _emit() an
   extra_indent parameter and splitting each mention into a short header
   line plus an indented, wrap-aware body line.
6. Outgoing text color (arrow-key cycled) never persisted across
   reconnects, even though handle prefix/suffix/color style did -- the
   'joined' event's payload already includes a style.typing_color field
   (same one the web client's typing-color dropdown reads/writes), but
   the terminal client ignored that part of the payload entirely. Now
   restores _color_idx from it on join, and pushes a set_style update
   whenever the user cycles color with the arrow keys.
7. Tab-complete was "iffy" -- sometimes worked, sometimes silently did
   nothing (zero matches gave no feedback at all, indistinguishable from
   "broken"), and could dump an unbounded list of every candidate for an
   ambiguous short prefix in a busy room. Added explicit "no match"
   feedback, capped the multi-candidate display with a "+N more" hint,
   and lock-protected the buffer read/mutation for consistency with
   every other _input_buf mutation site in the reader loop.

See anetbbs/features/mrc_chat.py.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.core  # noqa: F401  (resolves a circular import if mrc_chat is imported first)
from anetbbs.features.mrc_chat import MRCChat, MAX_OUTGOING_CHARS, _split_for_wire, _COLOR_SEQ


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
    return chat


def _run(coro):
    return asyncio.run(coro)


class MentionDetectionTests(unittest.TestCase):
    def test_mrc_message_mentioning_handle_is_counted(self):
        chat = _make_chat('StingRay')
        _run(chat._handle_event({
            'type': 'mrc_message',
            'from_user': 'Wanderer',
            'from_site': 'otherbbs',
            'from_room': 'lobby',
            'message': '[Wanderer]@otherbbs hey StingRay check this out',
        }))
        self.assertEqual(chat._mention_count, 1)
        self.assertEqual(len(chat._mention_log), 1)
        entry = chat._mention_log[0]
        self.assertEqual(entry['room'], '#lobby')
        self.assertIn('Wanderer', entry['from'])

    def test_non_mentioning_message_not_counted(self):
        chat = _make_chat('StingRay')
        _run(chat._handle_event({
            'type': 'mrc_message',
            'from_user': 'Wanderer',
            'from_site': 'otherbbs',
            'from_room': 'lobby',
            'message': '[Wanderer]@otherbbs just chatting about nothing',
        }))
        self.assertEqual(chat._mention_count, 0)
        self.assertEqual(len(chat._mention_log), 0)

    def test_dead_chat_action_private_branches_never_reached_by_real_traffic(self):
        # Sanity check on the actual root cause: the bridge only ever sends
        # 'mrc_message' for chat traffic (mrc/bridge/main.py), never 'chat'/
        # 'action'/'private'. Confirms fixing those branches alone (without
        # touching the mrc_message path) would NOT have fixed the bug.
        chat = _make_chat('StingRay')
        _run(chat._handle_event({
            'type': 'chat', 'user': 'Wanderer', 'bbs': 'otherbbs',
            'body': 'hey StingRay', 'room': 'lobby',
        }))
        # The (dead but harmless) 'chat' branch still runs standalone and
        # would count this -- proving it's DEAD only in the sense the
        # bridge never sends it, not that the branch itself is broken.
        self.assertEqual(chat._mention_count, 1)
        chat2 = _make_chat('StingRay')
        _run(chat2._handle_event({
            'type': 'mrc_message', 'from_user': 'Wanderer', 'from_site': 'otherbbs',
            'from_room': 'lobby', 'message': '[Wanderer]@otherbbs hey StingRay',
        }))
        self.assertEqual(chat2._mention_count, 1)

    def test_own_messages_never_count_as_mentions(self):
        chat = _make_chat('StingRay')
        _run(chat._handle_event({
            'type': 'mrc_message',
            'from_user': 'StingRay',
            'from_site': 'thisbbs',
            'from_room': 'lobby',
            'message': '[StingRay]@thisbbs talking to myself about StingRay',
        }))
        self.assertEqual(chat._mention_count, 0)

    def test_server_messages_never_count_as_mentions(self):
        chat = _make_chat('StingRay')
        _run(chat._handle_event({
            'type': 'mrc_message',
            'from_user': 'SERVER',
            'message': 'Welcome StingRay to the lobby',
        }))
        self.assertEqual(chat._mention_count, 0)

    def test_direct_message_marker_counts_as_mention_regardless_of_text(self):
        # Bridge marks PMs with a literal '/DirectMsg' substring in the
        # formatted body (mrc/bridge/main.py _dm_wrapper_prefix) rather than
        # a distinct event type -- same marker the web client checks.
        chat = _make_chat('StingRay')
        _run(chat._handle_event({
            'type': 'mrc_message',
            'from_user': 'Wanderer',
            'from_site': 'otherbbs',
            'message': '|15* |08(|15Wanderer|08/|14DirectMsg|08) |07no name mentioned here',
        }))
        self.assertEqual(chat._mention_count, 1)
        self.assertTrue(chat._mention_log[0]['body'].startswith('[DM] '))

    def test_status_bar_shows_mention_count_once_nonzero(self):
        chat = _make_chat('StingRay')
        chat._split_screen = True
        chat._mention_count = 3
        # _draw_status_line writes via session.write when split_screen is on;
        # just confirm it doesn't blow up and the count made it into the
        # write buffer somewhere.
        async def go():
            try:
                await chat._draw_status_line()
            except Exception:
                pass
        _run(go())
        joined = ''.join(str(x) for x in chat.session.written)
        self.assertIn('!3', joined)


class ReplyToLastDmTests(unittest.TestCase):
    """Tests for /r (reply-to-last-DM), MRC Phase F. Tracks the most
    recent inbound DM's sender the same way the mention log does (see
    _handle_event's is_dm/is_from_me/is_server guards just above the
    mention-log append)."""

    def _chat_with_sent(self, handle='StingRay'):
        chat = _make_chat(handle)
        chat.sent = []

        async def _fake_send_json(obj):
            chat.sent.append(obj)
        chat._send_json = _fake_send_json
        return chat

    def test_incoming_dm_sets_last_dm_from(self):
        chat = self._chat_with_sent()
        _run(chat._handle_event({
            'type': 'mrc_message',
            'from_user': 'Wanderer',
            'from_site': 'otherbbs',
            'message': '|15* |08(|15Wanderer|08/|14DirectMsg|08) |07hello there',
        }))
        self.assertEqual(chat._last_dm_from, 'Wanderer')

    def test_own_dm_echo_does_not_set_last_dm_from(self):
        chat = self._chat_with_sent()
        _run(chat._handle_event({
            'type': 'mrc_message',
            'from_user': 'StingRay',
            'message': '|15* |08(|15StingRay|08/|14DirectMsg|08) |07to myself',
        }))
        self.assertEqual(chat._last_dm_from, '')

    def test_r_with_no_prior_dm_shows_error_no_wire_traffic(self):
        chat = self._chat_with_sent()
        _run(chat._handle_slash('/r hello'))
        self.assertEqual(chat.sent, [])

    def test_r_no_args_shows_usage(self):
        chat = self._chat_with_sent()
        chat._last_dm_from = 'Wanderer'
        _run(chat._handle_slash('/r'))
        self.assertEqual(chat.sent, [])

    def test_r_replies_to_last_dm_sender(self):
        chat = self._chat_with_sent()
        chat._last_dm_from = 'Wanderer'
        _run(chat._handle_slash('/r good to hear from you'))
        self.assertEqual(len(chat.sent), 1)
        self.assertEqual(chat.sent[0]['type'], 'direct_message')
        self.assertEqual(chat.sent[0]['to_user'], 'Wanderer')
        self.assertEqual(chat.sent[0]['message'], 'good to hear from you')

    def test_full_dm_reply_round_trip(self):
        chat = self._chat_with_sent()
        _run(chat._handle_event({
            'type': 'mrc_message',
            'from_user': 'Wanderer',
            'message': '|15* |08(|15Wanderer|08/|14DirectMsg|08) |07hi',
        }))
        _run(chat._handle_slash('/r hi back'))
        self.assertEqual(chat.sent[0]['to_user'], 'Wanderer')


class MessageLengthTests(unittest.TestCase):
    def test_chat_wire_cap_accounts_for_handle_overhead(self):
        chat = _make_chat('StingRay')
        chat._handle_overhead = 20
        self.assertEqual(chat._chat_wire_cap(), MAX_OUTGOING_CHARS - 20)

    def test_dm_wire_cap_accounts_for_dm_overhead(self):
        chat = _make_chat('StingRay')
        chat._dm_overhead = 30
        self.assertEqual(chat._dm_wire_cap(), MAX_OUTGOING_CHARS - 30)

    def test_wire_cap_has_a_floor_even_with_huge_overhead(self):
        chat = _make_chat('StingRay')
        chat._handle_overhead = 135
        self.assertEqual(chat._chat_wire_cap(), 10)

    def test_joined_event_sets_overhead_from_bridge(self):
        chat = _make_chat('StingRay')
        self.assertEqual(chat._handle_overhead, 0)
        _run(chat._handle_event({
            'type': 'joined', 'handle': 'StingRay', 'room': 'lobby',
            'handle_overhead': 22, 'dm_overhead': 35,
        }))
        self.assertEqual(chat._handle_overhead, 22)
        self.assertEqual(chat._dm_overhead, 35)

    def test_140_char_message_with_realistic_overhead_no_longer_silently_truncated(self):
        # Before the fix: _split_for_wire(colored) used a flat cap of 140,
        # so a 140-char typed message went out as ONE chunk; the bridge
        # then prepended a ~20-char display handle and hard-truncated the
        # total at 140, silently dropping the message's tail.
        # After the fix: the chunk cap itself accounts for that overhead,
        # so the same input correctly becomes two "(1/2)"/"(2/2)" chunks
        # instead of one that gets clipped server-side.
        chat = _make_chat('StingRay')
        chat._handle_overhead = 20
        message = 'x' * 140
        colored = chat._current_color_pipe() + message
        chunks = _split_for_wire(colored, cap=chat._chat_wire_cap())
        self.assertGreater(len(chunks), 1,
            'a 140-char message with 20 chars of handle overhead must be '
            'split into multiple wire chunks, not sent as one that the '
            'bridge would truncate')
        for c in chunks:
            self.assertLessEqual(len(c), chat._chat_wire_cap())
        # Prepending the (fake) 20-char handle overhead to any single chunk
        # must never exceed the bridge's hard 140-char wire limit.
        for c in chunks:
            self.assertLessEqual(len(c) + chat._handle_overhead, MAX_OUTGOING_CHARS)

    def test_short_message_still_sends_as_a_single_chunk(self):
        chat = _make_chat('StingRay')
        chat._handle_overhead = 20
        message = 'just a short message'
        colored = chat._current_color_pipe() + message
        chunks = _split_for_wire(colored, cap=chat._chat_wire_cap())
        self.assertEqual(len(chunks), 1)


class HelpCommandSwapTests(unittest.TestCase):
    """/help and /helpserver were swapped 2026-07-03 per sysop request:
    /help now asks the MRC hub for its own help; /helpserver now shows
    this client's local command reference (previously the other way
    around)."""

    def test_help_sends_helpserver_to_the_hub(self):
        chat = _make_chat('StingRay')
        sent = []

        async def fake_send_json(obj):
            sent.append(obj)
        chat._send_json = fake_send_json

        result = _run(chat._handle_slash('/help'))
        self.assertTrue(result)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0], {'type': 'server_cmd', 'command': 'HELPSERVER'})

    def test_helpserver_shows_local_command_list(self):
        chat = _make_chat('StingRay')
        result = _run(chat._handle_slash('/helpserver'))
        self.assertTrue(result)
        joined = ' '.join(str(x) for x in chat.session.written)
        self.assertIn('Messaging', joined)
        self.assertIn('/mentions', joined)

    def test_h_and_question_mark_aliases_still_show_local_list(self):
        for alias in ('/h', '/?'):
            chat = _make_chat('StingRay')
            _run(chat._handle_slash(alias))
            joined = ' '.join(str(x) for x in chat.session.written)
            self.assertIn('Messaging', joined)


def _visible(s):
    import re as _re
    return _re.sub(r'\x1b\[[0-9;]*m', '', s)


class MentionsAlignmentTests(unittest.TestCase):
    def test_extra_indent_aligns_wrapped_continuation_under_the_body_start(self):
        chat = _make_chat('StingRay')
        chat._split_screen = True
        chat._term_columns = 80
        long_body = 'word ' * 40  # long enough to force wrapping at 80 cols
        _run(chat._emit(long_body, extra_indent='    '))
        lines = [_visible(l) for l in chat._display_lines]
        self.assertGreater(len(lines), 1, 'expected the long body to wrap')
        # First line: "HH:MM " (6) + our manual 4-space indent = 10 chars
        # before content starts.
        self.assertTrue(lines[0][5] == ' ' and lines[0][6:10] == '    ',
                        f'expected 4 spaces after the HH:MM timestamp, got: {lines[0]!r}')
        # Every continuation line must start with the SAME total indent
        # (6 timestamp + 4 extra = 10 spaces) so it lines up under where
        # the first line's actual content began.
        for cont in lines[1:]:
            self.assertEqual(cont[:10], ' ' * 10,
                f'continuation line not aligned under the body text: {cont!r}')

    def test_mentions_command_produces_aligned_two_line_entries(self):
        chat = _make_chat('SyntaxError')
        chat._split_screen = True
        chat._term_columns = 80
        long_body = 'Welcome to MRC SyntaxError! ' * 6
        chat._mention_count = 1
        chat._mention_log.append({
            'time': '14:05', 'room': '#lobby',
            'from': 'johnny5@The_Delta_Quadrant', 'body': long_body,
        })
        _run(chat._handle_slash('/mentions'))
        lines = [_visible(l) for l in chat._display_lines]
        # Find the header line (contains the 'from' field) and confirm the
        # very next lines (the wrapped body) all share one consistent
        # left-indent rather than jumping back to column 0 or some
        # mismatched width.
        header_idx = next(i for i, l in enumerate(lines) if 'johnny5' in l)
        body_lines = []
        for l in lines[header_idx + 1:]:
            if not l.strip() or '(cleared)' in l:
                break
            body_lines.append(l)
        self.assertGreaterEqual(len(body_lines), 2,
            'expected the long mention body to wrap across multiple lines')
        # The first body line legitimately starts with "HH:MM " (digits, not
        # spaces) before the 4-space extra_indent -- only continuation lines
        # are pure spaces up to that same column. What matters is that
        # content starts at the SAME column (10) on every line.
        self.assertTrue(body_lines[0][5] == ' ' and body_lines[0][6:10] == '    ',
            f'first body line prefix wrong: {body_lines[0]!r}')
        for cont in body_lines[1:]:
            self.assertEqual(cont[:10], ' ' * 10,
                f'continuation line not aligned to column 10: {cont!r}')


class TypingColorPersistenceTests(unittest.TestCase):
    def test_joined_event_restores_saved_typing_color(self):
        chat = _make_chat('StingRay')
        self.assertEqual(chat._color_idx, 0)  # default '07'
        target_idx = _COLOR_SEQ.index('12')
        _run(chat._handle_event({
            'type': 'joined', 'handle': 'StingRay', 'room': 'lobby',
            'style': {'typing_color': '12', 'prefix': '', 'suffix': ''},
        }))
        self.assertEqual(chat._color_idx, target_idx)

    def test_joined_event_with_no_style_leaves_default_color(self):
        chat = _make_chat('StingRay')
        _run(chat._handle_event({
            'type': 'joined', 'handle': 'StingRay', 'room': 'lobby',
        }))
        self.assertEqual(chat._color_idx, 0)

    def test_joined_event_with_unknown_color_code_leaves_default(self):
        chat = _make_chat('StingRay')
        _run(chat._handle_event({
            'type': 'joined', 'handle': 'StingRay', 'room': 'lobby',
            'style': {'typing_color': 'not-a-real-code'},
        }))
        self.assertEqual(chat._color_idx, 0)

    def test_cycling_color_sends_set_style_to_persist_it(self):
        chat = _make_chat('StingRay')
        chat._split_screen = False
        sent = []

        async def fake_send_json(obj):
            sent.append(obj)
        chat._send_json = fake_send_json

        _run(chat._cycle_color(1))
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]['type'], 'set_style')
        self.assertEqual(sent[0]['typing_color'], _COLOR_SEQ[chat._color_idx])

    def test_cycling_color_preserves_prefix_and_suffix(self):
        # Regression test for a real bug found during the MRC Phase E
        # work: _cycle_color used to send only {'type': 'set_style',
        # 'typing_color': code} -- but the bridge's _handle_set_style
        # (mrc/bridge/main.py) hard-defaults prefix/suffix to '' when
        # the field is simply absent from the request, unlike every
        # other style field (which correctly falls back to the
        # existing session value). That silently wiped any prefix/
        # suffix decoration the user had set via the web style panel,
        # every single time they cycled their outgoing color with the
        # arrow keys. Fixed via _style_payload(), which always sends
        # the full last-known style with just the changed field(s)
        # overridden -- see also _apply_style, which keeps self._style
        # in sync from 'joined'/'style_updated' events.
        chat = _make_chat('StingRay')
        chat._split_screen = False
        chat._style = {
            'prefix': '!', 'suffix': '|12>', 'color': '05',
            'prefix_color': '05', 'handle_color': '05',
            'suffix_color': '05', 'typing_color': '07',
        }
        sent = []

        async def fake_send_json(obj):
            sent.append(obj)
        chat._send_json = fake_send_json

        _run(chat._cycle_color(-1))
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]['type'], 'set_style')
        self.assertEqual(sent[0]['prefix'], '!')
        self.assertEqual(sent[0]['suffix'], '|12>')
        self.assertEqual(sent[0]['typing_color'], _COLOR_SEQ[chat._color_idx])


_ROOM_USERS = ['<bby', 'Rixter', 'phigan', 'johnny5', 'Sulf', 'Firehawke',
              'drmad', 'StingRay', 'Winzlo', 'SyntaxError']


class TabCompleteTests(unittest.TestCase):
    def _typed(self, chat, text):
        chat._input_buf = list(text)

    def test_reported_repro_sta_yields_no_match_not_everyone(self):
        # Jerry's exact report: typed "sta", hit Tab, and every user in the
        # room got dumped. None of _ROOM_USERS actually start with "sta"
        # (StingRay starts "sti", SyntaxError starts "syn") -- this test
        # documents that the fixed filtering logic correctly returns
        # zero matches for this exact input, not the whole roster.
        chat = _make_chat('SyntaxError')
        chat._split_screen = False
        chat._known_users = set(_ROOM_USERS)
        self._typed(chat, 'sta')
        _run(chat._tab_complete())
        joined = ' '.join(str(x) for x in chat.session.written)
        self.assertIn('no match', joined.lower())
        for u in _ROOM_USERS:
            self.assertNotIn(u, joined)

    def test_zero_matches_gives_explicit_feedback_not_silence(self):
        chat = _make_chat('SyntaxError')
        chat._split_screen = False
        chat._known_users = {'Alice', 'Bob'}
        self._typed(chat, 'zzz')
        _run(chat._tab_complete())
        joined = ' '.join(str(x) for x in chat.session.written)
        self.assertTrue(joined.strip(), 'expected some feedback, got nothing')
        self.assertIn('no match', joined.lower())

    def test_single_match_completes_and_inserts_colon(self):
        chat = _make_chat('SyntaxError')
        chat._split_screen = False
        chat._known_users = {'StingRay', 'Sulf'}
        self._typed(chat, 'Sti')
        _run(chat._tab_complete())
        self.assertEqual(''.join(chat._input_buf), 'StingRay: ')

    def test_single_match_mid_line_gets_space_not_colon(self):
        chat = _make_chat('SyntaxError')
        chat._split_screen = False
        chat._known_users = {'StingRay', 'Sulf'}
        self._typed(chat, 'hey Sti')
        _run(chat._tab_complete())
        self.assertEqual(''.join(chat._input_buf), 'hey StingRay ')

    def test_multiple_matches_with_common_prefix_extends_without_emitting(self):
        chat = _make_chat('SyntaxError')
        chat._split_screen = False
        chat._known_users = {'Sulf', 'Sulfuric'}
        self._typed(chat, 'Su')
        _run(chat._tab_complete())
        self.assertEqual(''.join(chat._input_buf), 'Sulf')
        self.assertEqual(chat.session.written, [])

    def test_multiple_matches_no_common_extension_lists_candidates(self):
        chat = _make_chat('SyntaxError')
        chat._split_screen = False
        chat._known_users = {'Sulf', 'Sam'}
        self._typed(chat, 'S')
        _run(chat._tab_complete())
        joined = ' '.join(str(x) for x in chat.session.written)
        self.assertIn('Sulf', joined)
        self.assertIn('Sam', joined)

    def test_many_candidates_get_capped_with_a_more_hint(self):
        chat = _make_chat('SyntaxError')
        chat._split_screen = False
        # Each name diverges right after 'S' so the common-prefix-extension
        # branch can't fire (it would just extend to 'S', which is what's
        # already typed) -- forces the multi-candidate listing branch.
        import string
        chat._known_users = {f'S{letter}user' for letter in string.ascii_lowercase[:20]}
        self._typed(chat, 'S')
        _run(chat._tab_complete())
        joined = ' '.join(str(x) for x in chat.session.written)
        shown = joined.count('user')
        self.assertLessEqual(shown, MRCChat._TAB_MATCH_DISPLAY_CAP)
        self.assertIn('more', joined.lower())

    def test_empty_buffer_does_nothing(self):
        chat = _make_chat('SyntaxError')
        chat._split_screen = False
        chat._known_users = set(_ROOM_USERS)
        self._typed(chat, '')
        _run(chat._tab_complete())
        self.assertEqual(chat.session.written, [])


class TabCompleteDeadlockRegressionTests(unittest.TestCase):
    """Hit this for real in production 2026-07-04: an earlier version of
    _tab_complete() wrapped its entire body in `async with
    self._input_lock:`, but the no-match/multi-match feedback paths call
    _emit(), which -- ONLY when _split_screen is True -- calls
    _redraw_chat_area(), which itself acquires self._input_lock.
    asyncio.Lock is not reentrant, so that's a guaranteed deadlock the
    moment Tab hit either of those paths in a real (split-screen) session.
    The other TabCompleteTests in this file all use _split_screen = False,
    which makes _emit() take an early-return that never touches the lock
    at all -- which is exactly why they didn't catch this. These tests use
    _split_screen = True (the real, live-session configuration) and a hard
    timeout so a reintroduced deadlock fails the test suite immediately
    instead of hanging it forever."""

    async def _with_timeout(self, coro, seconds=3):
        await asyncio.wait_for(coro, timeout=seconds)

    def test_no_match_path_does_not_deadlock_in_split_screen_mode(self):
        chat = _make_chat('SyntaxError')
        chat._split_screen = True
        chat._known_users = {'Alice', 'Bob'}
        chat._input_buf = list('zzz')
        _run(self._with_timeout(chat._tab_complete()))

    def test_multi_match_path_does_not_deadlock_in_split_screen_mode(self):
        chat = _make_chat('SyntaxError')
        chat._split_screen = True
        chat._known_users = {'Sam', 'Sulf'}
        chat._input_buf = list('S')
        _run(self._with_timeout(chat._tab_complete()))

    def test_capped_multi_match_path_does_not_deadlock_in_split_screen_mode(self):
        chat = _make_chat('SyntaxError')
        chat._split_screen = True
        import string
        chat._known_users = {f'S{letter}user' for letter in string.ascii_lowercase[:20]}
        chat._input_buf = list('S')
        _run(self._with_timeout(chat._tab_complete()))


class WelcomeEventCoverageTests(unittest.TestCase):
    """MRC Phase F review item: does the terminal have any equivalent of
    the reference client's first-connect welcome screen? Answer: yes,
    already -- the bridge sends a real 'welcome' event on WS connect
    (mrc/bridge/main.py: {"type": "welcome", "message": "Connected to
    MRC Bridge", ...}), which the web client handles explicitly
    (mrc/index.html's `case 'welcome':`) and the terminal client
    surfaces through its generic unknown-event fallback (the final
    `if body:` branch in _handle_event) since 'welcome' isn't in any of
    the specific-event-type buckets above it. Confirmed here rather than
    adding a redundant dedicated handler."""

    def test_welcome_event_reaches_the_generic_fallback_display(self):
        chat = _make_chat('StingRay')
        _run(chat._handle_event({
            'type': 'welcome',
            'message': 'Connected to MRC Bridge',
            'server': 'hub.example',
        }))
        joined = '\n'.join(chat.session.written)
        self.assertIn('welcome', joined)
        self.assertIn('Connected to MRC Bridge', joined)


class KnownUsersRosterSourceTests(unittest.TestCase):
    """Reported live 2026-07-04: connected as SyntaxError, saw StingRay
    clearly listed in a /who (WHOON) roster dump, typed 'Sti' and hit Tab
    -- got "no match" both times. Root cause: the WHOON reply is a plain
    hub-formatted cosmetic dump with no structural per-user markers; the
    client's old nick-scraping fallback regex silently failed on every
    line of it (anchored match, WHOON lines start with '*.:', not a name).
    Removed that fallback entirely -- matching both the web MRC client and
    the anetmrc_v1.3.9 reference client, neither of which ever scrapes
    chat/WHOON body text for the roster -- and the bridge now also fires a
    structured USERLIST refresh whenever a client asks for WHOON (see
    test_mrc_bridge_userlist.py), so the roster comes from the same
    reliable source either way."""

    def test_whoon_style_roster_dump_does_not_populate_known_users(self):
        chat = _make_chat('SyntaxError')
        chat._split_screen = False
        whoon_dump = (
            '*.: &   StingRay              {K}  #lobby (MRC)            idle:  1h59m'
        )
        _run(chat._handle_event({
            'type': 'mrc_message', 'from_user': 'SERVER',
            'message': whoon_dump,
        }))
        self.assertNotIn('StingRay', chat._known_users)

    def test_ordinary_chat_sentence_is_not_scraped_as_a_fake_nick(self):
        # The old regex would have extracted "Hello" as a bogus "nick"
        # from a line like this.
        chat = _make_chat('SyntaxError')
        chat._split_screen = False
        _run(chat._handle_event({
            'type': 'mrc_message', 'from_user': 'Wanderer', 'from_site': 'otherbbs',
            'message': '[Wanderer]@otherbbs Hello everyone, how is it going',
        }))
        self.assertNotIn('Hello', chat._known_users)
        # The real sender IS still tracked (structured from_user field,
        # not the regex fallback).
        self.assertIn('Wanderer', chat._known_users)

    def test_userlist_control_message_correctly_populates_known_users(self):
        # Wire format per the MRC protocol spec (USERLIST transaction):
        # comma-separated, no spaces -- "{user1},{user2},...". NOT
        # whitespace-separated. Confirmed against the doc directly and
        # cross-checked against the web client (mrc/index.html
        # tryParseUserListFromServerMessage: `.split(',')`).
        chat = _make_chat('SyntaxError')
        chat._split_screen = False
        _run(chat._handle_event({
            'type': 'mrc_message', 'from_user': 'SERVER',
            'message': 'USERLIST:StingRay,StackFault,SyntaxError',
        }))
        self.assertIn('StingRay', chat._known_users)
        self.assertIn('StackFault', chat._known_users)

    def test_space_separated_userlist_is_not_the_real_wire_format(self):
        # Regression guard for the exact mistake made the first time this
        # was "fixed": a whitespace-separated USERLIST body is NOT what
        # the hub actually sends (comma-separated per spec, confirmed
        # against mrc-dev.txt and the working web client). If this test
        # ever starts passing, it means _known_users picked up the
        # unsplit compound string as a single garbage entry again --
        # exactly the silent failure mode that shipped once already.
        chat = _make_chat('SyntaxError')
        chat._split_screen = False
        _run(chat._handle_event({
            'type': 'mrc_message', 'from_user': 'SERVER',
            'message': 'USERLIST:StingRay StackFault SyntaxError',
        }))
        self.assertNotIn('StingRay', chat._known_users)

    def test_end_to_end_repro_userlist_then_tab_completes_stingray(self):
        chat = _make_chat('SyntaxError')
        chat._split_screen = False
        _run(chat._handle_event({
            'type': 'mrc_message', 'from_user': 'SERVER',
            'message': 'USERLIST:StingRay,StackFault,SyntaxError',
        }))
        chat._input_buf = list('Sti')
        _run(chat._tab_complete())
        self.assertEqual(''.join(chat._input_buf), 'StingRay: ')


if __name__ == '__main__':
    unittest.main()
