"""Regression tests for the new terminal message-board search feature
(BBSMenuUI.search_messages(), anetbbs/features/bbs_ui.py), reached via
list_boards()'s 'S' hotkey -- requested live 2026-09-29 ("I don't think
there is any option to search for messages... need to make ANetBBS
more message/forum/reading friendly").

Covers: matching by subject and by body content, board-level access
control on results (a board a caller can't read must never appear,
even if its posts match), a reply's search result opening the whole
thread (not just that one reply in isolation -- read_thread_v2()
treats whatever id it's given as the thread ROOT), and the no-matches
case.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


def _strip_ansi(text):
    import re
    return re.sub(r'\x1b\[[0-9;]*m', '', text)


class _FakeSession:
    def __init__(self, access_level=10, is_admin=False, keys=None, lines=None):
        self.user = {'id': 1, 'username': 'searchtest', 'access_level': access_level,
                     'is_admin': is_admin}
        self.window_size = (80, 24)
        self.written = []
        self._keys = list(keys or [])
        self._lines = list(lines or [])

    async def write(self, text):
        self.written.append(text)

    async def read_key_arrow(self):
        return self._keys.pop(0) if self._keys else 'Q'

    async def read_line(self, prompt=''):
        if prompt:
            await self.write(prompt)
        return self._lines.pop(0) if self._lines else ''

    def transcript(self):
        return ''.join(self.written)


class MessageBoardSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent
                          / '.message_board_search_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            u = User(username='searchpostauthor', email='spa@example.com',
                    password_hash='x')
            db.session.add(u)
            db.session.commit()
            cls.author_id = u.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def setUp(self):
        from anetbbs.models import db, Board, Post, EchoArea, EchomailMessage
        with self.app.app_context():
            Post.query.delete()
            Board.query.delete()
            EchomailMessage.query.delete()
            EchoArea.query.delete()
            db.session.commit()

    def _ui(self, access_level=10, is_admin=False, keys=None, lines=None):
        from anetbbs.features.bbs_ui import BBSMenuUI
        session = _FakeSession(access_level=access_level, is_admin=is_admin,
                               keys=keys, lines=lines)
        return BBSMenuUI(session), session

    def _patched_app(self):
        return patch('anetbbs.features.bbs_ui._app', return_value=self.app)

    def _make_board(self, name, min_access_level=0):
        from anetbbs.models import db, Board
        with self.app.app_context():
            b = Board(name=name, is_active=True, min_access_level=min_access_level)
            db.session.add(b)
            db.session.commit()
            return b.id

    def _make_post(self, board_id, subject, content, parent_id=None):
        from anetbbs.models import db, Post
        with self.app.app_context():
            p = Post(board_id=board_id, author_id=self.author_id,
                     subject=subject, content=content, parent_id=parent_id)
            db.session.add(p)
            db.session.commit()
            return p.id

    def _make_echo_area(self, tag, min_access_level=0, is_sysop_only=False):
        from anetbbs.models import db, EchomailNetwork, EchoArea
        with self.app.app_context():
            net = EchomailNetwork.query.filter_by(name='SearchTestNet').first()
            if net is None:
                net = EchomailNetwork(name='SearchTestNet', network_type='binkp',
                                      our_address='1:1/1')
                db.session.add(net)
                db.session.commit()
            area = EchoArea(network_id=net.id, tag=tag, name=tag,
                            is_active=True, is_subscribed=True,
                            min_access_level=min_access_level,
                            is_sysop_only=is_sysop_only)
            db.session.add(area)
            db.session.commit()
            return area.id

    def _make_echomail(self, area_id, subject, body, from_name='Someone'):
        from anetbbs.models import db, EchoArea, EchomailMessage
        with self.app.app_context():
            area = EchoArea.query.get(area_id)
            m = EchomailMessage(area_id=area_id, network_id=area.network_id,
                               from_name=from_name, to_name='All',
                               subject=subject, body=body)
            db.session.add(m)
            db.session.commit()
            return m.id

    def test_matches_by_subject(self):
        board_id = self._make_board('General')
        self._make_post(board_id, 'Unique Widget Discussion', 'body text')
        ui, session = self._ui(lines=['widget'], keys=['Q'])
        with self._patched_app():
            asyncio.run(ui.search_messages())
        self.assertIn('Unique Widget Discussion', _strip_ansi(session.transcript()))

    def test_matches_by_body_content(self):
        board_id = self._make_board('General')
        self._make_post(board_id, 'Ordinary Subject', 'a rare gizmo mentioned here')
        ui, session = self._ui(lines=['gizmo'], keys=['Q'])
        with self._patched_app():
            asyncio.run(ui.search_messages())
        self.assertIn('Ordinary Subject', _strip_ansi(session.transcript()))

    def test_no_matches_shows_a_message(self):
        self._make_board('General')
        ui, session = self._ui(lines=['zzz_nothing_matches_this_zzz'], keys=[])
        with self._patched_app():
            asyncio.run(ui.search_messages())
        self.assertIn('No matches', _strip_ansi(session.transcript()))

    def test_blank_search_term_returns_immediately(self):
        ui, session = self._ui(lines=[''])
        with self._patched_app(), \
             patch.object(ui, '_rss_lightbar', new=AsyncMock(
                 side_effect=AssertionError('must not search on a blank term'))):
            asyncio.run(ui.search_messages())

    def test_gated_board_posts_never_appear_for_a_low_level_user(self):
        low_board = self._make_board('Open Board', min_access_level=0)
        high_board = self._make_board('VIP Board', min_access_level=50)
        self._make_post(low_board, 'findme in open', 'body')
        self._make_post(high_board, 'findme in vip', 'body')

        ui, session = self._ui(access_level=10, is_admin=False,
                               lines=['findme'], keys=['Q'])
        with self._patched_app():
            asyncio.run(ui.search_messages())
        text = _strip_ansi(session.transcript())
        self.assertIn('findme in open', text)
        self.assertNotIn('findme in vip', text,
                         'a board above the caller\'s access level must never '
                         'leak a matching post into search results')

    def test_admin_sees_gated_board_matches_too(self):
        high_board = self._make_board('VIP Board', min_access_level=50)
        self._make_post(high_board, 'findme in vip', 'body')

        ui, session = self._ui(access_level=10, is_admin=True,
                               lines=['findme'], keys=['Q'])
        with self._patched_app():
            asyncio.run(ui.search_messages())
        self.assertIn('findme in vip', _strip_ansi(session.transcript()))

    def test_selecting_a_reply_result_opens_the_whole_thread_not_just_the_reply(self):
        board_id = self._make_board('General')
        root_id = self._make_post(board_id, 'Original Post', 'root content')
        self._make_post(board_id, 'Re: Original Post',
                        'unique_reply_marker_text', parent_id=root_id)

        ui, session = self._ui(lines=['unique_reply_marker'], keys=['ENTER'])
        with self._patched_app(), \
             patch.object(ui, 'read_thread_v2', new=AsyncMock()) as mock_read:
            asyncio.run(ui.search_messages())
        mock_read.assert_called_once()
        called_post_id = mock_read.call_args.args[0]
        self.assertEqual(called_post_id, root_id,
                         "a reply's search result must open via the THREAD's "
                         "root post id, not the reply's own id -- "
                         "read_thread_v2() treats whatever id it gets as the root")


    def test_s_hotkey_from_list_boards_reaches_search(self):
        """The real entry point a caller actually uses: list_boards()'s
        'S' hotkey must reach search_messages(), not just the direct
        method call every other test above exercises."""
        self._make_board('General')
        ui, session = self._ui(keys=['S', 'Q'], lines=[''])
        with self._patched_app(), \
             patch.object(ui, 'search_messages', new=AsyncMock()) as mock_search:
            asyncio.run(ui.list_boards())
        mock_search.assert_called_once()

    # ---- echomail extension (added same day, per Jerry's follow-up:
    # "the message search, that is for local board and echomail
    # messages, correct?" -- confirmed he wanted both) ----

    def test_matches_echomail_by_subject(self):
        area_id = self._make_echo_area('GENERAL')
        self._make_echomail(area_id, 'Unique Echomail Subject', 'body text')
        ui, session = self._ui(lines=['Echomail Subject'], keys=['Q'])
        with self._patched_app():
            asyncio.run(ui.search_messages())
        self.assertIn('Unique Echomail Subject', _strip_ansi(session.transcript()))

    def test_matches_echomail_by_body(self):
        area_id = self._make_echo_area('GENERAL')
        self._make_echomail(area_id, 'Ordinary', 'a rare thingamajig mentioned here')
        ui, session = self._ui(lines=['thingamajig'], keys=['Q'])
        with self._patched_app():
            asyncio.run(ui.search_messages())
        self.assertIn('Ordinary', _strip_ansi(session.transcript()))

    def test_gated_echo_area_never_leaks_for_a_low_level_user(self):
        open_area = self._make_echo_area('OPEN', min_access_level=0)
        vip_area = self._make_echo_area('VIP', min_access_level=50)
        self._make_echomail(open_area, 'findme in open echo', 'body')
        self._make_echomail(vip_area, 'findme in vip echo', 'body')

        ui, session = self._ui(access_level=10, is_admin=False,
                               lines=['findme'], keys=['Q'])
        with self._patched_app():
            asyncio.run(ui.search_messages())
        text = _strip_ansi(session.transcript())
        self.assertIn('findme in open echo', text)
        self.assertNotIn('findme in vip echo', text)

    def test_sysop_only_echo_area_never_leaks_for_a_non_admin(self):
        sysop_area = self._make_echo_area('SYSOPCHAT', is_sysop_only=True)
        self._make_echomail(sysop_area, 'findme sysop only echo', 'body')

        ui, session = self._ui(access_level=100, is_admin=False,
                               lines=['findme'], keys=[])
        with self._patched_app():
            asyncio.run(ui.search_messages())
        self.assertIn('No matches', _strip_ansi(session.transcript()),
                      'is_sysop_only must gate even a high access_level '
                      'non-admin user out of the results')

    def test_forged_echomail_headers_are_sanitized_in_results(self):
        area_id = self._make_echo_area('GENERAL')
        evil = '\x1b[2J\x1b[1;1HPWNED'
        self._make_echomail(area_id, f'findme{evil}subject', 'body',
                            from_name=f'Evil{evil}Sender')
        ui, session = self._ui(lines=['findme'], keys=['Q'])
        with self._patched_app():
            asyncio.run(ui.search_messages())
        transcript = session.transcript()
        self.assertNotIn(evil, transcript)
        self.assertIn('findme', transcript)
        self.assertIn('Evil', transcript)

    def test_selecting_an_echo_result_opens_read_echo_area_at_that_message(self):
        area_id = self._make_echo_area('GENERAL')
        msg_id = self._make_echomail(area_id, 'findme echo target', 'body')
        ui, session = self._ui(lines=['findme'], keys=['ENTER'])
        with self._patched_app(), \
             patch.object(ui, 'read_echo_area', new=AsyncMock()) as mock_read:
            asyncio.run(ui.search_messages())
        mock_read.assert_called_once()
        self.assertEqual(mock_read.call_args.args[0], area_id)
        self.assertEqual(mock_read.call_args.kwargs.get('jump_to_msg_id'), msg_id)

    def test_board_and_echo_results_can_appear_together(self):
        board_id = self._make_board('General')
        self._make_post(board_id, 'findme board hit', 'body')
        area_id = self._make_echo_area('GENERAL')
        self._make_echomail(area_id, 'findme echo hit', 'body')

        ui, session = self._ui(lines=['findme'], keys=['Q'])
        with self._patched_app():
            asyncio.run(ui.search_messages())
        text = _strip_ansi(session.transcript())
        self.assertIn('findme board hit', text)
        self.assertIn('findme echo hit', text)


if __name__ == '__main__':
    unittest.main()
