"""PP-1 browser matrix: does a user-uploaded personal page get BBS-origin
privileges in a real browser?

Opt-in (needs Playwright + Chromium): ANETBBS_BROWSER_TESTS=1.
Run the same file against old code to see the "before" result.

Setup: a real werkzeug server for a temp app (CSRF ON), three hostnames
mapped to 127.0.0.1 by Chromium's host resolver (bbs.test, pages.bbs.test,
pages-other.test), an admin logged in through the real login form.
Every request records whether a session cookie arrived.
"""
import json
import os
import socket
import tempfile
import threading
import time
import unittest

BROWSER = os.environ.get('ANETBBS_BROWSER_TESTS') == '1'

PROBE_JS = r"""
(async () => {
  const r = {};
  try { r.cookie = document.cookie; } catch (e) { r.cookie = 'ERR:' + e.name; }
  try { localStorage.setItem('k', 'v'); r.storage = 'accessible'; }
  catch (e) { r.storage = 'ERR:' + e.name; }
  try {
    const res = await fetch('/admin/users', {credentials: 'include'});
    const t = await res.text();
    r.admin = /csrf_token/.test(t) ? 'READ_ADMIN_PAGE' : ('status' + res.status);
  } catch (e) { r.admin = 'ERR:' + e.name; }
  r.opener = (window.opener === null) ? 'null' : 'set';
  document.title = 'R' + JSON.stringify(r);
  fetch('/beacon/html-ran').catch(() => {});
})();
"""


def _free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


@unittest.skipUnless(BROWSER, 'set ANETBBS_BROWSER_TESTS=1 (needs playwright + chromium)')
class PersonalPageBrowserIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import anetbbs.config as cfg_mod
        from werkzeug.serving import make_server
        cls._orig = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls.tmp = tempfile.mkdtemp(prefix='pp1b-')
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls.tmp}/t.db'
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        app = create_app('testing')
        data_dir = os.path.join(cls.tmp, 'data')
        app.config.update(TESTING=False, WTF_CSRF_ENABLED=True,
                          PERSONAL_PAGES_ENABLED=True, DATA_DIR=data_dir,
                          SERVER_NAME=None, SESSION_COOKIE_SECURE=False)
        with app.app_context():
            db.create_all()
            for name, adm in (('sysop', True), ('mallory', False)):
                u = User.query.filter_by(username=name).first() or User(
                    username=name, email=f'{name}@x.test')
                u.is_admin, u.is_verified, u.is_active = adm, True, True
                u.set_password('Corr3ct-Horse-pw')
                db.session.add(u)
            db.session.commit()
        cls.seen = []

        @app.before_request
        def _record():
            from flask import request
            cls.seen.append((request.host.split(':')[0], request.path,
                             'session' in request.cookies,
                             request.cookies.get('session', '')[:8]))
            if request.path.startswith('/beacon/'):
                return ('', 204)

        mal = os.path.join(data_dir, 'personal_pages', 'users', 'mallory')
        os.makedirs(os.path.join(mal, 'sub'), exist_ok=True)
        page = f'<html><head><title>start</title></head><body><script>{PROBE_JS}</script></body></html>'
        files = {
            'x.html': page, 'x.htm': page, 'x.xhtml': page,
            'sub/index.html': page,
            'a.svg': '<svg xmlns="http://www.w3.org/2000/svg" onload="fetch(\'/beacon/svg-ran\')">'
                     '<script>fetch("/beacon/svg-script-ran")</script></svg>',
            'embed.html': '<html><body><img src="a.svg"><title>embed</title></body></html>',
            'note.txt': '<script>fetch("/beacon/txt-ran")</script>',
            'noext': '<script>fetch("/beacon/noext-ran")</script>',
            'lib.js': 'document.title = "JSRAN"; fetch("/beacon/js-ran");',
            'usesjs.html': '<html><body><script src="lib.js"></script></body></html>',
            'toss.html': '<script>try{document.cookie="session=TOSSED; domain=bbs.test; path=/";'
                         'document.title="TOSS-OK"}catch(e){document.title="TOSS-ERR:"+e.name}</script>',
        }
        for rel, body in files.items():
            with open(os.path.join(mal, rel), 'w') as f:
                f.write(body)
        sysroot = os.path.join(data_dir, 'personal_pages', 'club')
        os.makedirs(sysroot, exist_ok=True)
        with open(os.path.join(sysroot, 'index.html'), 'w') as f:
            f.write(page)

        cls.port = _free_port()
        cls.server = make_server('127.0.0.1', cls.port, app, threaded=True)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.app = app

        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        engine = os.environ.get('ANETBBS_BROWSER', 'chromium')
        cls.engine = engine
        if engine == 'firefox':
            cls.browser = cls.pw.firefox.launch(firefox_user_prefs={
                'network.dns.localDomains': 'bbs.test,pages.bbs.test,pages-other.test',
                'network.proxy.allow_hijacking_localhost': True})
        else:
            cls.browser = cls.pw.chromium.launch(args=[
                '--host-resolver-rules=MAP bbs.test 127.0.0.1, MAP pages.bbs.test 127.0.0.1, '
                'MAP pages-other.test 127.0.0.1'])
        cls.ctx = cls.browser.new_context(accept_downloads=True)
        p = cls.ctx.new_page()
        base = cls.url('bbs.test', '/auth/login')
        p.goto(base)
        p.fill('input[name=username]', 'sysop')
        p.fill('input[name=password]', 'Corr3ct-Horse-pw')
        p.click('[type=submit]')
        p.wait_for_load_state()
        p.close()

    @classmethod
    def tearDownClass(cls):
        import anetbbs.config as cfg_mod
        for closer in (lambda: cls.ctx.close(), lambda: cls.browser.close(),
                       lambda: cls.pw.stop(), lambda: cls.server.shutdown()):
            try:
                closer()
            except Exception:
                pass
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig
        print(f'\nPP1-BROWSER-RESULTS[{getattr(cls, "engine", "?")}] '
              + json.dumps(getattr(cls, 'results', {}), sort_keys=True))

    @classmethod
    def url(cls, host, path):
        return f'http://{host}:{cls.port}{path}'

    def _probe(self, host, path):
        p = self.ctx.new_page()
        try:
            p.goto(self.url(host, path))
            deadline = time.time() + 8
            while time.time() < deadline and not p.title().startswith('R{'):
                time.sleep(0.1)
            t = p.title()
            return json.loads(t[1:]) if t.startswith('R{') else {'title': t}
        finally:
            p.close()

    def _record(self, key, value):
        type(self).results = getattr(type(self), 'results', {})
        type(self).results[key] = value

    def _beacons(self):
        return {path for _h, path, *_ in self.seen if path.startswith('/beacon/')}

    def test_logged_in_admin_sanity(self):
        p = self.ctx.new_page()
        r = p.goto(self.url('bbs.test', '/admin/users'))
        ok = r.status == 200 and 'csrf_token' in p.content()
        p.close()
        self._record('admin_session_works', ok)
        self.assertTrue(ok, 'test setup: admin must be logged in on bbs.test')

    def test_html_variants_and_indexes_cannot_use_admin_session(self):
        for path in ('/~mallory/x.html', '/~mallory/x.htm', '/~mallory/sub/',
                     '/club/'):
            with self.subTest(path=path):
                r = self._probe('bbs.test', path)
                self._record(f'probe {path}', r)
                self.assertNotEqual(r.get('admin'), 'READ_ADMIN_PAGE', r)
                self.assertEqual(r.get('cookie', ''), '', r)
                self.assertTrue(str(r.get('storage', '')).startswith('ERR'), r)

    def test_admin_fetch_from_page_carries_no_session_cookie(self):
        self.seen.clear()
        self._probe('bbs.test', '/~mallory/x.html')
        hits = [s for s in self.seen if s[1] == '/admin/users']
        self._record('admin_fetch_cookie_sent', [h[2] for h in hits])
        self.assertTrue(hits, 'the page did attempt the fetch')
        self.assertFalse(any(h[2] for h in hits), hits)

    def test_svg_txt_noext_never_execute(self):
        self.seen.clear()
        for path in ('/~mallory/embed.html', '/~mallory/note.txt', '/~mallory/noext'):
            p = self.ctx.new_page()
            try:
                p.goto(self.url('bbs.test', path))
                time.sleep(0.8)
            finally:
                p.close()
        p = self.ctx.new_page()
        try:
            with p.expect_download(timeout=5000) as dl:
                try:
                    p.goto(self.url('bbs.test', '/~mallory/a.svg'))
                except Exception:
                    pass
            self._record('svg_direct_is_download', bool(dl.value))
        finally:
            p.close()
        time.sleep(0.5)
        beacons = self._beacons()
        self._record('svg_txt_noext_beacons', sorted(beacons))
        self.assertFalse({'/beacon/svg-ran', '/beacon/svg-script-ran',
                          '/beacon/txt-ran', '/beacon/noext-ran'} & beacons, beacons)

    def test_script_src_runs_only_inside_the_sandbox(self):
        self.seen.clear()
        p = self.ctx.new_page()
        try:
            p.goto(self.url('bbs.test', '/~mallory/usesjs.html'))
            time.sleep(0.8)
            title = p.title()
        finally:
            p.close()
        self._record('script_src_title', title)
        hits = [s for s in self.seen if s[1] == '/beacon/js-ran']
        self.assertFalse(any(h[2] for h in hits), 'beacon must not carry the session')

    def test_cookie_scope_on_other_hostnames(self):
        self.seen.clear()
        for host in ('pages.bbs.test', 'pages-other.test'):
            p = self.ctx.new_page()
            try:
                p.goto(self.url(host, '/~mallory/x.html'))
            finally:
                p.close()
        sent = {h: c for h, path, c, _v in self.seen if path == '/~mallory/x.html'}
        self._record('session_cookie_sent_by_host', sent)
        self.assertFalse(sent.get('pages.bbs.test'), sent)
        self.assertFalse(sent.get('pages-other.test'), sent)

    def test_cookie_tossing_from_sibling_subdomain(self):
        """Records whether a page on pages.bbs.test can plant a cookie the
        BBS host then receives (decides the __Host- recommendation)."""
        p = self.ctx.new_page()
        try:
            p.goto(self.url('pages.bbs.test', '/~mallory/toss.html'))
            time.sleep(0.5)
            toss_title = p.title()
        finally:
            p.close()
        self.seen.clear()
        p = self.ctx.new_page()
        try:
            p.goto(self.url('bbs.test', '/about'))
        finally:
            p.close()
        received = [v for h, path, c, v in self.seen if h == 'bbs.test' and path == '/about']
        self._record('cookie_toss', {'page_title': toss_title,
                                     'bbs_received_session_prefix': received})
        # Under the sandbox the page cannot set cookies at all.
        self.assertNotEqual(toss_title, 'TOSS-OK')

    def test_manager_link_has_no_opener(self):
        from anetbbs.models import User
        data_dir = self.app.config['DATA_DIR']
        own = os.path.join(data_dir, 'personal_pages', 'users', 'sysop')
        os.makedirs(own, exist_ok=True)
        with open(os.path.join(own, 'o.html'), 'w') as f:
            f.write(f'<script>{PROBE_JS}</script>')
        p = self.ctx.new_page()
        try:
            p.goto(self.url('bbs.test', '/_pages/'))
            with self.ctx.expect_page() as newp:
                p.click('a[href="/~sysop/o.html"]')
            np = newp.value
            deadline = time.time() + 8
            while time.time() < deadline and not np.title().startswith('R{'):
                time.sleep(0.1)
            r = json.loads(np.title()[1:]) if np.title().startswith('R{') else {}
            np.close()
        finally:
            p.close()
        self._record('manager_link_opener', r.get('opener'))
        self.assertEqual(r.get('opener'), 'null', r)
        del User


if __name__ == '__main__':
    unittest.main()
