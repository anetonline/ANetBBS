"""Unit tests for anetbbs-cfg's per-section data-access helpers (the
curses-free half of anetbbs/cfg/sections/*.py -- list_*/create_*/
update_*/delete_*/reorder_* functions). The curses-driving `run`/`_add`/
`_edit` wrappers on top of these can't be exercised headlessly (no TTY),
so this covers the actual DB logic directly, same pattern as
test_cfg_env_editor.py for the System section.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class CfgSectionsDataTests(unittest.TestCase):
    def setUp(self):
        from anetbbs.web_app import create_app
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        from anetbbs.models import db
        db.session.remove()
        self.ctx.pop()

    # -- boards --------------------------------------------------------

    def test_boards_crud_and_reorder(self):
        # create_app('testing') seeds its own default demo boards -- filter
        # list_boards() down to just the ones this test created rather than
        # asserting the full (seed-data-polluted) list.
        from anetbbs.cfg.sections import boards as b

        board1 = b.create_board(dict(b.NEW_DEFAULTS, name="ZZCfgTest General", order=1000))
        board2 = b.create_board(dict(b.NEW_DEFAULTS, name="ZZCfgTest Tech", order=1001))
        mine_ids = {board1.id, board2.id}

        def mine():
            return [x for x in b.list_boards() if x.id in mine_ids]

        self.assertEqual([x.name for x in mine()], ["ZZCfgTest General", "ZZCfgTest Tech"])

        b.update_board(board1, {"description": "General chat", "min_access_level": 20})
        self.assertEqual(board1.description, "General chat")
        self.assertEqual(board1.min_access_level, 20)

        b.reorder_board(board1, 1)  # move General down past Tech
        self.assertEqual([x.name for x in mine()], ["ZZCfgTest Tech", "ZZCfgTest General"])

        b.delete_board(board2)
        self.assertEqual([x.name for x in mine()], ["ZZCfgTest General"])

    def test_boards_duplicate_name_raises_integrity_error(self):
        from sqlalchemy.exc import IntegrityError
        from anetbbs.cfg.sections import boards as b

        b.create_board(dict(b.NEW_DEFAULTS, name="ZZCfgTest Dup"))
        with self.assertRaises(IntegrityError):
            b.create_board(dict(b.NEW_DEFAULTS, name="ZZCfgTest Dup"))

    # -- echomail --------------------------------------------------------

    def test_echomail_networks_and_areas(self):
        # create_app('testing') seeds a default HubIdentity's networks too --
        # same filter-by-id approach as the boards test above.
        from anetbbs.cfg.sections import echomail as e

        net = e.create_network(dict(e.NETWORK_NEW_DEFAULTS, name="ZZCfgTest fsxNet"))
        self.assertIn(net.id, {n.id for n in e.list_networks()})

        area1 = e.create_area(net, dict(e.AREA_NEW_DEFAULTS, tag="FSX_GEN", name="General", order=1))
        area2 = e.create_area(net, dict(e.AREA_NEW_DEFAULTS, tag="FSX_TECH", name="Tech", order=2))
        self.assertEqual([a.tag for a in e.list_areas(net)], ["FSX_GEN", "FSX_TECH"])

        e.reorder_area(net, area1, 1)
        self.assertEqual([a.tag for a in e.list_areas(net)], ["FSX_TECH", "FSX_GEN"])

        e.update_network(net, {"is_active": False})
        self.assertFalse(net.is_active)

        e.delete_area(area2)
        self.assertEqual([a.tag for a in e.list_areas(net)], ["FSX_GEN"])

        e.delete_network(net)
        self.assertNotIn(net.id, {n.id for n in e.list_networks()})

    # -- files --------------------------------------------------------

    def test_file_areas_crud(self):
        # create_app('testing') seeds default demo file areas too -- same
        # filter-by-id approach as the boards test above.
        from anetbbs.cfg.sections import files as f

        area = f.create_file_area(dict(f.NEW_DEFAULTS, tag="ZZCFGTEST_FILES_GEN", name="General Files"))
        self.assertIn(area.id, {a.id for a in f.list_file_areas()})

        f.update_file_area(area, {"is_active": False, "upload_permission": "sysop"})
        self.assertFalse(area.is_active)
        self.assertEqual(area.upload_permission, "sysop")

        f.delete_file_area(area)
        self.assertNotIn(area.id, {a.id for a in f.list_file_areas()})

    # -- users --------------------------------------------------------

    def test_user_search_update_and_password_reset(self):
        from anetbbs.models import db, User
        from anetbbs.cfg.sections import users as u

        alice = User(username="alice", email="alice@example.com")
        alice.set_password("original-password")
        bob = User(username="bob", email="bob@example.com")
        bob.set_password("original-password")
        db.session.add_all([alice, bob])
        db.session.commit()

        results = u.search_users("ali")
        self.assertEqual([x.username for x in results], ["alice"])

        u.update_user(alice, {"access_level": 50, "is_locked": True})
        self.assertEqual(alice.access_level, 50)
        self.assertTrue(alice.is_locked)

        old_hash = alice.password_hash
        temp_password = u.reset_password(alice)
        self.assertNotEqual(alice.password_hash, old_hash)
        self.assertTrue(alice.check_password(temp_password))
        self.assertFalse(alice.check_password("original-password"))

    def test_ip_bans_crud(self):
        from sqlalchemy.exc import IntegrityError
        from anetbbs.cfg.sections import users as u

        ban = u.create_ban({"cidr": "10.0.0.0/8", "reason": "abuse", "expires_at": None})
        self.assertEqual([b.cidr for b in u.list_bans()], ["10.0.0.0/8"])

        with self.assertRaises(IntegrityError):
            u.create_ban({"cidr": "10.0.0.0/8", "reason": "dupe", "expires_at": None})

        from anetbbs.models import db
        db.session.rollback()

        u.update_ban(ban, {"reason": "updated reason", "expires_at": None})
        self.assertEqual(ban.reason, "updated reason")

        u.delete_ban(ban)
        self.assertEqual(u.list_bans(), [])

    def test_parse_expiry(self):
        from anetbbs.cfg.sections import users as u

        self.assertIsNone(u._parse_expiry(""))
        self.assertIsNone(u._parse_expiry(None))
        dt = u._parse_expiry("2026-12-31")
        self.assertEqual((dt.year, dt.month, dt.day), (2026, 12, 31))
        with self.assertRaises(ValueError):
            u._parse_expiry("not-a-date")


if __name__ == "__main__":
    unittest.main()
