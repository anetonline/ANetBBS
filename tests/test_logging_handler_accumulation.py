"""Regression test for a real bug found live (2026-08-28, alongside the
severe OOM investigation on Jerry's dev laptop): anetbbs.web_app.
_configure_logging() unconditionally called app.logger.addHandler() on
every create_app() call. Flask's app.logger resolves to a named
logger via logging.getLogger(name), and Python's logging module never
garbage-collects a named logger -- so within one long-running process
that calls create_app() many times (exactly what this repo's own test
suite does: one call per test file's setUpClass, dozens of times in a
single `pytest a.py b.py c.py ...` invocation), handlers piled up on
the SAME logger across every call, and every subsequent log line got
written once per accumulated handler. Confirmed live via `lsof
bbs.log` showing a single long-running test process holding 8 separate
open file descriptors to the same log file. This compounding
duplication, not just the separately-fixed lack of rotation, is the
real reason an ordinary dev log reached 80 million lines / 6.1GB.

Also covers the rotation fix itself: the file handler is a
RotatingFileHandler with a real size cap, not a plain FileHandler.
"""
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class LoggingHandlerAccumulationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        os.environ['FLASK_ENV'] = 'testing'
        import anetbbs.config as cfg_mod
        self._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        self.addCleanup(self._restore_db_uri)

    def _restore_db_uri(self):
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = self._orig_db_uri

    def test_repeated_create_app_does_not_accumulate_handlers(self):
        """Same shape as this repo's own batched pytest runs: many
        create_app() calls in one process. Handler count on app.logger
        must stay constant (one console + one file handler), not grow
        with each call."""
        import anetbbs.config as cfg_mod
        from anetbbs.web_app import create_app

        db_path = os.path.join(self._tmp.name, 'handler_accum.db')
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'

        apps = [create_app('testing') for _ in range(5)]

        # Flask's app.logger is a named logger (logging.getLogger(name)),
        # cached process-wide -- confirms these 5 create_app() calls all
        # share the SAME underlying logger object, which is exactly the
        # condition that let handlers pile up.
        loggers = {id(a.logger) for a in apps}
        self.assertEqual(len(loggers), 1,
                         'expected every create_app() call to share the same '
                         'named logger -- if this fails, the test no longer '
                         'exercises the real accumulation condition')

        last_app = apps[-1]
        stream_handlers = [h for h in last_app.logger.handlers
                           if isinstance(h, logging.StreamHandler)
                           and not isinstance(h, logging.FileHandler)]
        file_handlers = [h for h in last_app.logger.handlers
                         if isinstance(h, logging.FileHandler)]
        self.assertEqual(len(stream_handlers), 1,
                         f'expected exactly one console handler after 5 '
                         f'create_app() calls, found {len(stream_handlers)} -- '
                         f'this is the exact shape of the live bug (a handler '
                         f'added on every call, never removed)')
        self.assertEqual(len(file_handlers), 1,
                         f'expected exactly one file handler after 5 '
                         f'create_app() calls, found {len(file_handlers)}')

    def test_file_handler_is_rotating_not_unbounded(self):
        import anetbbs.config as cfg_mod
        from anetbbs.web_app import create_app
        from logging.handlers import RotatingFileHandler

        db_path = os.path.join(self._tmp.name, 'rotating.db')
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'

        app = create_app('testing')
        file_handlers = [h for h in app.logger.handlers
                         if isinstance(h, logging.FileHandler)]
        self.assertEqual(len(file_handlers), 1)
        handler = file_handlers[0]
        self.assertIsInstance(
            handler, RotatingFileHandler,
            'log file handler must be a RotatingFileHandler with a real size '
            'cap -- a plain FileHandler is what let bbs.log reach 6.1GB live')
        self.assertGreater(handler.maxBytes, 0)
        self.assertGreater(handler.backupCount, 0)


if __name__ == '__main__':
    unittest.main()
