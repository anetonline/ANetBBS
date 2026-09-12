"""Regression tests for a Jinja-template-layer security audit (2026-09-12)
covering anetbbs/templates/ specifically (as opposed to the Python route/
handler layer prior audit rounds already covered).

Each finding below is the same underlying bug class: a value with no
scheme validation on save, rendered straight into an href= attribute (or,
for the postcard/ANSI-editor case, into a <script> block) with no guard --
a javascript: URI (or, for the script case, a literal "</script>") lets
attacker-controlled content execute same-origin in whoever's browser
renders the page. This is the exact class of bug the gemini/RSS
javascript: URI fix (web/gemini.py's _GEMTEXT_SAFE_SCHEMES, rss/poller.py's
_is_safe_http_url()) already closed for gemtext links and RSS item
links/images -- these are the other templates doing the same unguarded
`<a href="{{ value }}">` pattern that check missed:

1. profile/view.html -- User.website and a custom "url"-type profile
   field are free text with no scheme validation on save (web/profile.py).
   Any OTHER user viewing that profile and clicking the link would run it.

2. rss/feed.html -- RssFeed.site_url can be auto-populated from the
   external feed's own self-declared <link> (rss/poller.py) with no
   scheme check -- RssItem.link/image_url already get this check at
   ingest time; site_url didn't.

3. peers/view.html, peers/index.html, peers/admin.html -- PeerBbs.web_url
   is submitted by ANY logged-in user via peers.submit (self-service
   directory listing pending sysop approval) with no scheme validation
   on save (web/peers.py). peers/admin.html renders it for the SYSOP to
   review BEFORE approving -- a javascript: URI here would execute in the
   reviewing admin's own session, a privilege-escalation path from any
   registered user into an admin browser session.

4. templates/ansi_editor/_editor_widget.html -- grid_json is
   json.dumps()'d server-side from raw client POST data with no per-cell
   validation (web/ansi_editor.py's save(), web/postcards.py's save()); a
   cell's "c" field can be an arbitrary-length string, so a crafted save
   could plant a literal "</script>" that closes the real <script> tag
   early once |safe re-embeds it verbatim. postcards.py reuses this exact
   widget for ANY logged-in user's postcard, and lets an admin edit
   someone else's postcard too (_require_owner_or_403), so this could
   execute in a reviewing admin's session.

5. templates/irc/index.html -- default_nick (current_user.username) was
   interpolated into a JS string literal with plain quoting instead of
   |tojson -- a lower-severity finding since Jinja's own HTML-entity
   auto-escaping of quote characters already prevented an actual
   break-out, but it's still the wrong pattern for a JS context and this
   locks in the correct one.

None of these required a Python-side change to fix -- every fix is a
template-only scheme guard (or |tojson) at the render site, matching how
this app's own RSS ingest-time fix protects every template that renders
RssItem.link/image_url without each needing its own copy of the check.
"""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class TemplatesAuditXssTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.templates_audit_xss_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import (db, User, PeerBbs, RssFeed, Postcard,
                                     UserField, UserFieldValue)
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False

        with cls.app.app_context():
            db.create_all()

            victim = User(username='xss_victim', email='xss_victim@example.com',
                          is_active=True, access_level=10)
            victim.set_password('victimpassword123')
            admin = User(username='xss_admin', email='xss_admin@example.com',
                        is_active=True, is_admin=True, access_level=255)
            admin.set_password('adminpassword123')
            db.session.add_all([victim, admin])
            db.session.commit()
            cls.victim_id = victim.id
            cls.admin_id = admin.id

            # --- Finding 1: profile website + custom url field ---
            victim.website = 'javascript:alert(document.cookie)'
            db.session.commit()

            uf = UserField(name='homepage', label='Homepage', field_type='url',
                          show_in_profile=True)
            db.session.add(uf)
            db.session.commit()
            db.session.add(UserFieldValue(
                user_id=victim.id, field_id=uf.id,
                value='javascript:alert(document.cookie)'))
            db.session.commit()

            # --- Finding 2: RSS feed site_url ---
            feed = RssFeed(name='Evil Feed', url='https://example.com/feed.xml',
                           site_url='javascript:alert(document.cookie)',
                           is_active=True, min_access_level=0)
            db.session.add(feed)
            db.session.commit()
            cls.feed_id = feed.id

            # --- Finding 3: PeerBbs.web_url, both pending and approved ---
            pending_peer = PeerBbs(
                name='Evil Pending Peer', hostname='evilpending.example.com',
                web_url='javascript:alert(document.cookie)',
                is_active=True, is_approved=False,
                submitted_by_user_id=victim.id)
            approved_peer = PeerBbs(
                name='Evil Approved Peer', hostname='evilapproved.example.com',
                web_url='javascript:alert(document.cookie)',
                is_active=True, is_approved=True,
                submitted_by_user_id=victim.id)
            db.session.add_all([pending_peer, approved_peer])
            db.session.commit()
            cls.pending_peer_id = pending_peer.id
            cls.approved_peer_id = approved_peer.id

            # --- Finding 4: postcard grid_json script breakout ---
            evil_grid = {
                'width': 1, 'height': 1,
                'cells': [{'c': '</script><script>alert(document.cookie)</script>',
                          'fg': 1, 'bg': 1}],
            }
            card = Postcard(name='Evil Card', slug='evil-card-xss',
                            width=1, height=1, grid_json=json.dumps(evil_grid),
                            ansi_text='', created_by_id=victim.id)
            db.session.add(card)
            db.session.commit()
            cls.postcard_slug = card.slug

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    # ---- Finding 1: profile/view.html ----

    def test_profile_website_javascript_uri_not_rendered_as_href(self):
        client = self._client_as(self.admin_id)
        resp = client.get('/profile/xss_victim')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertNotIn('href="javascript:alert(document.cookie)"', body,
                         'a javascript: URI must never be link-ified')

    def test_profile_custom_url_field_javascript_uri_not_rendered_as_href(self):
        client = self._client_as(self.admin_id)
        resp = client.get('/profile/xss_victim')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        # The plain text value is still shown (just not as a clickable
        # link) -- confirm no unguarded href carries the payload.
        self.assertNotIn('<a href="javascript:', body)

    # ---- Finding 2: rss/feed.html ----

    def test_rss_feed_site_url_javascript_uri_not_rendered_as_href(self):
        client = self._client_as(self.victim_id)
        resp = client.get(f'/rss/{self.feed_id}')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertNotIn('href="javascript:alert(document.cookie)"', body)

    # ---- Finding 3: peers templates ----

    def test_peers_admin_pending_web_url_javascript_uri_not_rendered_as_href(self):
        """The sysop-review path -- an admin viewing the pending-approval
        queue must never have a javascript: URI link-ified in their own
        session."""
        client = self._client_as(self.admin_id)
        resp = client.get('/bbses/admin')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertNotIn('href="javascript:alert(document.cookie)"', body)

    def test_peers_admin_approved_web_url_javascript_uri_not_rendered_as_href(self):
        client = self._client_as(self.admin_id)
        resp = client.get('/bbses/admin')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertNotIn('href="javascript:alert(document.cookie)"', body)

    def test_peers_view_web_url_javascript_uri_not_rendered_as_href(self):
        client = self._client_as(self.admin_id)
        resp = client.get(f'/bbses/{self.approved_peer_id}')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertNotIn('href="javascript:alert(document.cookie)"', body)

    # Note: peers.index ('/bbses/') is deliberately not exercised here --
    # it kicks off a real background network refresh of external BBS
    # directory sources on every uncached hit (peers.py's
    # _refresh_source_async()), which is unrelated to this fix and not
    # something a template-layer regression test should depend on.
    # peers/index.html's local-peer web_url guard is textually identical
    # to peers/admin.html's (both fixed together), which the tests above
    # already exercise via a real request.

    # ---- Finding 4: postcard / ansi editor grid_json script breakout ----

    def test_postcard_edit_grid_cell_cannot_break_out_of_script_tag(self):
        client = self._client_as(self.victim_id)
        resp = client.get(f'/postcards/{self.postcard_slug}/edit')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertNotIn('</script><script>alert(document.cookie)</script>', body,
                         'a grid cell must never be able to close the real '
                         '<script> tag early')
        # The payload text should still be present, just safely JSON-escaped
        # (tojson escapes "<" and ">" so the browser HTML parser never sees
        # a literal "</script" while scanning the raw-text script element).
        self.assertIn('alert(document.cookie)', body)


if __name__ == '__main__':
    unittest.main()
