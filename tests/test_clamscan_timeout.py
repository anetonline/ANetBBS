"""Regression tests for the sysop-configurable ClamAV scan timeout
(Jerry asked: "is there a way to raise to 60 seconds?" -- was previously
a hardcoded 30s default in scan_path() with no way to change it short of
editing source. Now CLAMSCAN_TIMEOUT (env var + Admin -> Settings field,
default 60) same pattern as the existing IDLE_TIMEOUT_SECONDS /
BOT_GATE_TIMEOUT settings.
"""
import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_DATA_DIR = Path(__file__).resolve().parents[1] / 'data'


def _snapshot_data_dir():
    if not _DATA_DIR.is_dir():
        return set()
    return set(_DATA_DIR.iterdir())


def _fresh_app(db_path):
    import anetbbs.config as cfg_mod
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    os.environ['FLASK_ENV'] = 'testing'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    return app


class ClamscanTimeoutNoAppContextTests(unittest.TestCase):
    """These don't need Flask at all -- scan_path()'s fallback path when
    called outside any app context (e.g. a standalone script)."""

    def test_no_app_context_falls_back_to_60(self):
        from anetbbs.features.virus_scan import scan_path
        with tempfile.NamedTemporaryFile() as f:
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout='', stderr='')
                scan_path(f.name)
                _, kwargs = mock_run.call_args
                self.assertEqual(kwargs['timeout'], 60)

    def test_explicit_timeout_overrides_fallback(self):
        from anetbbs.features.virus_scan import scan_path
        with tempfile.NamedTemporaryFile() as f:
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout='', stderr='')
                scan_path(f.name, timeout=15)
                _, kwargs = mock_run.call_args
                self.assertEqual(kwargs['timeout'], 15)

    def test_missing_scanner_binary_still_handled(self):
        from anetbbs.features.virus_scan import scan_path
        with tempfile.NamedTemporaryFile() as f:
            with patch('subprocess.run', side_effect=FileNotFoundError):
                result = scan_path(f.name)
                self.assertFalse(result.scanner_available)


class ClamscanTimeoutConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._data_dir_before = _snapshot_data_dir()
        import anetbbs.config as cfg_mod
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._orig_flask_env = os.environ.get('FLASK_ENV')
        cls._orig_clamscan_env = os.environ.get('CLAMSCAN_TIMEOUT')

    @classmethod
    def tearDownClass(cls):
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        if cls._orig_flask_env is None:
            os.environ.pop('FLASK_ENV', None)
        else:
            os.environ['FLASK_ENV'] = cls._orig_flask_env
        if cls._orig_clamscan_env is None:
            os.environ.pop('CLAMSCAN_TIMEOUT', None)
        else:
            os.environ['CLAMSCAN_TIMEOUT'] = cls._orig_clamscan_env
        for entry in _snapshot_data_dir() - cls._data_dir_before:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        os.environ.pop('CLAMSCAN_TIMEOUT', None)

    def test_default_is_60_when_env_var_unset(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'default.db'))
        with app.app_context():
            self.assertEqual(app.config.get('CLAMSCAN_TIMEOUT'), 60)

    def test_scan_path_reads_current_app_config(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'wired.db'))
        from anetbbs.features.virus_scan import scan_path
        with app.app_context():
            app.config['CLAMSCAN_TIMEOUT'] = 90
            with tempfile.NamedTemporaryFile() as f:
                with patch('subprocess.run') as mock_run:
                    mock_run.return_value = MagicMock(returncode=0, stdout='', stderr='')
                    scan_path(f.name)
                    _, kwargs = mock_run.call_args
                    self.assertEqual(kwargs['timeout'], 90)

    def test_explicit_timeout_still_overrides_app_config(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'override.db'))
        from anetbbs.features.virus_scan import scan_path
        with app.app_context():
            app.config['CLAMSCAN_TIMEOUT'] = 90
            with tempfile.NamedTemporaryFile() as f:
                with patch('subprocess.run') as mock_run:
                    mock_run.return_value = MagicMock(returncode=0, stdout='', stderr='')
                    scan_path(f.name, timeout=5)
                    _, kwargs = mock_run.call_args
                    self.assertEqual(kwargs['timeout'], 5)

    def test_editable_settings_lists_clamscan_timeout_and_no_restart_needed(self):
        from anetbbs.web.admin import EDITABLE_SETTINGS
        entry = next((e for e in EDITABLE_SETTINGS if e[0] == 'CLAMSCAN_TIMEOUT'), None)
        self.assertIsNotNone(entry, 'CLAMSCAN_TIMEOUT missing from Admin -> Settings')
        _key, _label, _kind, restart_flag = entry
        self.assertFalse(restart_flag,
                         'scan_path() reads timeout per-call, no restart should be needed')


if __name__ == '__main__':
    unittest.main()
