"""Regression test for a real bug found while digging into a Jerry-
reported failure: tests/msp_loopback_check.py (a manual MSP/SYSTAT
loopback diagnostic that builds two separate BBS app instances,
each with its own DATABASE_URL, in one process) always crashed
building the second app with `sqlite3.OperationalError: no such
table: bbs_menus`.

Root cause: DevelopmentConfig.SQLALCHEMY_DATABASE_URI and
ProductionConfig.SQLALCHEMY_DATABASE_URI were plain class-body
expressions (`os.environ.get('DATABASE_URL') or <default>`),
evaluated exactly once -- the first time anetbbs.config is imported
in a process. Any code that calls create_app() more than once in the
same process with a different DATABASE_URL each time (the loopback
script's whole point -- simulating two separate BBS instances)
silently got the FIRST call's database URI for every app after the
first. Confirmed via direct reproduction: the second app's own
app.config['SQLALCHEMY_DATABASE_URI'] pointed at the first app's temp
directory, and the second app's own data directory never got a
database file at all.

In real production this is dormant -- a systemd-deployed process
calls create_app() exactly once, with DATABASE_URL fixed from .env
before the process ever imports anetbbs.config. It only bites code
that builds multiple app instances with different databases in one
process, which is exactly what the MSP loopback diagnostic does.

Fixed with a class-attribute descriptor (_EnvDatabaseURI) that
re-reads os.environ on every access instead of freezing at import
time. A plain @property does NOT work here: get_config() returns the
config CLASS itself (never an instance), and property.__get__ only
triggers through instance attribute access -- Flask's own
config.from_object(cfg) and every get_config() caller read the URI
via getattr(cfg, 'SQLALCHEMY_DATABASE_URI') on the class, which is
exactly the access pattern this test exercises.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class DatabaseUriEnvFreshnessTests(unittest.TestCase):
    def setUp(self):
        self._orig_database_url = os.environ.get('DATABASE_URL')

    def tearDown(self):
        if self._orig_database_url is None:
            os.environ.pop('DATABASE_URL', None)
        else:
            os.environ['DATABASE_URL'] = self._orig_database_url

    def test_production_config_uri_follows_env_changes_within_one_process(self):
        os.environ['DATABASE_URL'] = 'sqlite:////tmp/first-uri-test.db'
        first = cfg_mod.ProductionConfig.SQLALCHEMY_DATABASE_URI
        self.assertEqual(first, 'sqlite:////tmp/first-uri-test.db')

        os.environ['DATABASE_URL'] = 'sqlite:////tmp/second-uri-test.db'
        second = cfg_mod.ProductionConfig.SQLALCHEMY_DATABASE_URI
        self.assertEqual(second, 'sqlite:////tmp/second-uri-test.db')
        self.assertNotEqual(first, second)

    def test_development_config_uri_follows_env_changes_within_one_process(self):
        os.environ['DATABASE_URL'] = 'sqlite:////tmp/dev-first-uri-test.db'
        first = cfg_mod.DevelopmentConfig.SQLALCHEMY_DATABASE_URI
        self.assertEqual(first, 'sqlite:////tmp/dev-first-uri-test.db')

        os.environ['DATABASE_URL'] = 'sqlite:////tmp/dev-second-uri-test.db'
        second = cfg_mod.DevelopmentConfig.SQLALCHEMY_DATABASE_URI
        self.assertNotEqual(first, second)

    def test_getattr_on_class_sees_fresh_value(self):
        """Exercises the exact access pattern get_config() callers and
        Flask's config.from_object() use -- getattr() on the class
        itself, not an instance. This is the pattern a plain @property
        would silently break."""
        os.environ['DATABASE_URL'] = 'sqlite:////tmp/getattr-first.db'
        cfg = cfg_mod.get_config('production')
        first = getattr(cfg, 'SQLALCHEMY_DATABASE_URI', None)
        self.assertEqual(first, 'sqlite:////tmp/getattr-first.db')

        os.environ['DATABASE_URL'] = 'sqlite:////tmp/getattr-second.db'
        cfg = cfg_mod.get_config('production')
        second = getattr(cfg, 'SQLALCHEMY_DATABASE_URI', None)
        self.assertEqual(second, 'sqlite:////tmp/getattr-second.db')

    def test_falls_back_to_default_path_when_env_unset(self):
        os.environ.pop('DATABASE_URL', None)
        uri = cfg_mod.ProductionConfig.SQLALCHEMY_DATABASE_URI
        self.assertTrue(uri.startswith('sqlite:///'))
        self.assertTrue(uri.endswith('anetbbs.db'))


if __name__ == '__main__':
    unittest.main()
