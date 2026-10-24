"""Tests for the file-area category/sub-menu feature -- added because
file areas had NO grouping concept at all until a sysop pointed out the
gap ("file areas - i just realized we dont have sub-menus"). Mirrors
the already-shipped Game/GameCategory stable-numbering + drag-and-drop
+ self-healing feature (see tests/test_door_games_stable_numbering.py)
as closely as the file-area domain allows -- same model shape
(FileAreaCategory mirrors GameCategory: name/slug/sort_order/
as_submenu), same admin reorder/renumber routes, same boot-time
collision self-heal.
"""
import asyncio
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


def _strip_ansi(text):
    return re.sub(r'\x1b\[[0-9;]*m', '', text)


def _fresh_app(db_path):
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    os.environ['FLASK_ENV'] = 'testing'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    return app


class FileAreaCategoryModelTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        from anetbbs.models import db
        self._ctx = self.app.app_context()
        self._ctx.push()
        self.addCleanup(self._ctx.pop)
        db.create_all()

    def test_file_area_has_category_and_sort_order_columns(self):
        from anetbbs.models import db, FileArea
        fa = FileArea(tag='TEST.ONE', name='Test One', category='games', sort_order=5)
        db.session.add(fa)
        db.session.commit()
        got = FileArea.query.filter_by(tag='TEST.ONE').first()
        self.assertEqual(got.category, 'games')
        self.assertEqual(got.sort_order, 5)

    def test_file_area_category_defaults(self):
        from anetbbs.models import db, FileArea
        fa = FileArea(tag='TEST.TWO', name='Test Two')
        db.session.add(fa)
        db.session.commit()
        got = FileArea.query.filter_by(tag='TEST.TWO').first()
        self.assertEqual(got.category, 'other')
        self.assertEqual(got.sort_order, 0)

    def test_file_area_category_model_round_trips(self):
        from anetbbs.models import db, FileAreaCategory
        cat = FileAreaCategory(name='Games', slug='games-test-cat',
                               sort_order=3, as_submenu=True)
        db.session.add(cat)
        db.session.commit()
        got = FileAreaCategory.query.filter_by(slug='games-test-cat').first()
        self.assertEqual(got.name, 'Games')
        self.assertTrue(got.as_submenu)


class FileAreaCategoryAdminTests(unittest.TestCase):
    """Admin CRUD + drag-and-drop reorder + one-click renumber --
    mirrors games_admin.py's own category route tests."""

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.file_area_cat_admin_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()
            admin = User(username='fileareacatadmin',
                        email='fileareacatadmin@example.com',
                        password_hash='x', is_admin=True, access_level=100)
            db.session.add(admin)
            db.session.commit()
            cls.admin_id = admin.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True
        return client

    def test_add_edit_delete_category_round_trip(self):
        from anetbbs.models import FileAreaCategory
        client = self._client()
        resp = client.post('/admin/file-areas/categories/add', data={
            'name': 'Games', 'slug': 'crud-games', 'sort_order': '1',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            cat = FileAreaCategory.query.filter_by(slug='crud-games').first()
            self.assertIsNotNone(cat)
            cat_id = cat.id

        resp = client.post(f'/admin/file-areas/categories/{cat_id}/edit', data={
            'name': 'Games Renamed', 'slug': 'crud-games', 'sort_order': '2',
            'as_submenu': 'y',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            cat_after = FileAreaCategory.query.filter_by(slug='crud-games').first()
            self.assertEqual(cat_after.name, 'Games Renamed')
            self.assertTrue(cat_after.as_submenu)

        resp = client.post(f'/admin/file-areas/categories/{cat_id}/delete',
                           follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            self.assertIsNone(FileAreaCategory.query.filter_by(slug='crud-games').first())

    def test_reorder_categories_writes_clean_sort_order_in_posted_order(self):
        """Only as_submenu=True categories with at least one visible
        active area participate -- same policy as reorder_categories()
        for games."""
        from anetbbs.models import db, FileArea, FileAreaCategory
        with self.app.app_context():
            c1 = FileAreaCategory(name='Reorder Cat One', slug='fa-reorder-cat-one',
                                  as_submenu=True, sort_order=1)
            c2 = FileAreaCategory(name='Reorder Cat Two', slug='fa-reorder-cat-two',
                                  as_submenu=True, sort_order=2)
            db.session.add_all([c1, c2])
            db.session.add_all([
                FileArea(tag='FA.REORDER.ONE', name='One', category='fa-reorder-cat-one',
                        is_active=True),
                FileArea(tag='FA.REORDER.TWO', name='Two', category='fa-reorder-cat-two',
                        is_active=True),
            ])
            db.session.commit()
            c1_id, c2_id = c1.id, c2.id

        client = self._client()
        resp = client.post('/admin/file-areas/categories/reorder',
                           json={'order': [c2_id, c1_id]})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()['ok'])

        with self.app.app_context():
            c1b = FileAreaCategory.query.get(c1_id)
            c2b = FileAreaCategory.query.get(c2_id)
            self.assertLess(c2b.sort_order, c1b.sort_order)

    def test_reorder_categories_excludes_inline_categories(self):
        from anetbbs.models import db, FileArea, FileAreaCategory
        with self.app.app_context():
            inline_cat = FileAreaCategory(name='Inline Dragged', slug='fa-inline-dragged',
                                          as_submenu=False, sort_order=99)
            sub_a = FileAreaCategory(name='Drag Sub A', slug='fa-drag-sub-a',
                                     as_submenu=True, sort_order=1)
            db.session.add_all([inline_cat, sub_a])
            db.session.add(FileArea(tag='FA.DRAGSUBA', name='A', category='fa-drag-sub-a',
                                    is_active=True))
            db.session.commit()
            inline_id, sub_a_id = inline_cat.id, sub_a.id

        client = self._client()
        resp = client.post('/admin/file-areas/categories/reorder',
                           json={'order': [inline_id, sub_a_id]})
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            inline_after = FileAreaCategory.query.get(inline_id)
            self.assertEqual(inline_after.sort_order, 99,
                            msg="inline category's sort_order must be left untouched")

    def test_renumber_categories_endpoint(self):
        from anetbbs.models import db, FileArea, FileAreaCategory
        with self.app.app_context():
            db.session.add_all([
                FileAreaCategory(name='Cat One', slug='fa-renumber-cat-one',
                                 as_submenu=True, sort_order=5),
                FileAreaCategory(name='Cat Two', slug='fa-renumber-cat-two',
                                 as_submenu=True, sort_order=5),
            ])
            db.session.add_all([
                FileArea(tag='FA.RNONE', name='One', category='fa-renumber-cat-one',
                        is_active=True),
                FileArea(tag='FA.RNTWO', name='Two', category='fa-renumber-cat-two',
                        is_active=True),
            ])
            db.session.commit()
        client = self._client()
        resp = client.post('/admin/file-areas/categories/renumber', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            c1 = FileAreaCategory.query.filter_by(slug='fa-renumber-cat-one').first()
            c2 = FileAreaCategory.query.filter_by(slug='fa-renumber-cat-two').first()
            self.assertNotEqual(c1.sort_order, c2.sort_order)

    def test_reorder_areas_writes_gapped_sort_order_in_posted_order(self):
        from anetbbs.models import db, FileArea
        with self.app.app_context():
            a = FileArea(tag='FA.ORDA', name='A', is_active=True, sort_order=1)
            b = FileArea(tag='FA.ORDB', name='B', is_active=True, sort_order=2)
            db.session.add_all([a, b])
            db.session.commit()
            a_id, b_id = a.id, b.id

        client = self._client()
        resp = client.post('/admin/file-areas/reorder', json={'order': [b_id, a_id]})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()['ok'])
        with self.app.app_context():
            a2 = FileArea.query.get(a_id)
            b2 = FileArea.query.get(b_id)
            self.assertLess(b2.sort_order, a2.sort_order)

    def test_renumber_areas_scoped_to_one_category_leaves_others_untouched(self):
        from anetbbs.models import db, FileArea
        with self.app.app_context():
            db.session.add_all([
                FileArea(tag='FA.SCOPEDONE', name='One', category='fa-scoped-cat',
                        is_active=True, sort_order=0),
                FileArea(tag='FA.SCOPEDTWO', name='Two', category='fa-scoped-cat',
                        is_active=True, sort_order=0),
                FileArea(tag='FA.SCOPEDUNRELATED', name='Unrelated', category='other',
                        is_active=True, sort_order=777),
            ])
            db.session.commit()

        client = self._client()
        resp = client.post('/admin/file-areas/renumber',
                           data={'category': 'fa-scoped-cat'}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            one = FileArea.query.filter_by(tag='FA.SCOPEDONE').first()
            two = FileArea.query.filter_by(tag='FA.SCOPEDTWO').first()
            unrelated = FileArea.query.filter_by(tag='FA.SCOPEDUNRELATED').first()
            self.assertNotEqual(one.sort_order, two.sort_order)
            self.assertEqual(unrelated.sort_order, 777)

    def test_renumber_areas_in_a_submenu_category_assigns_clean_sequence(self):
        """Real bug found alongside the identical one in
        games_admin.py's renumber_games(): renumber_file_areas() used
        to assign the GAPPED 10/20/30/... sequence unconditionally,
        even for areas inside an as_submenu=True category --
        contradicting resolve_file_area_sort_order_collisions()'s own
        every-boot policy of a clean 1/2/3/... run for exactly those
        areas."""
        from anetbbs.models import db, FileArea, FileAreaCategory
        with self.app.app_context():
            db.session.add(FileAreaCategory(name='Renumber Submenu Cat',
                                            slug='fa-renumber-submenu-cat', as_submenu=True))
            db.session.add_all([
                FileArea(tag='FA.SUBONE', name='Sub One', category='fa-renumber-submenu-cat',
                        is_active=True, sort_order=50),
                FileArea(tag='FA.SUBTWO', name='Sub Two', category='fa-renumber-submenu-cat',
                        is_active=True, sort_order=20),
            ])
            db.session.commit()

        client = self._client()
        resp = client.post('/admin/file-areas/renumber',
                           data={'category': 'fa-renumber-submenu-cat'}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            two = FileArea.query.filter_by(tag='FA.SUBTWO').first()
            one = FileArea.query.filter_by(tag='FA.SUBONE').first()
            self.assertEqual(two.sort_order, 1,
                            msg='lower pre-existing sort_order (20) must come first')
            self.assertEqual(one.sort_order, 2,
                            msg='submenu areas must get a clean 1/2/3/... run, '
                                'not the gapped 10/20/30/... scheme')

    def test_reorder_areas_in_a_submenu_category_writes_clean_sequence(self):
        """Same bug, for the drag-and-drop endpoint instead of the
        one-click renumber button."""
        from anetbbs.models import db, FileArea, FileAreaCategory
        with self.app.app_context():
            db.session.add(FileAreaCategory(name='Reorder Submenu Cat',
                                            slug='fa-reorder-submenu-cat', as_submenu=True))
            a = FileArea(tag='FA.DRAGA', name='Drag A', category='fa-reorder-submenu-cat',
                        is_active=True, sort_order=1)
            b = FileArea(tag='FA.DRAGB', name='Drag B', category='fa-reorder-submenu-cat',
                        is_active=True, sort_order=2)
            db.session.add_all([a, b])
            db.session.commit()
            a_id, b_id = a.id, b.id

        client = self._client()
        resp = client.post('/admin/file-areas/reorder', json={'order': [b_id, a_id]})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()['ok'])

        with self.app.app_context():
            a2 = FileArea.query.get(a_id)
            b2 = FileArea.query.get(b_id)
            self.assertEqual(b2.sort_order, 1,
                            msg='B was posted first and must get the clean value 1')
            self.assertEqual(a2.sort_order, 2,
                            msg='submenu areas must get a clean 1/2/3/... run, '
                                'not the gapped 10/20/30/... scheme')


class ResolveFileAreaSortOrderCollisionsTests(unittest.TestCase):
    """Boot-time self-heal -- mirrors
    test_door_games_stable_numbering.py's ResolveSortOrderCollisionsTests
    exactly, scoped to FileArea/FileAreaCategory."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        from anetbbs.models import db
        self._ctx = self.app.app_context()
        self._ctx.push()
        self.addCleanup(self._ctx.pop)
        db.create_all()

    def test_areas_sharing_zero_sort_order_in_a_submenu_category_get_resolved(self):
        from anetbbs.models import db, FileArea, FileAreaCategory
        from anetbbs.features.file_areas import resolve_file_area_sort_order_collisions
        db.session.add(FileAreaCategory(name='Collide Cat', slug='fa-collide-cat',
                                        as_submenu=True))
        db.session.add_all([
            FileArea(tag='FA.COLLIDEA', name='A', category='fa-collide-cat',
                    is_active=True, sort_order=0),
            FileArea(tag='FA.COLLIDEB', name='B', category='fa-collide-cat',
                    is_active=True, sort_order=0),
            FileArea(tag='FA.COLLIDEC', name='C', category='fa-collide-cat',
                    is_active=True, sort_order=0),
        ])
        db.session.commit()

        changed = resolve_file_area_sort_order_collisions()
        self.assertTrue(changed)

        orders = {a.sort_order for a in
                 FileArea.query.filter_by(category='fa-collide-cat').all()}
        self.assertEqual(orders, {1, 2, 3})

    def test_inline_area_collision_only_bumps_duplicates(self):
        """Areas NOT in a submenu category keep the conservative
        bump-the-duplicates fix (a top-level area's own number could be
        hardcoded into door/menu art the same way a game's can).
        Deliberately NOT category='other' -- create_app('testing') also
        seeds ~16 real bundled default file areas, all also defaulting
        to category='other' with sort_order=0, which collide with each
        other regardless of this test's own area and would make
        `changed` True for an unrelated reason."""
        from anetbbs.models import db, FileArea
        from anetbbs.features.file_areas import resolve_file_area_sort_order_collisions
        db.session.add(FileArea(tag='FA.UNIQUE', name='Unique',
                               category='fa-isolated-unique-test-cat',
                               is_active=True, sort_order=50))
        db.session.commit()

        changed = resolve_file_area_sort_order_collisions()
        self.assertFalse(changed)
        got = FileArea.query.filter_by(tag='FA.UNIQUE').first()
        self.assertEqual(got.sort_order, 50)

    def test_populated_submenu_categories_resequence_to_start_at_one(self):
        from anetbbs.models import db, FileArea, FileAreaCategory
        from anetbbs.features.file_areas import resolve_file_area_sort_order_collisions
        db.session.add_all([
            FileAreaCategory(name='Gappy A', slug='fa-gappy-cat-a',
                             as_submenu=True, sort_order=20),
            FileAreaCategory(name='Gappy B', slug='fa-gappy-cat-b',
                             as_submenu=True, sort_order=70),
        ])
        db.session.add_all([
            FileArea(tag='FA.GAPPYA', name='A', category='fa-gappy-cat-a', is_active=True),
            FileArea(tag='FA.GAPPYB', name='B', category='fa-gappy-cat-b', is_active=True),
        ])
        db.session.commit()

        changed = resolve_file_area_sort_order_collisions()
        self.assertTrue(changed)

        a = FileAreaCategory.query.filter_by(slug='fa-gappy-cat-a').first()
        b = FileAreaCategory.query.filter_by(slug='fa-gappy-cat-b').first()
        self.assertEqual(a.sort_order, 1)
        self.assertEqual(b.sort_order, 2)

    def test_empty_submenu_category_is_never_assigned_a_number(self):
        from anetbbs.models import db, FileAreaCategory
        from anetbbs.features.file_areas import resolve_file_area_sort_order_collisions
        db.session.add(FileAreaCategory(name='Empty Cat', slug='fa-empty-cat',
                                        as_submenu=True, sort_order=42))
        db.session.commit()

        changed = resolve_file_area_sort_order_collisions()
        self.assertFalse(changed)
        c = FileAreaCategory.query.filter_by(slug='fa-empty-cat').first()
        self.assertEqual(c.sort_order, 42)

    def test_inline_categories_are_never_touched(self):
        from anetbbs.models import db, FileAreaCategory
        from anetbbs.features.file_areas import resolve_file_area_sort_order_collisions
        db.session.add_all([
            FileAreaCategory(name='Inline Collide A', slug='fa-inline-collide-a',
                             as_submenu=False, sort_order=3),
            FileAreaCategory(name='Inline Collide B', slug='fa-inline-collide-b',
                             as_submenu=False, sort_order=3),
        ])
        db.session.commit()

        resolve_file_area_sort_order_collisions()

        a = FileAreaCategory.query.filter_by(slug='fa-inline-collide-a').first()
        b = FileAreaCategory.query.filter_by(slug='fa-inline-collide-b').first()
        self.assertEqual(a.sort_order, 3)
        self.assertEqual(b.sort_order, 3)


class AnsiTerminalCategoryDrillDownTests(unittest.TestCase):
    """ANSI lightbar drill-down for an as_submenu=True file-area
    category -- same two-level shape as features/games.py's own
    as_submenu door categories, adapted to the lightbar widget
    file-area browsing already uses."""

    class _FakeSession:
        def __init__(self, keys):
            self.user = {'id': 1, 'username': 'testuser', 'access_level': 100,
                         'is_admin': True}
            self.written = []
            self._keys = list(keys)

        async def write(self, text):
            self.written.append(text)

        async def read_key_arrow(self):
            return self._keys.pop(0) if self._keys else 'Q'

        async def read_line(self, prompt=''):
            return ''

        def transcript(self):
            return ''.join(self.written)

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.file_area_ansi_drill_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        from anetbbs.models import db, FileArea, FileAreaCategory
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            db.session.add(FileAreaCategory(
                name='Drill Games', slug='ansi-drill-games',
                as_submenu=True, sort_order=1))
            db.session.add(FileArea(
                tag='ANSI.DRILL.ONE', name='Drill Area One',
                category='ansi-drill-games', is_active=True,
                is_sysop_only=False, min_access_level=10,
                upload_permission='users', storage_path=''))
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_submenu_category_shows_as_one_collapsed_row(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        from unittest.mock import patch

        session = self._FakeSession(keys=['Q'])
        ui = BBSMenuUI(session)
        with patch('anetbbs.features.bbs_ui._app', return_value=self.app):
            asyncio.run(ui.list_files())
        txt = _strip_ansi(session.transcript())
        self.assertIn('Drill Games', txt)
        self.assertNotIn('Drill Area One', txt,
                         msg='the individual area must be hidden behind the '
                             'collapsed category row, not listed inline')

    def test_entering_the_category_reveals_its_area(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        from unittest.mock import patch, AsyncMock

        session = self._FakeSession(keys=['ENTER'])
        ui = BBSMenuUI(session)
        with patch('anetbbs.features.bbs_ui._app', return_value=self.app), \
             patch.object(ui, '_file_area_browse', new=AsyncMock()) as mock_browse:
            asyncio.run(ui._file_area_category_browse(
                'Drill Games',
                [{'id': 1, 'name': 'Drill Area One', 'tag': 'ANSI.DRILL.ONE',
                 'cnt': 0, 'inactive': False, 'sysop_only': False,
                 'can_upload': True, 'storage_path': '', 'category': 'ansi-drill-games'}],
                '/tmp', 'http://localhost', []))
        mock_browse.assert_called_once()
        self.assertEqual(mock_browse.call_args.args[1], 'Drill Area One')


class PetsciiTerminalCategoryDrillDownTests(unittest.TestCase):
    """PETSCII paginated-picker drill-down for an as_submenu=True
    file-area category -- same shape as the ANSI lightbar version
    above, adapted to PETSCII's numbered-picker widget."""

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.file_area_petscii_drill_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        from anetbbs.models import db, FileArea, FileAreaCategory
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            db.session.add(FileAreaCategory(
                name='Petscii Drill Games', slug='petscii-drill-games',
                as_submenu=True, sort_order=-999999))
            db.session.add(FileArea(
                tag='PETSCII.DRILL.ONE', name='Petscii Drill Area One',
                category='petscii-drill-games', is_active=True,
                is_sysop_only=False, min_access_level=10,
                upload_permission='users', storage_path=''))
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_submenu_category_collapses_and_drilling_in_reveals_its_area(self):
        from anetbbs.features import petscii_ui
        from unittest.mock import patch, AsyncMock

        class _FakeSession:
            def __init__(self, responses):
                self._responses = list(responses)
                self.written = []
                self.access_level = 100

            async def write(self, text):
                self.written.append(text)

            async def read_line(self, prompt=''):
                if prompt:
                    await self.write(prompt)
                return self._responses.pop(0) if self._responses else 'Q'

            async def clear_screen(self):
                self.written.append('[CLR]')

            @property
            def petscii_width(self):
                return 40

            def transcript(self):
                return ''.join(self.written)

        session = _FakeSession(['1', '1', 'Q', 'Q'])
        with patch('anetbbs.features.petscii_ui._app_ctx',
                   return_value=self.app.app_context()), \
             patch('anetbbs.features.petscii_ui._access_level', return_value=100), \
             patch.object(petscii_ui, '_files_browse', new=AsyncMock()) as mock_browse:
            asyncio.run(petscii_ui._files_menu(session))
        txt = session.transcript()
        # The full transcript legitimately shows the area name once the
        # drill-down into the category's own sub-list screen happens --
        # only the FIRST (top-level) screen must hide it behind the
        # collapsed category row.
        top_level_screen = txt.split('Area: Petscii Drill Games')[0]
        self.assertIn('Petscii Drill Games', top_level_screen)
        self.assertNotIn('Petscii Drill Area One', top_level_screen,
                         msg='the individual area must be hidden behind the '
                             'collapsed category row in the top-level picker')
        mock_browse.assert_called_once()
        self.assertEqual(mock_browse.call_args.args[2], 'Petscii Drill Area One')


class WebIndexCategoryOrderingTests(unittest.TestCase):
    """Regression test for a real bug found in GitHub PR #31 (external
    contribution): the web file-areas index route used to build
    cat_order by walking FileAreas in THEIR OWN sort_order and taking
    each category slug in first-seen order -- not by each
    FileAreaCategory's own configured sort_order. A category with a
    later sort_order could still render first if one of its areas
    happened to have a lower FileArea.sort_order than an area in an
    earlier-configured category. Fixed by walking `categories` (already
    queried in FileAreaCategory.sort_order order) directly, appending
    any uncategorized/fallback ('other') areas at the very end
    regardless of their own sort_order.
    """

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.file_area_web_index_order_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, FileArea, FileAreaCategory
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()
            user = User(username='webindexorderuser',
                       email='webindexorderuser@example.com',
                       password_hash='x', access_level=10)
            db.session.add(user)
            db.session.commit()
            cls.user_id = user.id

            # Category B is configured to sort FIRST (sort_order=1),
            # category A SECOND (sort_order=2) -- but area tags/sort
            # orders are set up so a buggy first-seen-by-area walk would
            # produce the opposite order.
            db.session.add_all([
                FileAreaCategory(name='Cat B', slug='web-order-cat-b', sort_order=1),
                FileAreaCategory(name='Cat A', slug='web-order-cat-a', sort_order=2),
            ])
            # Lowest FileArea.sort_order of all three is the UNCATEGORIZED
            # one -- under the old bug this would make 'other' render
            # FIRST, when it must always render LAST.
            db.session.add_all([
                FileArea(tag='WEBORD.OTH', name='Other Area',
                        category=None, sort_order=0),
                FileArea(tag='WEBORD.AAA', name='Cat A Area',
                        category='web-order-cat-a', sort_order=1),
                FileArea(tag='WEBORD.ZZZ', name='Cat B Area',
                        category='web-order-cat-b', sort_order=2),
            ])
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.user_id)
            sess['_fresh'] = True
        return client

    def test_cat_order_follows_category_sort_order_not_area_first_seen(self):
        from unittest.mock import patch
        import anetbbs.web.file_areas as file_areas_mod
        captured = {}

        def _fake_render(template_name, **kwargs):
            captured.update(kwargs)
            return ''

        client = self._client()
        with patch.object(file_areas_mod, 'render_template', side_effect=_fake_render):
            resp = client.get('/file-areas/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            captured.get('cat_order'),
            ['web-order-cat-b', 'web-order-cat-a', 'other'],
            msg='categories must appear in their own configured sort_order '
                '(Cat B=1 before Cat A=2), with the uncategorized fallback '
                "('other') always last regardless of its area's own "
                'sort_order')


if __name__ == '__main__':
    unittest.main()
