"""Regression test for a real gap found live (2026-09-21) while
diagnosing the umrc-client stats-file feature: mrc/bridge/main.py's
config.json has always accepted a "log_level" key (install.sh's
wizard writes it, config.example.json documents it) but NOTHING ever
read it -- the module logger's level was set once, at import time,
purely from the MRC_BRIDGE_LOG_LEVEL environment variable. A sysop
editing "log_level" in config.json -- the only place this setting is
documented/visible -- and restarting the service saw zero effect,
with no error or any indication why, confirmed live trying to capture
a debug wire trace on production.

Fixed by having BridgeApp.__init__() apply config.json's own
log_level to the logger, unless MRC_BRIDGE_LOG_LEVEL is explicitly
set in the environment (which still wins, for a one-off override with
no file edit -- see the module-level constant's own docstring).
"""
import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrc.bridge import main as bridge_main  # noqa: E402
from mrc.bridge.main import BridgeApp  # noqa: E402


def _make_app(tmp_path, **config_overrides):
    config = {
        "mrc_host": "test.invalid",
        "mrc_port": 1,
        "bridge_bbs": "TestBBS",
        "platform_info": "TEST/1.0",
        "data_dir": str(tmp_path / "data"),
    }
    config.update(config_overrides)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    return BridgeApp(str(config_path))


class LogLevelConfigTests(unittest.TestCase):
    def setUp(self):
        self._orig_level = bridge_main.logger.level
        self.addCleanup(bridge_main.logger.setLevel, self._orig_level)
        self._env_patch = mock.patch.dict(os.environ, {}, clear=False)
        self._env_patch.start()
        os.environ.pop('MRC_BRIDGE_LOG_LEVEL', None)
        self.addCleanup(self._env_patch.stop)

    def test_config_log_level_debug_is_applied(self):
        with tempfile.TemporaryDirectory() as td:
            bridge_main.logger.setLevel(logging.INFO)
            _make_app(Path(td), log_level="DEBUG")
            self.assertEqual(bridge_main.logger.level, logging.DEBUG)

    def test_env_var_takes_precedence_over_config(self):
        with tempfile.TemporaryDirectory() as td:
            bridge_main.logger.setLevel(logging.WARNING)
            os.environ["MRC_BRIDGE_LOG_LEVEL"] = "INFO"
            _make_app(Path(td), log_level="DEBUG")
            # Env var was set -- config's "DEBUG" must NOT override it.
            self.assertEqual(bridge_main.logger.level, logging.WARNING)

    def test_missing_log_level_key_leaves_level_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            bridge_main.logger.setLevel(logging.ERROR)
            _make_app(Path(td))  # no log_level key at all
            self.assertEqual(bridge_main.logger.level, logging.ERROR)

    def test_invalid_log_level_value_is_ignored_not_fatal(self):
        with tempfile.TemporaryDirectory() as td:
            bridge_main.logger.setLevel(logging.INFO)
            # Must not raise -- a typo in config.json shouldn't crash
            # the whole bridge at startup.
            _make_app(Path(td), log_level="BOGUS")
            self.assertEqual(bridge_main.logger.level, logging.INFO)


if __name__ == '__main__':
    unittest.main()
