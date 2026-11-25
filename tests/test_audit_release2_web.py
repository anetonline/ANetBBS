"""Release 2 audit fixes reachable through the web app.

M-6 session revocation, M-8 email change, M-7 stable question, N-5 config
typing, L-4 suggest endpoints, L-5 healthz, L-6 score input, L-13 verify
link origin, L-14 private-mail images, F-2 deleted PMs, I-7 qwkhub gate,
I-8 dos-data access, L-3 add-on integrity.
"""
import os
import tempfile
import unittest
from unittest import mock

import anetbbs.config as cfg_mod


class _AppCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp = tempfile.mkdtemp(prefix='r2web-')
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp}/t.db'
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False,
                              PUBLIC_BASE_URL='https://bbs.example.test')
        with cls.app.app_context():
            db.create_all()
            cls.alice = cls._user('alice', 'alice@example.test')
            cls.bob = cls._user('bob', 'bob@example.test')

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_uri

    @classmethod
    def _user(cls, name, email, admin=False):
        with cls.app.app_context():
            return cls._user_in_ctx(name, email, admin)

    @classmethod
    def _user_in_ctx(cls, name, email, admin):
        from anetbbs.models import db, User
        u = User.query.filter_by(username=name).first()
        if u is None:
            u = User(username=name, email=email, is_verified=True, is_active=True,
                     is_admin=admin)
            u.set_password('Corr3ct-horse!')
            getattr(u, "ensure_auth_version", lambda: None)()
            db.session.add(u)
            db.session.commit()
        return u.id

    def _login(self, uid):
        from anetbbs.models import db, User
        c = self.app.test_client()
        with self.app.app_context():
            ident = db.session.get(User, uid).get_id()
        with c.session_transaction() as s:
            s['_user_id'] = ident
            s['_fresh'] = True
        return c

    def _authed(self, c):
        r = c.get('/profile/edit')
        return r.status_code == 200


class SessionRevocation(_AppCase):
    def test_password_change_and_lock_end_existing_sessions(self):
        from anetbbs.models import db, User
        c = self._login(self.alice)
        self.assertTrue(self._authed(c))
        with self.app.app_context():
            u = db.session.get(User, self.alice)
            u.set_password('An0ther-horse!')
            db.session.commit()
        self.assertFalse(self._authed(c))

        c = self._login(self.alice)
        self.assertTrue(self._authed(c))
        with self.app.app_context():
            u = db.session.get(User, self.alice)
            u.is_locked = True
            db.session.commit()
            u.is_locked = False
            db.session.commit()
        self.assertFalse(self._authed(c))

    def test_deactivate_after_commit_still_revokes(self):
        # Attributes expire after a commit; the listener must still fire.
        from anetbbs.models import db, User
        uid = self._user('dave', 'dave@example.test')
        c = self._login(uid)
        self.assertTrue(self._authed(c))
        with self.app.app_context():
            u = db.session.get(User, uid)
            db.session.commit()            # expires u.is_active
            u.is_active = False
            db.session.commit()
            u.is_active = True
            db.session.commit()
        self.assertFalse(self._authed(c))

    def test_logout_needs_post_and_everywhere_revokes_others(self):
        c1, c2 = self._login(self.bob), self._login(self.bob)
        r = c1.get('/auth/logout')
        self.assertEqual(r.status_code, 200)
        self.assertIn('data-logout-form', r.get_data(as_text=True))
        self.assertTrue(self._authed(c1))          # GET only shows a confirm page
        c1.post('/auth/logout', data={'everywhere': '1'})
        self.assertFalse(self._authed(c1))
        self.assertFalse(self._authed(c2))


class EmailChangeNeedsPassword(_AppCase):
    def _post(self, c, email, pw=''):
        return c.post('/profile/edit', data={'email': email, 'current_password': pw})

    def test_email_change(self):
        from anetbbs.models import db, User
        uid = self._user('carol', 'carol@example.test')
        c = self._login(uid)
        with mock.patch('anetbbs.web.profile._notify_email_change') as note:
            self._post(c, 'attacker@example.test')
            with self.app.app_context():
                self.assertEqual(db.session.get(User, uid).email, 'carol@example.test')
            self._post(c, 'carol2@example.test', 'Corr3ct-horse!')
            with self.app.app_context():
                self.assertEqual(db.session.get(User, uid).email, 'carol2@example.test')
            self.assertTrue(note.called)


class StableRecoveryQuestion(_AppCase):
    def test_same_question_every_attempt(self):
        from anetbbs.models import db, UserSecurityAnswer
        from anetbbs.features import rate_limit as rl
        uid = self._user('erin', 'erin@example.test')
        with self.app.app_context():
            for q in ('Q-one?', 'Q-two?', 'Q-three?'):
                db.session.add(UserSecurityAnswer(user_id=uid, question=q,
                                                  answer_hash=UserSecurityAnswer.hash_answer('x')))
            db.session.commit()
        seen = set()
        for _ in range(8):
            rl._buckets.clear()
            c = self.app.test_client()
            c.post('/auth/forgot', data={'identifier': 'erin'}, base_url='https://bbs.example.test')
            with c.session_transaction(base_url='https://bbs.example.test') as sess:
                seen.add(sess.get('sq_answer_id'))
        self.assertEqual(len(seen), 1, seen)
        self.assertIsNotNone(seen.pop())


class SmallWebFixes(_AppCase):
    def _font_route(self):
        for rule in self.app.url_map.iter_rules():
            if rule.endpoint.endswith('.install_fonts'):
                return rule.rule
        self.fail('font-pack install route not found')

    def test_config_values_keep_their_type(self):
        from anetbbs.web.admin import _coerce_like
        self.assertIs(_coerce_like(True, 'false'), False)
        self.assertIs(_coerce_like(False, 'on'), True)
        self.assertEqual(_coerce_like(5, '7'), 7)
        self.assertEqual(_coerce_like(5, 'x'), 5)

    def test_suggest_endpoints_need_login(self):
        c = self.app.test_client()
        for url in ('/users/suggest?q=al', '/search/suggest?q=al'):
            r = c.get(url)
            self.assertIn(r.status_code, (302, 401), url)

    def test_healthz_minimal_for_remote_anonymous(self):
        c = self.app.test_client()
        r = c.get('/healthz', environ_base={'REMOTE_ADDR': '203.0.113.9'})
        self.assertEqual(set(r.get_json()), {'status', 'db_ok'})
        r = c.get('/healthz', environ_base={'REMOTE_ADDR': '127.0.0.1'},
                  headers={'X-Forwarded-For': '203.0.113.9'})
        self.assertEqual(set(r.get_json()), {'status', 'db_ok'})
        r = c.get('/healthz', environ_base={'REMOTE_ADDR': '127.0.0.1'})
        self.assertIn('version', r.get_json())

    def test_score_input_validated(self):
        from anetbbs.models import db, Game
        with self.app.app_context():
            if not Game.query.filter_by(slug='r2-score').first():
                db.session.add(Game(slug='r2-score', name='R2', game_type='web',
                                    is_active=True))
                db.session.commit()
        c = self._login(self.alice)
        r = c.post('/games/r2-score/score', json={'score': 'lots'})
        self.assertEqual(r.status_code, 400)
        r = c.post('/games/r2-score/score', json={'score': 1, 'details': {'x': 'y' * 5000}})
        self.assertEqual(r.status_code, 400)

    def test_verification_link_ignores_host_header(self):
        from anetbbs.web.auth import _email_verification_url
        with self.app.test_request_context('/', headers={'Host': 'evil.example'}):
            url = _email_verification_url('tok', None)
        self.assertTrue(url.startswith('https://bbs.example.test/'), url)

    def test_private_mail_blocks_remote_images(self):
        from anetbbs.web.markdown_render import render_markdown
        body = '![x](https://tracker.example/p.gif) ![y](/static/ok.png)'
        out = str(render_markdown(body, remote_images=False))
        self.assertNotIn('tracker.example', out)
        self.assertIn('/static/ok.png', out)
        self.assertIn('tracker.example', str(render_markdown(body)))

    def test_deleted_pm_hidden_from_that_side(self):
        from anetbbs.models import db, PrivateMessage
        with self.app.app_context():
            pm = PrivateMessage(sender_id=self.alice, recipient_id=self.bob,
                                subject='s', body='secret')
            db.session.add(pm)
            db.session.commit()
            pid = pm.id
        bob = self._login(self.bob)
        self.assertEqual(bob.get(f'/messages/{pid}').status_code, 200)
        bob.post(f'/messages/{pid}/delete')
        self.assertEqual(bob.get(f'/messages/{pid}').status_code, 403)
        self.assertEqual(self._login(self.alice).get(f'/messages/{pid}').status_code, 200)

    def test_bulletin_feed_skips_expired(self):
        from datetime import datetime, timedelta
        from anetbbs.models import db, Message
        with self.app.app_context():
            db.session.add_all([
                Message(author_id=self.alice, title='r2-live-bulletin', content='x'),
                Message(author_id=self.alice, title='r2-expired-bulletin', content='y',
                        expires_at=datetime.utcnow() - timedelta(days=1))])
            db.session.commit()
        body = self._login(self.alice).get(self._feed_url('bulletins')).get_data(as_text=True)
        self.assertIn('r2-live-bulletin', body)
        self.assertNotIn('r2-expired-bulletin', body)

    def _feed_url(self, name):
        for rule in self.app.url_map.iter_rules():
            if rule.rule.endswith(f'/{name}.xml'):
                return rule.rule
        self.fail(f'{name}.xml route not found')

    def test_qwkhub_only_on_hub(self):
        c = self.app.test_client()
        self.app.config['REGISTRY_MODE_ENABLED'] = False
        self.assertEqual(c.get('/qwkhub/ANYNODE.QWK').status_code, 404)

    def test_dos_data_only_for_accessible_games(self):
        from anetbbs.models import db, Game
        root = os.path.join(self._tmp, 'inst')
        os.makedirs(os.path.join(root, 'data', 'dos-games'), exist_ok=True)
        for n in ('pub.zip', 'priv.zip', 'orphan.zip'):
            with open(os.path.join(root, 'data', 'dos-games', n), 'wb') as f:
                f.write(b'PK')
        self.app.config['INSTALL_DIR'] = root
        with self.app.app_context():
            for slug, n, lvl in (('r2-pub', 'pub.zip', 0), ('r2-priv', 'priv.zip', 255)):
                if not Game.query.filter_by(slug=slug).first():
                    db.session.add(Game(slug=slug, name=slug, game_type='door_dos_browser',
                                        is_active=True, min_access_level=lvl,
                                        web_game_url=f'/games/dos-data/{n}'))
            db.session.commit()
        c = self._login(self.alice)
        self.assertEqual(c.get('/games/dos-data/pub.zip').status_code, 200)
        self.assertEqual(c.get('/games/dos-data/priv.zip').status_code, 404)
        self.assertEqual(c.get('/games/dos-data/orphan.zip').status_code, 404)

    def test_addon_requires_https_and_matching_pin(self):
        from anetbbs.web import addons
        admin = self._user('r2admin', 'r2admin@example.test', admin=True)
        c = self._login(admin)
        entry = addons.ADDONS[0]
        key = entry['url_config_key']
        pin_key = key.rsplit('_URL', 1)[0] + '_SHA256'
        self.app.config[key] = 'http://example.test/a.zip'
        self.app.config.pop(pin_key, None)
        r = c.post(f"/admin/addons/{entry['id']}/install")
        self.assertEqual(r.status_code, 400)
        self.app.config[pin_key] = 'not-hex'
        self.assertEqual(c.post(f"/admin/addons/{entry['id']}/install").status_code, 400)
        tmpf = os.path.join(self._tmp, 'dl.bin')
        with open(tmpf, 'wb') as f:
            f.write(b'not the pinned file')
        self.app.config[key] = 'https://example.test/a.zip'
        self.app.config[pin_key] = '0' * 64
        try:
            from anetbbs.web import _addon_download
            with mock.patch.object(_addon_download, 'download_zip_to_tempfile', return_value=tmpf):
                r = c.post(f"/admin/addons/{entry['id']}/install")
            self.assertEqual(r.status_code, 502)
            self.assertIn('does not match', r.get_json()['error'])
            self.assertFalse(os.path.exists(tmpf))
            # The ANSI editor's own font-pack route applies the same rule.
            self.app.config['TDF_FONTS_PACK_URL'] = 'http://example.test/f.zip'
            self.app.config.pop('TDF_FONTS_PACK_SHA256', None)
            self.app.config['TDF_FONTS_DIR'] = os.path.join(self._tmp, 'fonts')
            with mock.patch.object(_addon_download, 'download_zip_to_tempfile') as dl:
                r = c.post(self._font_route())
            self.assertEqual(r.status_code, 400, r.get_data(as_text=True)[:200])
            dl.assert_not_called()
        finally:
            self.app.config.pop(pin_key, None)


if __name__ == '__main__':
    unittest.main()
