"""Regression test for deploy/serve.py's DATABASE_URL log redaction.

Real gap found in a security/performance audit: serve.py used to log
_db_url completely raw on every service start. SQLite URLs (the only
officially supported backend, sqlite:///path) have no credentials at
all so this was a no-op in practice -- but ANETBBS_DB_URL is a real,
already-wired escape hatch for Postgres/MySQL, whose connection
strings DO carry a plaintext password (scheme://user:PASSWORD@host/db)
that would otherwise land in the systemd journal in plaintext on every
single service start/restart. Latent today, real the moment Postgres
support is actually used.

deploy/serve.py can't be imported directly in a test process -- it
calls eventlet.monkey_patch() and create_app()/socketio.run() at
module level with no __main__ guard, which would try to actually bind
a port and block. Instead this extracts just the
_redact_db_url()/_DB_URL_CREDENTIALS_RE definitions from the REAL
current source via ast and execs them in isolation, so the test
exercises the actual shipped logic rather than a hand-copied duplicate
that could silently drift out of sync.
"""
import ast
import sys
import unittest
from pathlib import Path

_SERVE_PY = Path(__file__).resolve().parents[1] / 'deploy' / 'serve.py'


def _load_redact_db_url():
    tree = ast.parse(_SERVE_PY.read_text())
    wanted_names = {'_DB_URL_CREDENTIALS_RE', '_redact_db_url'}
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == '_redact_db_url':
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == '_DB_URL_CREDENTIALS_RE'
                for t in node.targets):
            nodes.append(node)
    found_names = set()
    for node in nodes:
        if isinstance(node, ast.FunctionDef):
            found_names.add(node.name)
        else:
            found_names.add(node.targets[0].id)
    missing = wanted_names - found_names
    if missing:
        raise AssertionError(
            f'deploy/serve.py no longer defines {missing} -- '
            'update this test to match the current source')
    module = ast.Module(body=nodes, type_ignores=[])
    ns = {'re': __import__('re')}
    exec(compile(module, filename=str(_SERVE_PY), mode='exec'), ns)
    return ns['_redact_db_url']


class RedactDbUrlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.redact = staticmethod(_load_redact_db_url())

    def test_sqlite_url_is_unaffected(self):
        url = 'sqlite:///home/anetbbs/data/anetbbs.db'
        self.assertEqual(self.redact(url), url)

    def test_postgres_password_is_masked(self):
        url = 'postgresql://myuser:mypassword@localhost:5432/anetbbs'
        redacted = self.redact(url)
        self.assertNotIn('mypassword', redacted)
        self.assertIn('myuser', redacted)
        self.assertIn('localhost:5432/anetbbs', redacted)
        self.assertIn(':***@', redacted)

    def test_mysql_password_is_masked(self):
        url = 'mysql://root:hunter2@127.0.0.1/anetbbs'
        redacted = self.redact(url)
        self.assertNotIn('hunter2', redacted)
        self.assertIn('root', redacted)

    def test_url_with_no_password_is_unaffected(self):
        url = 'postgresql://myuser@localhost:5432/anetbbs'
        self.assertEqual(self.redact(url), url)

    def test_empty_and_none_are_handled(self):
        self.assertEqual(self.redact(''), '')
        self.assertIsNone(self.redact(None))


if __name__ == '__main__':
    unittest.main()
