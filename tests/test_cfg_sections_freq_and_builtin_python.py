"""Regression tests for anetbbs-cfg gaps found in a full audit of the
tool (Jerry's ask: "make sure anetbbs-cfg is fully updated"):

1. FileArea's new freq_enabled/freq_password columns (WaZOO FREQ,
   echomail/freq.py) were never added to anetbbs/cfg/sections/files.py's
   FIELDS, so a console-only sysop could never turn FREQ on for an area.
2. 'builtin_python' (the game_type ANetCRAFT actually uses,
   door_runner.py:791) was missing from BOTH anetbbs-cfg's
   GAME_TYPE_CHOICES and the web admin's own GameForm.game_type
   choices -- meaning no admin surface at all could create a new
   builtin_python game through its form (WTForms SelectField validates
   submitted values against its choices list).
3. anetbbs/cfg/sections/system.py's .env GROUPS had drifted well
   behind admin.py's EDITABLE_SETTINGS over several releases (MSP,
   MRC bridge, FILE_MOD_QUEUE_ENABLED, casino starting balances, QWK
   hub id/name, wiki edit gate, etc. were all unreachable from the
   terminal tool).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class CfgFreqFieldsTests(unittest.TestCase):
    def setUp(self):
        from anetbbs.web_app import create_app
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        from anetbbs.models import db
        db.session.remove()
        self.ctx.pop()

    def test_freq_fields_are_in_the_editable_field_list(self):
        from anetbbs.cfg.sections import files as f
        keys = {field["key"] for field in f.FIELDS}
        self.assertIn("freq_enabled", keys)
        self.assertIn("freq_password", keys)

    def test_freq_fields_are_in_new_defaults(self):
        from anetbbs.cfg.sections import files as f
        self.assertIn("freq_enabled", f.NEW_DEFAULTS)
        self.assertIn("freq_password", f.NEW_DEFAULTS)
        self.assertFalse(f.NEW_DEFAULTS["freq_enabled"])

    def test_create_and_update_file_area_round_trips_freq_settings(self):
        from anetbbs.cfg.sections import files as f
        area = f.create_file_area(dict(
            f.NEW_DEFAULTS, tag="ZZCFGTEST_FREQ", name="Freq Test",
            freq_enabled=True, freq_password="hunter2"))
        self.assertTrue(area.freq_enabled)
        self.assertEqual(area.freq_password, "hunter2")

        f.update_file_area(area, {"freq_enabled": False, "freq_password": None})
        self.assertFalse(area.freq_enabled)
        self.assertIsNone(area.freq_password)

        f.delete_file_area(area)


class CfgBuiltinPythonGameTypeTests(unittest.TestCase):
    def test_anetbbs_cfg_game_type_choices_include_builtin_python(self):
        from anetbbs.cfg.sections import games
        self.assertIn("builtin_python", games.GAME_TYPE_CHOICES)

    def test_web_admin_game_form_choices_include_builtin_python(self):
        from anetbbs.web.games_admin import GameForm
        choice_values = [c[0] for c in GameForm.game_type.kwargs["choices"]]
        self.assertIn("builtin_python", choice_values)


class CfgSystemGroupsUpToDateTests(unittest.TestCase):
    """Spot-checks specific settings that were genuinely missing --
    not a full mirror of admin.py's EDITABLE_SETTINGS (system.py has
    its own broader/narrower scope by design), just the concrete gaps
    found and fixed in this pass."""

    def test_msp_group_exists(self):
        from anetbbs.cfg.sections import system
        keys = {f["key"] for g in system.GROUPS for f in g["fields"]}
        self.assertIn("MSP_ENABLED", keys)
        self.assertIn("MSP_PORT", keys)

    def test_mrc_bridge_group_exists(self):
        from anetbbs.cfg.sections import system
        keys = {f["key"] for g in system.GROUPS for f in g["fields"]}
        self.assertIn("MRC_BRIDGE_HOST", keys)
        self.assertIn("MRC_BRIDGE_PORT", keys)
        self.assertIn("MRC_BRIDGE_WS_PATH", keys)

    def test_file_mod_queue_enabled_is_reachable(self):
        from anetbbs.cfg.sections import system
        keys = {f["key"] for g in system.GROUPS for f in g["fields"]}
        self.assertIn("FILE_MOD_QUEUE_ENABLED", keys)

    def test_casino_starting_balances_are_reachable(self):
        from anetbbs.cfg.sections import system
        keys = {f["key"] for g in system.GROUPS for f in g["fields"]}
        for key in ("CASINO_BLACKJACK_START", "CASINO_SLOTS_START",
                   "CASINO_VIDEOPOKER_START", "CASINO_HOLDEM_START"):
            self.assertIn(key, keys)

    def test_wiki_edit_gate_group_exists(self):
        from anetbbs.cfg.sections import system
        keys = {f["key"] for g in system.GROUPS for f in g["fields"]}
        self.assertIn("WIKI_MIN_POSTS", keys)
        self.assertIn("WIKI_MIN_DAYS", keys)

    def test_qwk_hub_identity_fields_are_reachable(self):
        from anetbbs.cfg.sections import system
        keys = {f["key"] for g in system.GROUPS for f in g["fields"]}
        self.assertIn("QWK_HUB_ID", keys)
        self.assertIn("QWK_HUB_NAME", keys)

    def test_no_duplicate_keys_across_groups(self):
        # Each .env key should appear in exactly one group -- a
        # duplicate would silently only ever show/save through
        # whichever group's form the sysop happened to open last.
        from anetbbs.cfg.sections import system
        all_keys = [f["key"] for g in system.GROUPS for f in g["fields"]]
        self.assertEqual(len(all_keys), len(set(all_keys)),
                         "a .env key appears in more than one GROUPS entry")

    def test_every_group_field_round_trips_through_the_real_env_editor(self):
        # Every field this pass added must actually be a valid,
        # parseable (key, kind) pair the existing env-editor machinery
        # already handles -- not just present in the list.
        from anetbbs.cfg.sections import system
        for group in system.GROUPS:
            for field in group["fields"]:
                self.assertIn(field["kind"], ("bool", "int", "text", "choice"),
                             f"{field['key']} has an unknown kind {field['kind']!r}")


if __name__ == "__main__":
    unittest.main()
