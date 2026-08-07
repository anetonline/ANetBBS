"""Unit tests for anetbbs/cfg/sections/system.py's .env parse/round-trip
helpers (anetbbs-cfg's System / Network Settings section).

No curses involved -- these exercise the plain parse/apply/render
functions directly, same spirit as test_wiki_render_placeholder_restore
etc: the curses-driving `run`/`_edit_group` functions are a thin layer
on top that can't be exercised headlessly (no TTY in CI/sandbox), so the
real logic worth testing lives in functions that don't touch curses at
all.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.cfg.sections import system as cfg_system

SAMPLE_ENV = """# ANetBBS Configuration
# Copy this file to .env and customize for your environment

FLASK_ENV=production
SECRET_KEY=abc123

# Telnet Server Settings
TELNET_ENABLED=true
TELNET_HOST=0.0.0.0
TELNET_PORT=2233

BBS_NAME=Joe's BBS
BBS_NODES=8
"""


class EnvRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp_path = Path(__file__).resolve().parent / ".cfg_env_editor_test.env"
        self.tmp_path.write_text(SAMPLE_ENV, encoding="utf-8")

    def tearDown(self):
        if self.tmp_path.exists():
            self.tmp_path.unlink()

    def test_noop_round_trip_is_byte_identical(self):
        lines = cfg_system.load_env_lines(self.tmp_path)
        rendered = cfg_system.render_lines(lines)
        self.assertEqual(rendered, SAMPLE_ENV)

    def test_env_dict_extracts_only_kv_lines(self):
        lines = cfg_system.load_env_lines(self.tmp_path)
        d = cfg_system.env_dict(lines)
        self.assertEqual(d["TELNET_ENABLED"], "true")
        self.assertEqual(d["TELNET_PORT"], "2233")
        self.assertEqual(d["BBS_NAME"], "Joe's BBS")
        self.assertNotIn("# ANetBBS Configuration", d)

    def test_apply_updates_changes_only_targeted_keys(self):
        lines = cfg_system.load_env_lines(self.tmp_path)
        new_lines = cfg_system.apply_updates(lines, {"TELNET_PORT": "2323", "BBS_NAME": "New Name"})
        rendered = cfg_system.render_lines(new_lines)
        self.assertIn("TELNET_PORT=2323", rendered)
        self.assertIn("BBS_NAME=New Name", rendered)
        # everything else (comments, other keys) untouched
        self.assertIn("TELNET_ENABLED=true", rendered)
        self.assertIn("# Telnet Server Settings", rendered)
        self.assertIn("SECRET_KEY=abc123", rendered)

    def test_apply_updates_appends_missing_key(self):
        lines = cfg_system.load_env_lines(self.tmp_path)
        new_lines = cfg_system.apply_updates(lines, {"WEB_PORT": "5000"})
        rendered = cfg_system.render_lines(new_lines)
        self.assertIn("WEB_PORT=5000", rendered)

    def test_write_env_persists_to_disk(self):
        lines = cfg_system.load_env_lines(self.tmp_path)
        new_lines = cfg_system.apply_updates(lines, {"BBS_NODES": "16"})
        cfg_system.write_env(new_lines, self.tmp_path)
        reread = cfg_system.load_env_lines(self.tmp_path)
        self.assertEqual(cfg_system.env_dict(reread)["BBS_NODES"], "16")

    def test_missing_file_returns_empty(self):
        missing = Path(__file__).resolve().parent / ".does_not_exist.env"
        self.assertEqual(cfg_system.load_env_lines(missing), [])

    def test_form_value_conversions(self):
        self.assertIs(cfg_system._to_form_value("bool", "true"), True)
        self.assertIs(cfg_system._to_form_value("bool", "false"), False)
        self.assertIs(cfg_system._to_form_value("bool", None), False)
        self.assertEqual(cfg_system._to_form_value("int", "2233"), 2233)
        self.assertEqual(cfg_system._to_form_value("int", None), 0)
        self.assertEqual(cfg_system._to_form_value("int", "not-a-number"), 0)
        self.assertEqual(cfg_system._to_form_value("text", None), "")
        self.assertEqual(cfg_system._to_env_value("bool", True), "true")
        self.assertEqual(cfg_system._to_env_value("bool", False), "false")
        self.assertEqual(cfg_system._to_env_value("int", 2233), "2233")


if __name__ == "__main__":
    unittest.main()
