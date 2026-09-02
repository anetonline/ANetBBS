"""Regression tests for the MRC web chat page's WebSocket host-override
mechanism (anetbbs/web/mrc_web.py + anetbbs/templates/mrc/index.html),
covering a real Docker bug found testing the single-container "quick
start" image against an actual Docker daemon for the first time.

That image has no nginx at all, so /mrcws doesn't exist on the web
app's own port (5000) -- only the MRC bridge's own port (8080,
published directly per docs/22-containers.md) actually answers it.
Terminal MRC worked (it talks to the bridge directly, host:port, no
browser/nginx involved) while web MRC silently 404'd.

The investigation initially aimed at the wrong target: anetbbs/web/
mrc_web.py used to compute a full `bridge_ws_url` and pass it to the
template, but the template's actual client-side WS URL builder
(buildWsUrlForChoice() in mrc/index.html, which supports picking
between multiple named MRC servers, each with its own path) never
read that variable at all -- it was 100% dead code, always
reconstructing from `location.host` (the browser's own host+port)
instead. Confirmed by grepping the template for the variable name
before assuming the first fix (which "worked" in the sense of
computing a correct value nothing ever consumed) was sufficient.

Real fix: mrc_web.py now renders a `window.MRC_WS_HOST_OVERRIDE` JS
global (empty string = "no override, keep using location.host", which
is already correct for nginx-fronted deployments); buildWsUrlForChoice()
uses it instead of location.host when non-empty. These tests can only
verify the Python side (what gets rendered into the page) -- the JS
half (that buildWsUrlForChoice() actually consumes it) needs a real
browser/Playwright check, not exercised by this suite.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class MRCWsHostOverrideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.mrc_web_docker_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            u = User.query.filter_by(username='mrcwebtest').first()
            if not u:
                u = User(username='mrcwebtest', email='mrcwebtest@example.com')
                u.set_password('x')
                db.session.add(u)
                db.session.commit()
            cls.uid = u.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _get_mrc_index(self, host='bbs.example.com:5000'):
        client = self.app.test_client()
        base_url = f'http://{host}'
        with client.session_transaction(base_url=base_url) as sess:
            sess['_user_id'] = str(self.uid)
            sess['_fresh'] = True
        return client.get('/mrc/', base_url=base_url)

    def _extract_override(self, body):
        marker = 'window.MRC_WS_HOST_OVERRIDE = "'
        start = body.index(marker) + len(marker)
        end = body.index('"', start)
        return body[start:end]

    def test_a_quote_in_the_override_value_cannot_break_out_of_the_js_string(self):
        """Real Low finding from a security/performance audit
        (2026-09-02): mrc_ws_host_override used to be interpolated into
        the JS global with plain Jinja auto-escaping (HTML-entity
        encoding) rather than a JS-safe encoder -- a fragile,
        non-standard pattern that only avoided breaking out by luck of
        the surrounding markup, not by design (in docker-single mode
        this value is partly built from request.host, the client-
        supplied Host header). Fixed via `| tojson`, a real JSON/JS
        string encoder. Tests the template's rendering directly with a
        value containing a literal double-quote + inline script payload
        -- the actual property the fix addresses -- rather than trying
        to smuggle one through a real HTTP Host header, which Werkzeug
        itself already normalizes/rejects before the view ever sees it.
        """
        with self.app.test_request_context('/mrc/'):
            from flask import render_template
            rendered_page = render_template(
                'mrc/index.html',
                mrc_ws_host_override='evil.com";alert(1);//',
                suggested_handle='',
            )
        self.assertEqual(
            rendered_page.count('window.MRC_WS_HOST_OVERRIDE ='), 1)
        marker = 'window.MRC_WS_HOST_OVERRIDE = '
        start = rendered_page.index(marker) + len(marker)
        # Parse the JSON value directly from that position -- the
        # payload's own raw text contains literal ';' characters
        # (from "alert(1);//"), so a naive search for the next ';'
        # would truncate mid-string; raw_decode() finds the real end
        # of the JSON literal regardless of what it contains.
        import json
        decoded, end = json.JSONDecoder().raw_decode(rendered_page[start:])
        self.assertEqual(decoded, 'evil.com";alert(1);//',
                         'the fix must not mangle a legitimate value, '
                         'only safely escape it')
        # And the very next non-whitespace character after the JSON
        # value must be the statement terminator, not more JS content
        # that escaped out of the string (i.e. nothing was interpreted
        # as separate code).
        after = rendered_page[start + end:start + end + 5].strip()
        self.assertTrue(after.startswith(';'),
                        f'expected the JSON literal to be immediately '
                        f'followed by the statement terminator, got: '
                        f'{after!r}')

    def test_docker_single_overrides_to_bridge_port_on_browser_host(self):
        """The actual bug: docker-single mode has no nginx, so the page
        must tell the browser to use <its own hostname>:<bridge port>
        instead of location.host (which would be :5000, the web app's
        own port -- 404, nothing listens there for /mrcws)."""
        with patch('anetbbs.web.mrc_web._RUNTIME', 'docker-single'):
            self.app.config['MRC_BRIDGE_HOST'] = 'localhost'  # default, unset by sysop
            self.app.config['MRC_BRIDGE_PORT'] = 8080
            resp = self._get_mrc_index()
        self.assertEqual(resp.status_code, 200)
        override = self._extract_override(resp.get_data(as_text=True))
        self.assertEqual(override, 'bbs.example.com:8080')

    def test_native_install_has_no_override(self):
        """Regression coverage for the already-working case: native
        installs (and docker-compose with its documented nginx/Caddy
        overlay) proxy /mrcws on the SAME port as the page -- the
        override must stay empty so the JS keeps using location.host."""
        with patch('anetbbs.web.mrc_web._RUNTIME', 'systemd'):
            self.app.config['MRC_BRIDGE_HOST'] = 'localhost'
            self.app.config['MRC_BRIDGE_PORT'] = 8080
            resp = self._get_mrc_index()
        self.assertEqual(resp.status_code, 200)
        override = self._extract_override(resp.get_data(as_text=True))
        self.assertEqual(override, '')

    def test_explicit_host_wins_regardless_of_runtime(self):
        """An explicitly configured public MRC_BRIDGE_HOST (the
        documented docker-compose pattern) must always take priority
        over any runtime-based guessing, in every runtime mode."""
        for runtime in ('docker-single', 'docker-compose', 'systemd'):
            with self.subTest(runtime=runtime):
                with patch('anetbbs.web.mrc_web._RUNTIME', runtime):
                    self.app.config['MRC_BRIDGE_HOST'] = 'mrc.example.com'
                    self.app.config['MRC_BRIDGE_PORT'] = 9999
                    resp = self._get_mrc_index()
                override = self._extract_override(resp.get_data(as_text=True))
                self.assertEqual(override, 'mrc.example.com:9999')

    def test_docker_compose_behaves_like_native(self):
        """docker-compose mode has real nginx in front when using the
        documented overlay -- only docker-single (no nginx at all)
        gets the automatic override. Confirms docker-compose isn't
        accidentally swept into the new branch."""
        with patch('anetbbs.web.mrc_web._RUNTIME', 'docker-compose'):
            self.app.config['MRC_BRIDGE_HOST'] = 'localhost'
            self.app.config['MRC_BRIDGE_PORT'] = 8080
            resp = self._get_mrc_index()
        override = self._extract_override(resp.get_data(as_text=True))
        self.assertEqual(override, '')

    def test_docker_single_strips_port_from_browser_host(self):
        """request.host includes the browser's own port (e.g.
        bbs.example.com:5000) -- must be stripped before appending the
        bridge's port, not concatenated into something like
        bbs.example.com:5000:8080."""
        with patch('anetbbs.web.mrc_web._RUNTIME', 'docker-single'):
            self.app.config['MRC_BRIDGE_HOST'] = 'localhost'
            self.app.config['MRC_BRIDGE_PORT'] = 8080
            resp = self._get_mrc_index(host='192.168.1.50:5000')
        override = self._extract_override(resp.get_data(as_text=True))
        self.assertEqual(override, '192.168.1.50:8080')


if __name__ == '__main__':
    unittest.main()
