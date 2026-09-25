"""Regression tests for two real gaps reported live (2026-09-25):

1. Pressing Q used to quit instantly and unconditionally, with no
   confirmation -- a single mistyped key (the reported case: meaning to
   press W) silently ended the whole session. Fixed with a Y/N confirm
   prompt (_render_confirm_quit_overlay); Y still saves/leaves-MP exactly
   as before, but run() now loops back to the main menu afterward
   instead of ending the door session outright.

2. There was no in-game help at all -- '?' now opens a paginated,
   detailed help overlay (HELP_PAGES).

A real bug was caught and fixed while building #1: the confirm-quit
handler originally duplicated _play_session()'s finally: cleanup
(save-or-leave-MP), which would have either double-saved or, worse,
left self._mp_mode still True going into the NEXT lobby round (since
_mp_leave() itself never resets it) -- meaning a player who quit out of
multiplayer and then started a fresh single-player game would have had
that new game silently treated as still being in multiplayer. Fixed by
having the confirm-quit handler only ever set self.running = False and
letting the existing finally: block (now also resetting _mp_mode) do
the one correct cleanup. Covered here directly, not just observed via
manual PTY testing.
"""
import asyncio
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.features.anetcraft as anetcraft_mod
from anetbbs.features.anetcraft import ANetCraft, HELP_PAGES


class _NullSession:
    """_play_session()'s finally: block writes a final SHOW+CLS to the
    session -- the other test files in this suite never run() far enough
    to hit that, so session=None has always been fine for them. These
    tests do, so they need something with a real async write()."""
    async def write(self, _text):
        pass


class AnetcraftHelpPageSizingTests(unittest.TestCase):
    """HELP_PAGES content is hand-authored with a hard layout budget
    (_render_help_overlay's box is 74 wide / fits 15 content lines) --
    verified here rather than trusted by eye, the same discipline this
    session's lobby-box redesign (the OpenDoors door side) used."""

    def test_every_page_fits_the_overlay_box(self):
        for title, lines in HELP_PAGES:
            self.assertLessEqual(len(lines), 15,
                                 f'page {title!r} has {len(lines)} lines, '
                                 'overflows the help box')
            for line in lines:
                self.assertLessEqual(len(line), 70,
                                     f'page {title!r} has an over-width line: '
                                     f'{line!r}')

    def test_at_least_one_page_and_all_titled(self):
        self.assertGreater(len(HELP_PAGES), 0)
        for title, lines in HELP_PAGES:
            self.assertTrue(title)
            self.assertIsInstance(lines, list)


class AnetcraftQuitConfirmTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig_save_dir = anetcraft_mod.SAVE_DIR
        anetcraft_mod.SAVE_DIR = Path(self._tmp)

    def tearDown(self):
        anetcraft_mod.SAVE_DIR = self._orig_save_dir
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_q_opens_confirm_prompt_instead_of_quitting_immediately(self):
        g = ANetCraft(session=None, username='alice')
        g._new_world('survival')
        g.running = True
        g._handle_key('q')
        self.assertEqual(g.mode, 'confirm_quit')
        self.assertTrue(g.running, 'Q must not quit immediately anymore')

    def test_n_cancels_and_resumes_play(self):
        g = ANetCraft(session=None, username='alice')
        g._new_world('survival')
        g.running = True
        g._handle_key('q')
        g._handle_key('n')
        self.assertEqual(g.mode, 'game')
        self.assertTrue(g.running)

    def test_esc_also_cancels(self):
        g = ANetCraft(session=None, username='alice')
        g._new_world('survival')
        g.running = True
        g._handle_key('q')
        g._handle_key('ESC')
        self.assertEqual(g.mode, 'game')
        self.assertTrue(g.running)

    def test_y_stops_the_loop_but_does_not_itself_save_or_leave_mp(self):
        """The regression this session caught: the handler must NOT
        duplicate _play_session()'s finally: cleanup -- only set
        running = False and let that one cleanup path run."""
        g = ANetCraft(session=None, username='alice')
        g._new_world('survival')
        g.running = True
        g._mp_mode = True   # simulate being mid-multiplayer
        g._handle_key('q')
        g._handle_key('y')
        self.assertFalse(g.running)
        # Still True here -- only _play_session()'s finally: resets it,
        # proving the handler itself didn't short-circuit that cleanup.
        self.assertTrue(g._mp_mode)

    def test_single_player_quit_confirm_saves_via_play_session_finally(self):
        g = ANetCraft(session=_NullSession(), username='bob')
        g._new_world('survival')
        g.player.hp = 11
        g.running = False   # skip straight to finally: (see module docstring)
        asyncio.run(g._play_session())
        self.assertTrue(g._save_path().exists())

        reloaded = ANetCraft(session=None, username='bob')
        self.assertTrue(reloaded.load())
        self.assertEqual(reloaded.player.hp, 11)

    def test_mp_quit_confirm_resets_mp_mode_for_the_next_lobby_round(self):
        """The actual regression: without this reset, choosing Continue
        or New Survival right after leaving multiplayer would silently
        still be treated as a multiplayer session."""
        g = ANetCraft(session=_NullSession(), username='carol')
        g._mp_join()
        self.assertTrue(g._mp_mode)
        g.running = False
        asyncio.run(g._play_session())
        self.assertFalse(g._mp_mode,
                         '_mp_mode must be reset after leaving multiplayer, '
                         'or the next lobby round inherits it incorrectly')


class AnetcraftHelpToggleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig_save_dir = anetcraft_mod.SAVE_DIR
        anetcraft_mod.SAVE_DIR = Path(self._tmp)

    def tearDown(self):
        anetcraft_mod.SAVE_DIR = self._orig_save_dir
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_question_mark_opens_and_closes_help(self):
        g = ANetCraft(session=None, username='alice')
        g._new_world('survival')
        g._handle_key('?')
        self.assertEqual(g.mode, 'help')
        self.assertEqual(g.help_page, 0)
        g._handle_key('?')
        self.assertEqual(g.mode, 'game')

    def test_paging_is_clamped_to_valid_range(self):
        g = ANetCraft(session=None, username='alice')
        g._new_world('survival')
        g._handle_key('?')
        g._handle_key('LEFT')   # already on page 0 -- must not go negative
        self.assertEqual(g.help_page, 0)
        for _ in range(len(HELP_PAGES) + 5):   # overshoot past the last page
            g._handle_key('RIGHT')
        self.assertEqual(g.help_page, len(HELP_PAGES) - 1)

    def test_help_overlay_renders_the_current_page_title(self):
        g = ANetCraft(session=None, username='alice')
        g._new_world('survival')
        g._handle_key('?')
        text = g._render_help_overlay()
        self.assertIn(HELP_PAGES[0][0], text)


if __name__ == '__main__':
    unittest.main()
