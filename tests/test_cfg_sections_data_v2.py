"""Unit tests for the 12 additional anetbbs-cfg sections built for full
web-admin parity (games, hub, security extras, file bulletins, gallery,
menu, petscii_menu, events, wall, login_modules, lastcallers, backups).
Same curses-free data-layer testing pattern as test_cfg_sections_data.py.
"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class CfgSectionsDataV2Tests(unittest.TestCase):
    def setUp(self):
        from anetbbs.web_app import create_app
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        from anetbbs.models import db
        db.session.remove()
        self.ctx.pop()

    # -- games --------------------------------------------------------

    def test_games_crud(self):
        from anetbbs.cfg.sections import games as g

        game = g.create_game(dict(g.GAME_NEW_DEFAULTS, name="ZZ Test Door", slug="zz-test-door"))
        self.assertIn(game.id, {x.id for x in g.list_games()})

        g.update_game(game, {"is_active": False, "max_nodes": 4})
        self.assertFalse(game.is_active)
        self.assertEqual(game.max_nodes, 4)

        g.delete_game(game)
        self.assertNotIn(game.id, {x.id for x in g.list_games()})

    def test_game_categories_crud(self):
        from anetbbs.cfg.sections import games as g

        cat = g.create_category(dict(g.CATEGORY_NEW_DEFAULTS, name="ZZ Cat", slug="zz-cat"))
        self.assertIn(cat.id, {x.id for x in g.list_categories()})
        g.update_category(cat, {"sort_order": 5})
        self.assertEqual(cat.sort_order, 5)
        g.delete_category(cat)
        self.assertNotIn(cat.id, {x.id for x in g.list_categories()})

    def test_game_session_disconnect_and_clear_stale(self):
        from anetbbs.models import db, Game, User, GameSession
        from anetbbs.cfg.sections import games as g

        game = g.create_game(dict(g.GAME_NEW_DEFAULTS, name="ZZ Session Door", slug="zz-session-door"))
        u = User(username="zzsessuser", email="zzsess@example.com")
        u.set_password("x")
        db.session.add(u)
        db.session.commit()

        gs = GameSession(game_id=game.id, user_id=u.id, node_number=1, status="active")
        db.session.add(gs)
        db.session.commit()

        self.assertEqual(len(g.list_active_sessions()), 1)

        with mock.patch("anetbbs.games.door_runner.terminate_session") as m_term:
            g.disconnect_session(gs.id)
        m_term.assert_called_once_with(gs.id)
        self.assertEqual(g.list_active_sessions(), [])

        gs2 = GameSession(game_id=game.id, user_id=u.id, node_number=2, status="active")
        db.session.add(gs2)
        db.session.commit()
        count = g.clear_stale_sessions()
        self.assertEqual(count, 1)
        self.assertEqual(g.list_active_sessions(), [])

    # -- hub: QWK node requests (security-relevant credential issuing) --

    def test_qwk_request_approve_creates_node_with_password(self):
        from anetbbs.models import db, QWKNodeRequest, QWKNode
        from anetbbs.cfg.sections import hub

        req = QWKNodeRequest(bbs_name="Zeta BBS", packet_id="ZETA1",
                              sysop_name="Zed", status="pending")
        db.session.add(req)
        db.session.commit()

        ok, msg = hub.approve_qwk_request(req)
        self.assertTrue(ok, msg)
        self.assertEqual(req.status, "approved")
        self.assertIsNotNone(req.node_id)
        self.assertEqual(req.reviewed_by, hub.REVIEWER_LABEL)

        node = QWKNode.query.get(req.node_id)
        self.assertEqual(node.packet_id, "ZETA1")
        self.assertEqual(len(req.generated_password), 16)
        self.assertEqual(node.password, req.generated_password)

    def test_qwk_request_approve_rejects_duplicate_packet_id(self):
        from anetbbs.models import db, QWKNodeRequest, QWKNode
        from anetbbs.cfg.sections import hub

        db.session.add(QWKNode(packet_id="DUPE1", name="Existing", password="x"))
        req = QWKNodeRequest(bbs_name="Dupe BBS", packet_id="DUPE1", status="pending")
        db.session.add(req)
        db.session.commit()

        ok, msg = hub.approve_qwk_request(req)
        self.assertFalse(ok)
        self.assertIn("already taken", msg)
        self.assertEqual(req.status, "pending")

    def test_qwk_request_approve_rejects_invalid_packet_id(self):
        from anetbbs.models import db, QWKNodeRequest
        from anetbbs.cfg.sections import hub

        req = QWKNodeRequest(bbs_name="Bad BBS", packet_id="!!bad!!", status="pending")
        db.session.add(req)
        db.session.commit()

        ok, msg = hub.approve_qwk_request(req)
        self.assertFalse(ok)
        self.assertIn("not valid", msg)

    def test_qwk_request_deny_sets_reason(self):
        from anetbbs.models import db, QWKNodeRequest
        from anetbbs.cfg.sections import hub

        req = QWKNodeRequest(bbs_name="Deny Me", packet_id="DENY1", status="pending")
        db.session.add(req)
        db.session.commit()

        ok, msg = hub.deny_qwk_request(req, "not a real BBS")
        self.assertTrue(ok)
        self.assertEqual(req.status, "denied")
        self.assertEqual(req.deny_reason, "not a real BBS")

    def test_qwk_request_cannot_double_review(self):
        from anetbbs.models import db, QWKNodeRequest
        from anetbbs.cfg.sections import hub

        req = QWKNodeRequest(bbs_name="Once", packet_id="ONCE1", status="approved")
        db.session.add(req)
        db.session.commit()

        ok, msg = hub.approve_qwk_request(req)
        self.assertFalse(ok)
        ok, msg = hub.deny_qwk_request(req)
        self.assertFalse(ok)

    def test_hub_log_listings_do_not_crash_when_empty(self):
        from anetbbs.cfg.sections import hub
        self.assertEqual(hub.list_areafix_log(), [])
        self.assertEqual(hub.list_poll_log(), [])

    # -- security extras (word filters, auto-ban, registration log) ---

    def test_word_filters_crud(self):
        from anetbbs.cfg.sections import users as u

        f = u.create_word_filter(dict(u.WORD_FILTER_NEW_DEFAULTS, pattern="zzbadword"))
        self.assertIn(f.id, {x.id for x in u.list_word_filters()})
        u.update_word_filter(f, {"is_active": False})
        self.assertFalse(f.is_active)
        u.delete_word_filter(f)
        self.assertNotIn(f.id, {x.id for x in u.list_word_filters()})

    def test_auto_ban_config_singleton_get_and_update(self):
        from anetbbs.cfg.sections import users as u

        cfg1 = u.get_auto_ban_config()
        cfg2 = u.get_auto_ban_config()
        self.assertEqual(cfg1.id, cfg2.id)

        u.update_auto_ban_config({"attempt_limit": 20, "enabled": False})
        cfg3 = u.get_auto_ban_config()
        self.assertEqual(cfg3.attempt_limit, 20)
        self.assertFalse(cfg3.enabled)

    def test_registration_attempts_listing(self):
        from anetbbs.models import db, RegistrationAttempt
        from anetbbs.cfg.sections import users as u

        db.session.add(RegistrationAttempt(ip_address="1.2.3.4", success=False,
                                            error_reason="bad captcha"))
        db.session.commit()
        rows = u.list_registration_attempts()
        self.assertTrue(any(r.ip_address == "1.2.3.4" for r in rows))

    # -- file bulletins -------------------------------------------------

    def test_file_bulletins_sync_and_edit(self):
        import tempfile
        from anetbbs.models import FileBulletin
        from anetbbs.cfg.sections import file_bulletins as fb

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "readme.txt").write_text("hello")
            self.app.config["FILE_BULLETINS_DIR"] = tmpdir
            rows = fb.list_bulletins()
            self.assertTrue(any(r.filename == "readme.txt" for r in rows))
            row = next(r for r in rows if r.filename == "readme.txt")
            self.assertFalse(row.is_active)  # auto-registered inactive
            fb.update_bulletin(row, {"is_active": True, "title": "Read Me"})
            self.assertTrue(row.is_active)
            row_id = row.id
            fb.delete_bulletin(row)
            # Query directly rather than list_bulletins() -- the file is
            # still on disk, so calling list_bulletins() again re-syncs
            # and legitimately re-registers a fresh row for it (matches
            # FileBulletin's own documented "never auto-deleted, files
            # still present get re-registered" behavior).
            self.assertIsNone(FileBulletin.query.get(row_id))

    # -- gallery (JSON-file backed, not DB -- mock the file I/O) -------

    def test_gallery_crud_against_mocked_config_store(self):
        from anetbbs.cfg.sections import gallery as gal

        store = []

        def fake_load():
            return [dict(g) for g in store]

        def fake_save(galleries):
            store[:] = [dict(g) for g in galleries]

        with mock.patch("anetbbs.web.gallery._load_config", side_effect=fake_load), \
             mock.patch("anetbbs.web.gallery._save_config", side_effect=fake_save):
            entry = gal.create_gallery({"label": "My Art", "slug": "", "path": "",
                                         "description": "", "is_active": True, "sort_order": 1})
            self.assertEqual(entry["slug"], "my-art")
            self.assertEqual(len(gal.list_galleries()), 1)

            with self.assertRaises(ValueError):
                gal.create_gallery({"label": "My Art Again", "slug": "my-art", "path": "",
                                     "description": "", "is_active": True, "sort_order": 2})

            gal.update_gallery("my-art", {"is_active": False})
            self.assertFalse(gal.list_galleries()[0]["is_active"])

            gal.delete_gallery("my-art")
            self.assertEqual(gal.list_galleries(), [])

    def test_gallery_slugify(self):
        from anetbbs.cfg.sections.gallery import _slugify
        self.assertEqual(_slugify("My Cool Gallery!"), "my-cool-gallery")
        self.assertEqual(_slugify(""), "gallery")

    # -- BBS menu / PETSCII menu ----------------------------------------

    def test_bbs_menu_and_items_crud_and_reorder(self):
        from anetbbs.cfg.sections import menu as m

        menu_row = m.create_menu(dict(m.MENU_NEW_DEFAULTS, name="zzmenu", title="ZZ Menu"))
        item1 = m.create_item(menu_row, dict(m.ITEM_NEW_DEFAULTS, hotkey="B", label="Boards", sort_order=1))
        item2 = m.create_item(menu_row, dict(m.ITEM_NEW_DEFAULTS, hotkey="F", label="Files", sort_order=2))

        self.assertEqual([i.hotkey for i in m.list_items(menu_row)], ["B", "F"])
        m.reorder_item(menu_row, item1, 1)
        self.assertEqual([i.hotkey for i in m.list_items(menu_row)], ["F", "B"])

        m.delete_item(item2)
        self.assertEqual(len(m.list_items(menu_row)), 1)
        m.delete_menu(menu_row)
        self.assertEqual([x.name for x in m.list_menus() if x.name == "zzmenu"], [])

    def test_petscii_menu_and_items_crud(self):
        from anetbbs.cfg.sections import petscii_menu as pm

        menu_row = pm.create_menu(dict(pm.MENU_NEW_DEFAULTS, name="zzpetmenu", title="ZZ PETSCII Menu"))
        item = pm.create_item(menu_row, dict(pm.ITEM_NEW_DEFAULTS, hotkey="B", label="Boards"))
        self.assertEqual(len(pm.list_items(menu_row)), 1)
        pm.update_item(item, {"label": "Message Boards"})
        self.assertEqual(item.label, "Message Boards")
        pm.delete_menu(menu_row)  # cascades

    # -- scheduled events -------------------------------------------------

    def test_scheduled_events_crud_and_json_validation(self):
        from anetbbs.cfg.sections import events as ev

        good = dict(ev.NEW_DEFAULTS, name="ZZ Event")
        self.assertIsNone(ev._validate_json_fields(good))

        bad = dict(good, params_json="{not json")
        self.assertIsNotNone(ev._validate_json_fields(bad))

        e = ev.create_event(good)
        self.assertIn(e.id, {x.id for x in ev.list_events()})
        ev.update_event(e, {"is_enabled": False})
        self.assertFalse(e.is_enabled)
        ev.delete_event(e)
        self.assertNotIn(e.id, {x.id for x in ev.list_events()})

    def test_run_event_now_executes_noop_handler(self):
        from anetbbs.cfg.sections import events as ev

        e = ev.create_event(dict(ev.NEW_DEFAULTS, name="ZZ Noop Event", handler_key="noop"))
        ok, out = ev.run_event_now(e.id)
        self.assertTrue(ok, out)

    # -- graffiti wall ----------------------------------------------------

    def test_wall_moderation_delete_restore_clear(self):
        from anetbbs.models import db, WallPost
        from anetbbs.cfg.sections import wall

        p1 = WallPost(username="zzwaller", line1="hi there")
        p2 = WallPost(username="zzwaller2", line1="hello")
        db.session.add_all([p1, p2])
        db.session.commit()

        self.assertGreaterEqual(len(wall.list_active_posts()), 2)

        wall.delete_post(p1)
        self.assertTrue(p1.is_deleted)
        self.assertIn(p1.id, {x.id for x in wall.list_deleted_posts()})

        wall.restore_post(p1)
        self.assertFalse(p1.is_deleted)

        count = wall.clear_all_posts()
        self.assertGreaterEqual(count, 2)
        self.assertEqual(wall.list_active_posts(), [])

    # -- login modules ------------------------------------------------

    def test_login_modules_crud_and_json_validation_and_reorder(self):
        from anetbbs.cfg.sections import login_modules as lm

        self.assertIsNone(lm._validate(dict(lm.NEW_DEFAULTS)))
        self.assertIsNotNone(lm._validate(dict(lm.NEW_DEFAULTS, params_json="{bad")))

        m1 = lm.create_module(dict(lm.NEW_DEFAULTS, name="ZZ Mod 1", sort_order=1))
        m2 = lm.create_module(dict(lm.NEW_DEFAULTS, name="ZZ Mod 2", sort_order=2))

        ordered = [x.name for x in lm.list_modules() if x.name in ("ZZ Mod 1", "ZZ Mod 2")]
        self.assertEqual(ordered, ["ZZ Mod 1", "ZZ Mod 2"])

        lm.reorder_module(m1, 1)
        ordered = [x.name for x in lm.list_modules() if x.name in ("ZZ Mod 1", "ZZ Mod 2")]
        self.assertEqual(ordered, ["ZZ Mod 2", "ZZ Mod 1"])

        lm.delete_module(m1)
        lm.delete_module(m2)

    # -- last callers (read-only) --------------------------------------

    def test_last_callers_listing(self):
        from anetbbs.models import db, CallerLog
        from anetbbs.cfg.sections import lastcallers as lc

        db.session.add(CallerLog(username="zzcaller", service="ssh", ip_address="9.9.9.9"))
        db.session.commit()
        rows = lc.list_recent()
        self.assertTrue(any(r.username == "zzcaller" for r in rows))

    # -- backups (filesystem-backed, uses real backups_admin helpers) --

    def test_backups_listing_and_delete_use_web_admin_helpers(self):
        from anetbbs.cfg.sections import backups as b

        with mock.patch("anetbbs.web.backups_admin._scan", return_value=[
            {"name": "anetbbs-backup-20260101000000", "created_at": None,
             "from_version": "1.0.1", "to_version": "1.0.2", "size_bytes": 1234,
             "files": {".env.bak"}},
        ]) as m_scan:
            rows = b.list_backups()
        m_scan.assert_called_once()
        self.assertEqual(rows[0]["name"], "anetbbs-backup-20260101000000")

        with mock.patch("anetbbs.web.backups_admin._safe_backup_dir", return_value=None):
            ok, msg = b.delete_backup("../etc/passwd")
        self.assertFalse(ok)
        self.assertIn("Invalid", msg)


if __name__ == "__main__":
    unittest.main()
