"""HTTP coverage for the Spanish UI, persisted preferences, and safe fallbacks.

Every request uses the real CSRF middleware, templates, and database. App data
and configuration live in a temporary directory; no checked-in DB is reused.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta
from itertools import count
from pathlib import Path
import sys

from bs4 import BeautifulSoup
import pytest
from sqlalchemy import event

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.models import (
    Board, EmailVerifyToken, MenuTranslation, PasswordRecoverySettings,
    PasswordResetToken, Post, PrivateMessage, SmtpConfig, User, db,
)

_client_addresses = count(1)


@pytest.fixture(scope='module')
def localized_app(tmp_path_factory):
    import anetbbs.config as cfg
    from anetbbs.web_app import create_app

    root = tmp_path_factory.mktemp('spanish-localization')
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv('FLASK_ENV', 'testing')
        patch.setenv('ANETBBS_SCHEMA_MIGRATE_ONLY', '1')
        patch.setattr(cfg.TestingConfig, 'SQLALCHEMY_DATABASE_URI',
                      f'sqlite:///{root / "bbs.sqlite"}')
        patch.setattr(cfg.TestingConfig, 'WTF_CSRF_ENABLED', True)
        patch.setattr(cfg.TestingConfig, 'SECRET_KEY', 'spanish-integration-tests')
        patch.setattr(cfg.TestingConfig, 'NUV_ENABLED', False)
        patch.setattr(cfg.TestingConfig, 'REGISTRATION_INVITE_CODE', '')
        for name in ('DATA_DIR', 'UPLOADS_DIR', 'AVATARS_DIR', 'NETWORK_JOIN_DIR',
                     'GAMES_DATA_DIR', 'ECHOMAIL_DATA_DIR', 'DOWNLOADS_DIR',
                     'FILE_BULLETINS_DIR', 'TDF_FONTS_DIR'):
            path = root / name.lower()
            path.mkdir()
            patch.setattr(cfg.TestingConfig, name, str(path))
        patch.setattr(cfg.TestingConfig, 'LOG_FILE', str(root / 'bbs.log'))
        app = create_app('testing')
        with app.app_context():
            PasswordRecoverySettings.get().security_questions_enabled = False
            smtp = SmtpConfig.query.first()
            if smtp:
                smtp.enabled = False
                smtp.email_verify_enabled = False
            db.session.commit()
        yield app
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
        for handler in list(app.logger.handlers):
            if getattr(handler, 'baseFilename', None) == str(root / 'bbs.log'):
                app.logger.removeHandler(handler)
                handler.close()


@pytest.fixture
def client(localized_app):
    browser = localized_app.test_client()
    # Registration's DB-backed per-IP limiter also needs request isolation.
    browser.environ_base['REMOTE_ADDR'] = f'2001:db8::{next(_client_addresses):x}'
    return browser


@pytest.fixture
def make_user(localized_app):
    serial = count()
    # A function-specific prefix avoids coupling cases through account data.
    import uuid
    prefix = 'locale' + uuid.uuid4().hex[:10]

    def create(language='en', **values):
        username = prefix + str(next(serial))
        with localized_app.app_context():
            user = User(username=username, email=f'{username}@example.com',
                        language=language, is_verified=True, **values)
            user.set_password('localization-password')
            db.session.add(user)
            db.session.commit()
            return user.id, user.username, user.email
    return create


def _soup(response):
    assert response.status_code == 200, response.get_data(as_text=True)[:1000]
    return BeautifulSoup(response.get_data(as_text=True), 'html.parser')


def _language(client, language):
    with client.session_transaction() as session:
        session['language'] = language


def _authenticate(client, user_id):
    with client.session_transaction() as session:
        session['_user_id'] = str(user_id)
        session['_fresh'] = True


def _post(client, path, data, *, token_path='/', follow_redirects=False):
    page = _soup(client.get(token_path))
    token = page.select_one('input[name=csrf_token]')['value']
    return client.post(path, data={**data, 'csrf_token': token},
                       follow_redirects=follow_redirects)


@contextmanager
def _overrides(app, **texts):
    with app.app_context():
        old = {}
        for key, text in texts.items():
            row = MenuTranslation.query.filter_by(lang='es', key=key).first()
            old[key] = row.text if row else None
            if row:
                row.text = text
            else:
                db.session.add(MenuTranslation(lang='es', key=key, text=text))
        db.session.commit()
    try:
        yield
    finally:
        with app.app_context():
            for key, text in old.items():
                row = MenuTranslation.query.filter_by(lang='es', key=key).one()
                if text is None:
                    db.session.delete(row)
                else:
                    row.text = text
            db.session.commit()


@pytest.mark.parametrize('language,new_here,sign_up,home', [
    ('en', 'New here?', 'Sign up', 'Home'),
    ('es', '¿Es tu primera visita?', 'Crear cuenta', 'Inicio'),
])
def test_anonymous_home_and_navigation(client, language, new_here, sign_up, home):
    _language(client, language)
    page = _soup(client.get('/'))
    assert page.html['lang'] == language
    assert new_here in page.get_text(' ', strip=True)
    assert sign_up in [a.get_text(' ', strip=True) for a in page.select('a[href="/auth/register"]')]
    assert home in [a.get_text(' ', strip=True) for a in page.select('a[href="/"]')]
    picker = page.select_one('form[action="/language"]')
    assert picker['method'] == 'post'
    assert picker.select_one('input[name=csrf_token]')
    assert picker.select_one(f'option[value={language}]').has_attr('selected')


@pytest.mark.parametrize('language,username,password,register,identifier,send', [
    ('en', 'Username', 'Password', 'Register', 'Username or Email', 'Send Reset Link'),
    ('es', 'Nombre de usuario', 'Contraseña', 'Registrarse',
     'Nombre de usuario o correo electrónico', 'Enviar enlace de recuperación'),
])
def test_authentication_and_recovery_form_labels(client, language, username, password,
                                                 register, identifier, send):
    _language(client, language)
    login = _soup(client.get('/auth/login'))
    assert login.select_one('label[for=username]').get_text(strip=True) == username
    assert login.select_one('label[for=password]').get_text(strip=True) == password
    registration = _soup(client.get('/auth/register'))
    assert registration.select_one('input[type=submit]')['value'] == register
    forgot = _soup(client.get('/auth/forgot'))
    assert forgot.select_one('label[for=identifier]').get_text(strip=True) == identifier
    assert forgot.select_one('form:not([action]) button').get_text(strip=True) == send


@pytest.mark.parametrize('language,required,invalid_credentials,invalid_email,mismatch', [
    # Spanish first catches accidental mutation of shared WTForms validators
    # when the following English request binds the same form class.
    ('es', 'Este campo es obligatorio.', 'Nombre de usuario o contraseña incorrectos',
     'Dirección de correo electrónico no válida.', 'Las contraseñas deben coincidir'),
    ('en', 'This field is required.', 'Invalid username or password',
     'Invalid email address.', 'Passwords must match'),
])
def test_auth_validation_uses_request_language(client, language, required,
                                              invalid_credentials, invalid_email, mismatch):
    _language(client, language)
    empty = _soup(_post(client, '/auth/login', {}, token_path='/auth/login'))
    assert required in empty.get_text(' ', strip=True)
    bad_login = _soup(_post(client, '/auth/login', {
        'username': 'no-such-localized-user', 'password': 'incorrect',
    }, token_path='/auth/login', follow_redirects=True))
    assert invalid_credentials in bad_login.get_text(' ', strip=True)
    invalid = _soup(_post(client, '/auth/register', {
        'username': 'valid-localized-name', 'email': 'not-an-email',
        'password': 'password-one', 'password2': 'password-two',
    }, token_path='/auth/register'))
    text = invalid.get_text(' ', strip=True)
    assert invalid_email in text
    assert mismatch in text


def test_anonymous_cookie_resolution_and_session_priority(localized_app):
    client = localized_app.test_client()
    client.set_cookie('anetbbs_language', 'es')
    assert _soup(client.get('/')).html['lang'] == 'es'
    _language(client, 'en')
    assert _soup(client.get('/')).html['lang'] == 'en'
    other = localized_app.test_client()
    other.set_cookie('anetbbs_language', 'unsupported')
    assert _soup(other.get('/')).html['lang'] == 'en'


def test_language_picker_requires_csrf_and_persists_secure_cookie(localized_app, client, monkeypatch):
    rejected = client.post('/language', data={'language': 'es'})
    assert rejected.status_code == 400
    with client.session_transaction() as session:
        assert 'language' not in session
        assert '_user_id' not in session
    monkeypatch.setitem(localized_app.config, 'SESSION_COOKIE_SECURE', True)
    response = _post(client, '/language', {'language': 'es', 'return_to': '/auth/login'})
    assert response.status_code == 302
    assert response.headers['Location'] == '/auth/login'
    cookie = client.get_cookie('anetbbs_language')
    assert cookie.value == 'es'
    assert cookie.max_age == 365 * 24 * 60 * 60
    assert cookie.http_only and cookie.secure and cookie.same_site == 'Lax'
    assert cookie.path == '/' and cookie.expires is not None
    with client.session_transaction() as session:
        assert session['language'] == 'es'
        assert '_user_id' not in session
    # The preference survives losing the login/session cookie.
    client.delete_cookie(localized_app.config['SESSION_COOKIE_NAME'])
    assert _soup(client.get('/auth/login')).html['lang'] == 'es'


def test_language_picker_rejects_unknown_values(client):
    for language in ('de', 'pt', '../es', ''):
        response = _post(client, '/language', {'language': language})
        assert response.status_code == 400
    with client.session_transaction() as session:
        assert 'language' not in session


@pytest.mark.parametrize('target', [
    'https://example.net/evil', '//example.net/evil', '/\\example.net/evil',
    'https://[broken', '/boards/\nLocation: https://example.net',
])
def test_language_picker_refuses_external_or_malformed_redirects(client, target):
    response = _post(client, '/language', {'language': 'es', 'return_to': target})
    assert response.status_code == 302
    assert response.headers['Location'] == '/'


def test_language_picker_preserves_relative_query_and_fragment(client):
    target = '/boards/?search=some%20text&page=2#recent'
    response = _post(client, '/language', {'language': 'es', 'return_to': target})
    assert response.status_code == 302
    assert response.headers['Location'] == target


@pytest.mark.parametrize('preferred,anonymous', [('es', 'en'), ('en', 'es')])
def test_login_account_preference_wins_and_survives_logout(client, make_user, preferred, anonymous):
    user_id, username, _ = make_user(preferred)
    _language(client, anonymous)
    response = _post(client, '/auth/login', {
        'username': username, 'password': 'localization-password',
    }, token_path='/auth/login', follow_redirects=True)
    assert _soup(response).html['lang'] == preferred
    with client.session_transaction() as session:
        assert session['_user_id'] == str(user_id)
        assert session['language'] == preferred
    assert client.get_cookie('anetbbs_language').value == preferred
    # An authenticated account also wins over stale browser preferences.
    _language(client, anonymous)
    client.set_cookie('anetbbs_language', anonymous)
    assert _soup(client.get('/')).html['lang'] == preferred
    logged_out = _soup(client.get('/auth/logout', follow_redirects=True))
    assert logged_out.html['lang'] == preferred
    with client.session_transaction() as session:
        assert '_user_id' not in session
        assert session['language'] == preferred


@pytest.mark.parametrize('language', ['en', 'es'])
def test_registration_inherits_anonymous_preference(localized_app, client, language):
    import uuid
    username = 'registered' + uuid.uuid4().hex[:10]
    _language(client, language)
    response = _post(client, '/auth/register', {
        'username': username, 'email': f'{username}@example.com',
        'password': 'registered-password', 'password2': 'registered-password',
    }, token_path='/auth/register', follow_redirects=True)
    assert _soup(response).html['lang'] == language
    with localized_app.app_context():
        user = User.query.filter_by(username=username).one()
        assert user.language == language
        user_id = user.id
    with client.session_transaction() as session:
        assert session['_user_id'] == str(user_id)
        assert session['language'] == language


def test_email_verification_restores_account_language(localized_app, client, make_user):
    user_id, _, _ = make_user('es')
    with localized_app.app_context():
        user = db.session.get(User, user_id)
        user.is_verified = False
        db.session.add(EmailVerifyToken(user_id=user_id, token='spanish-verify-token',
                                       expires_at=datetime.utcnow() + timedelta(hours=1)))
        db.session.commit()
    _language(client, 'en')
    page = _soup(client.get('/auth/verify/spanish-verify-token'))
    assert page.html['lang'] == 'es'
    assert '¡Correo electrónico verificado!' in page.get_text(' ', strip=True)
    with client.session_transaction() as session:
        assert session['language'] == 'es'
        assert session['_user_id'] == str(user_id)


@pytest.mark.parametrize('language,new_password,updated', [
    ('en', 'New Password', 'Password updated. You can log in now.'),
    ('es', 'Nueva contraseña', 'Contraseña actualizada. Ya puedes iniciar sesión.'),
])
def test_recovery_reset_form_and_success(localized_app, client, make_user, language,
                                        new_password, updated):
    user_id, _, _ = make_user()
    token = f'localized-reset-{user_id}'
    with localized_app.app_context():
        db.session.add(PasswordResetToken(user_id=user_id, token=token,
                                         expires_at=datetime.utcnow() + timedelta(hours=1)))
        db.session.commit()
    _language(client, language)
    path = '/auth/reset/' + token
    page = _soup(client.get(path))
    assert page.select_one('label[for=password]').get_text(strip=True) == new_password
    result = _soup(_post(client, path, {'password': 'new-password', 'password2': 'new-password'},
                         token_path=path, follow_redirects=True))
    assert updated in result.get_text(' ', strip=True)
    with localized_app.app_context():
        assert db.session.get(User, user_id).check_password('new-password')
        assert PasswordResetToken.query.filter_by(token=token).one().used_at is not None


@pytest.mark.parametrize('language,heading,subject_label,created,replied', [
    ('en', 'Message Boards', 'Subject', 'Post created successfully!', 'Reply posted successfully!'),
    ('es', 'Tablones de mensajes', 'Asunto', '¡Mensaje publicado correctamente!',
     '¡Respuesta publicada correctamente!'),
])
def test_board_list_read_create_and_reply_preserve_content(localized_app, client, make_user,
                                                          language, heading, subject_label,
                                                          created, replied):
    user_id, _, _ = make_user(language)
    _authenticate(client, user_id)
    board_name = f'Local board {user_id} <script>alert("board")</script>'
    with localized_app.app_context():
        board = Board(name=board_name, description='User description <b>verbatim</b>',
                      min_access_level=0)
        db.session.add(board)
        db.session.commit()
        board_id = board.id
    listing = _soup(client.get('/boards/'))
    assert heading in listing.h1.get_text(' ', strip=True)
    assert board_name in listing.get_text(' ', strip=True)
    assert '<script>alert("board")</script>' not in str(listing)
    board_page = _soup(client.get(f'/boards/{board_id}'))
    assert board_name in board_page.get_text(' ', strip=True)
    path = f'/boards/{board_id}/new'
    form = _soup(client.get(path))
    assert form.select_one('label[for=subject]').get_text(strip=True) == subject_label
    invalid = _soup(_post(client, path, {'subject': '', 'content': ''}, token_path=path))
    assert ('Este campo es obligatorio.' if language == 'es' else 'This field is required.') in invalid.get_text()
    subject = 'Original subject <img src=x onerror=alert(1)>'
    content = 'Original message <script>alert("post")</script> & café.'
    page = _soup(_post(client, path, {'subject': subject, 'content': content},
                       token_path=path, follow_redirects=True))
    assert created in page.get_text(' ', strip=True)
    assert subject in page.get_text(' ', strip=True)
    assert '<script>alert("post")</script>' not in str(page)
    assert page.select_one('#postBodyPlain').get_text() == content
    with localized_app.app_context():
        post = Post.query.filter_by(board_id=board_id, parent_id=None).one()
        assert (post.subject, post.content) == (subject, content)
        post_id = post.id
    reply = 'User reply <b>unaltered</b> & español.'
    page = _soup(_post(client, f'/boards/post/{post_id}/reply', {'content': reply},
                       token_path=f'/boards/post/{post_id}', follow_redirects=True))
    assert replied in page.get_text(' ', strip=True)
    assert '<b>unaltered</b>' not in str(page)
    assert reply in [
        body.get_text() for body in page.select('.ansi-body')
    ]
    with localized_app.app_context():
        assert Post.query.filter_by(parent_id=post_id).one().content == reply


@pytest.mark.parametrize('language,inbox,compose,recipient_label,sent,quote', [
    ('en', 'Inbox', 'Compose Message', 'To (username)', 'Message sent!', 'wrote:'),
    ('es', 'Bandeja de entrada', 'Redactar mensaje', 'Para (nombre de usuario)',
     '¡Mensaje enviado!', 'escribió:'),
])
def test_private_message_inbox_read_compose_and_quote_reply(localized_app, client, make_user,
                                                           language, inbox, compose,
                                                           recipient_label, sent, quote):
    user_id, username, _ = make_user(language)
    sender_id, sender, _ = make_user()
    body = 'Original PM <script>alert("pm")</script> & ñáéü.'
    subject = 'User PM <b>subject</b>'
    with localized_app.app_context():
        pm = PrivateMessage(sender_id=sender_id, recipient_id=user_id,
                            subject=subject, body=body)
        db.session.add(pm)
        db.session.commit()
        message_id = pm.id
    _authenticate(client, user_id)
    page = _soup(client.get('/messages/'))
    assert page.h1.get_text(' ', strip=True) == inbox
    assert subject in page.get_text(' ', strip=True)
    assert '<b>subject</b>' not in str(page)
    read = _soup(client.get(f'/messages/{message_id}'))
    assert '<script>alert("pm")</script>' not in str(read)
    # PMs are already decoded Unicode. The read view must keep accents intact
    # rather than interpreting their code points as legacy CP437 bytes.
    assert read.select_one('#bodyPlain').get_text() == body
    with localized_app.app_context():
        stored = db.session.get(PrivateMessage, message_id)
        assert stored.read_at is not None
        assert stored.body == body
    quote_path = f'/messages/compose/{sender}?quote={message_id}'
    form = _soup(client.get(quote_path))
    assert form.h1.get_text(' ', strip=True) == compose
    assert form.select_one('label[for=recipient]').get_text(strip=True) == recipient_label
    assert form.select_one('input[name=recipient]')['value'] == sender
    assert form.select_one('input[name=subject]')['value'] == f'Re: {subject}'
    assert form.select_one('textarea[name=content]').get_text().strip() == f'{sender} {quote}\n> {body}'
    reply = 'Original reply <img src=x onerror=alert(1)> & ñáéü.'
    result = _soup(_post(client, f'/messages/compose/{sender}', {
        'recipient': sender, 'subject': f'Re: {subject}', 'content': reply,
    }, token_path=quote_path, follow_redirects=True))
    assert sent in result.get_text(' ', strip=True)
    with localized_app.app_context():
        pm = PrivateMessage.query.filter_by(sender_id=user_id, recipient_id=sender_id).one()
        assert (pm.subject, pm.body) == (f'Re: {subject}', reply)


@pytest.mark.parametrize('language', ['en', 'es'])
def test_private_message_unicode_preserves_ansi_escaping_and_plain_links(
        localized_app, client, make_user, language):
    recipient_id, _, _ = make_user(language)
    sender_id, _, _ = make_user()
    url = 'https://example.net/image.png'
    body = f'PM \x1b[31mrojo\x1b[0m ñáéü <script>evil</script> {url}'
    with localized_app.app_context():
        message = PrivateMessage(sender_id=sender_id, recipient_id=recipient_id,
                                 subject='ANSI and Unicode', body=body)
        db.session.add(message)
        db.session.commit()
        message_id = message.id
    _authenticate(client, recipient_id)
    page = _soup(client.get(f'/messages/{message_id}'))
    rendered = page.select_one('#bodyPlain')
    assert rendered.get_text() == f'PM rojo ñáéü <script>evil</script> {url}'
    colored = [span for span in rendered.select('span')
               if span.get_text() == 'rojo' and 'color:' in span.get('style', '')]
    assert colored
    assert rendered.select_one('script, img') is None
    assert rendered.select_one('a')['href'] == url
    with localized_app.app_context():
        assert db.session.get(PrivateMessage, message_id).body == body


@pytest.mark.parametrize('initial,target,label,placeholder', [
    ('en', 'es', 'Language', 'Your display name'),
    ('es', 'en', 'Idioma', 'Tu nombre visible'),
])
def test_profile_language_change_updates_account_session_and_cookie(localized_app, client,
                                                                    make_user, initial,
                                                                    target, label, placeholder):
    user_id, username, email = make_user(initial)
    _authenticate(client, user_id)
    page = _soup(client.get('/profile/edit'))
    assert page.select_one('label[for=language]').get_text(strip=True) == label
    assert page.select_one('input[name=display_name]')['placeholder'] == placeholder
    assert {o['value'] for o in page.select('#language option')} == {'en', 'es', 'de', 'pt'}
    result = _soup(_post(client, '/profile/edit', {
        'email': email, 'language': target, 'theme_id': '0',
        'display_name': 'Untranslated <b>name</b>', 'bio': 'User bio <script>example</script>',
    }, token_path='/profile/edit', follow_redirects=True))
    assert result.html['lang'] == target
    assert '<b>name</b>' not in str(result)
    with localized_app.app_context():
        user = db.session.get(User, user_id)
        assert user.language == target
        assert user.display_name == 'Untranslated <b>name</b>'
        assert user.bio == 'User bio <script>example</script>'
    with client.session_transaction() as session:
        assert session['language'] == target
    assert client.get_cookie('anetbbs_language').value == target


def test_profile_preserves_existing_custom_language(localized_app, client, make_user):
    user_id, _, email = make_user('eo')
    _authenticate(client, user_id)
    page = _soup(client.get('/profile/edit'))
    custom = page.select_one('#language option[value=eo]')
    assert custom is not None and custom.has_attr('selected')
    assert page.select_one('#site-language option[value=eo]').has_attr('selected')
    unchanged = _post(client, '/language', {'language': 'eo', 'return_to': '/profile/edit'},
                      token_path='/profile/edit')
    assert unchanged.status_code == 302
    rejected = _post(client, '/language', {'language': 'unknown'}, token_path='/profile/edit')
    assert rejected.status_code == 400
    result = _post(client, '/profile/edit', {'email': email, 'language': 'eo', 'theme_id': '0'},
                   token_path='/profile/edit')
    assert result.status_code == 302
    with localized_app.app_context():
        assert db.session.get(User, user_id).language == 'eo'
    logged_out = _soup(client.get('/auth/logout', follow_redirects=True))
    assert logged_out.select_one('#site-language option[value=eo]').has_attr('selected')
    unchanged = _post(client, '/language', {'language': 'eo'}, token_path='/auth/login')
    assert unchanged.status_code == 302


def test_picker_updates_authenticated_account(localized_app, client, make_user):
    user_id, _, _ = make_user('en')
    _authenticate(client, user_id)
    rejected = client.post('/language', data={'language': 'es'})
    assert rejected.status_code == 400
    with localized_app.app_context():
        assert db.session.get(User, user_id).language == 'en'
    response = _post(client, '/language', {'language': 'es', 'return_to': '/profile/edit'})
    assert response.status_code == 302
    with localized_app.app_context():
        assert db.session.get(User, user_id).language == 'es'
    assert _soup(client.get('/profile/edit')).html['lang'] == 'es'


def test_custom_overrides_are_preserved_escaped_and_fall_back_safely(localized_app, client, make_user):
    from anetbbs.features.i18n import seed_default_translations, translate
    from flask import render_template_string, session

    hostile = '<img src=x onerror="alert(1)">'
    with _overrides(localized_app, **{
        'nav.home': hostile,
        'test.format': 'Incorrect {missing}',
        'test.malformed': 'Incorrect {name',
        'test.position': 'Incorrect {0}',
        'test.attribute': 'Incorrect {name.missing}',
        'test.subscript': 'Incorrect {name[bad]}',
        'test.named': 'Hola {name}',
    }):
        with localized_app.app_context():
            seed_default_translations()
            assert MenuTranslation.query.filter_by(lang='es', key='nav.home').one().text == hostile
        _language(client, 'es')
        page = _soup(client.get('/'))
        assert hostile in [a.get_text(' ', strip=True) for a in page.select('a[href="/"]')]
        assert page.select_one('img[onerror]') is None
        with localized_app.test_request_context('/'):
            session['language'] = 'es'
            for key in ('test.format', 'test.malformed', 'test.position',
                        'test.attribute', 'test.subscript'):
                assert translate(key, 'Hello {name}', name='Ana') == 'Hello Ana'
            assert translate('test.missing', 'First fallback') == 'First fallback'
            assert translate('test.missing', 'Second fallback') == 'Second fallback'
            rendered = render_template_string("{{ t('test.named', 'Hello {name}', name=name) }}",
                                              name=hostile)
            assert BeautifulSoup(rendered, 'html.parser').get_text() == 'Hola ' + hostile
            assert '<img' not in rendered


@pytest.mark.parametrize('language,expected', [('en', 0), ('es', 1)])
def test_translation_catalogue_is_loaded_once_per_request(localized_app, client, language, expected):
    _language(client, language)
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        if 'from menu_translations' in ' '.join(statement.lower().split()):
            statements.append(statement)

    with localized_app.app_context():
        engine = db.engine
        event.listen(engine, 'before_cursor_execute', capture)
    try:
        for path in ('/', '/auth/register'):
            statements.clear()
            assert client.get(path).status_code == 200
            assert len(statements) == expected
    finally:
        event.remove(engine, 'before_cursor_execute', capture)


@pytest.mark.parametrize('override,expected', [
    ('Entre %(minimum)d y %(max)d caracteres.',
     'Field must be between 3 and 80 characters long.'),
    ('Entre %(min)z y %(max)d caracteres.',
     'Field must be between 3 and 80 characters long.'),
    ('Al menos %(min)d caracteres.',
     'Field must be between 3 and 80 characters long.'),
    ('Entre %(min)d y %(max)d caracteres (100%%).',
     'Entre 3 y 80 caracteres (100%).'),
])
def test_validation_override_preserves_percent_markers(localized_app, client, override, expected):
    _language(client, 'es')
    with _overrides(localized_app, **{'validation.length_range': override}):
        page = _soup(_post(client, '/auth/login', {
            'username': 'ab', 'password': 'invalid-form-only',
        }, token_path='/auth/login'))
        assert expected in page.get_text(' ', strip=True)
        assert page.select_one('label[for=password]').get_text(strip=True) == 'Contraseña'


@pytest.mark.parametrize('language,expected', [
    ('en', 'Security questions configured: 1'),
    ('es', 'Preguntas configuradas: 1'),
])
def test_one_security_question_uses_count_neutral_wording(
        localized_app, client, make_user, language, expected):
    from anetbbs.models import SecurityQuestion, UserSecurityAnswer
    user_id, _, _ = make_user(language)
    _authenticate(client, user_id)
    with localized_app.app_context():
        previous = PasswordRecoverySettings.get().security_questions_enabled
        PasswordRecoverySettings.get().security_questions_enabled = True
        question = SecurityQuestion.query.filter_by(is_active=True).first().text
        db.session.add(UserSecurityAnswer(user_id=user_id, question=question,
                                         answer_hash=UserSecurityAnswer.hash_answer('local answer')))
        db.session.commit()
    try:
        page = _soup(client.get('/profile/security-questions'))
        assert expected in page.select_one('.alert-success').get_text(' ', strip=True)
    finally:
        with localized_app.app_context():
            PasswordRecoverySettings.get().security_questions_enabled = previous
            db.session.commit()
