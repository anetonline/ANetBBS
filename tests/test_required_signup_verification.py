import ssl
from unittest.mock import Mock
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from anetbbs import mailer
from anetbbs.models import EmailVerifyToken, SmtpConfig, User, db
from tests.test_spanish_localization import client, localized_app, _post


@pytest.fixture
def signup(localized_app, monkeypatch):
    monkeypatch.setitem(localized_app.config, 'REGISTRATION_REQUIRE_VERIFIED_EMAIL', True)
    monkeypatch.setitem(localized_app.config, 'PUBLIC_BASE_URL', 'https://bbs.example.test')
    with localized_app.app_context():
        smtp = SmtpConfig.query.first()
        if smtp is None:
            smtp = SmtpConfig()
            db.session.add(smtp)
        smtp.enabled = True
        smtp.host = 'mail.example.test'
        smtp.from_address = 'bbs@example.test'
        smtp.email_verify_enabled = True
        smtp.use_tls = True
        smtp.use_ssl = False
        db.session.commit()

    username = f'confirmed{uuid4().hex[:12]}'
    return {
        'username': username,
        'email': f'{username}@example.com',
        'password': 'a-local-registration-password',
        'password2': 'a-local-registration-password',
    }


def _register(client, signup):
    return _post(client, '/auth/register', signup, token_path='/auth/register')


def _assert_not_logged_in(client):
    with client.session_transaction() as session:
        assert '_user_id' not in session


@pytest.mark.parametrize('missing', ['smtp', 'email verification', 'SMTP TLS', 'HTTPS', 'NUV conflict'])
def test_required_verification_refuses_signup_when_unavailable(
        localized_app, client, signup, monkeypatch, missing):
    if missing in {'smtp', 'email verification', 'SMTP TLS'}:
        with localized_app.app_context():
            smtp = SmtpConfig.query.first()
            if missing == 'smtp':
                smtp.enabled = False
            elif missing == 'email verification':
                smtp.email_verify_enabled = False
            else:
                smtp.use_tls = False
                smtp.use_ssl = False
            db.session.commit()
    elif missing == 'HTTPS':
        monkeypatch.setitem(localized_app.config, 'PUBLIC_BASE_URL', 'http://bbs.example.test')
    else:
        monkeypatch.setitem(localized_app.config, 'NUV_ENABLED', True)

    assert _register(client, signup).status_code == 503
    _assert_not_logged_in(client)
    with localized_app.app_context():
        assert User.query.filter_by(username=signup['username']).first() is None


def test_mail_failure_keeps_account_unverified_and_resend_allows_activation(
        localized_app, client, signup, monkeypatch):
    send = Mock(side_effect=[(False, 'relay unavailable'), (True, '')])
    monkeypatch.setattr(mailer, 'send_verification_email', send)
    assert _register(client, signup).status_code == 200

    credentials = {k: signup[k] for k in ('username', 'password')}
    _post(client, '/auth/login', credentials, token_path='/auth/login')
    _assert_not_logged_in(client)
    with localized_app.app_context():
        assert not User.query.filter_by(username=signup['username']).one().is_verified

    resend = '/auth/verify/resend'
    assert _post(client, resend, {
        'identifier': signup['username'], 'password': signup['password'],
    }, token_path=resend).status_code == 200
    links = [urlsplit(call.args[1]) for call in send.call_args_list]
    assert len(links) == 2
    assert {(link.scheme, link.netloc) for link in links} == {('https', 'bbs.example.test')}
    path = links[-1].path

    result = client.get(path)
    assert urlsplit(result.location).path == '/auth/login'
    _assert_not_logged_in(client)
    with localized_app.app_context():
        user = User.query.filter_by(username=signup['username']).one()
        assert user.is_verified and not user.is_admin
        token = EmailVerifyToken.query.filter_by(token=path.rsplit('/', 1)[-1]).one()
        assert token.used_at is not None

    assert client.get(path).status_code == 302
    _assert_not_logged_in(client)
    assert _post(client, '/auth/login', credentials, token_path='/auth/login').status_code == 302
    assert client.get('/admin/').status_code == 403
    assert client.get('/admin/db-backup').status_code == 403


@pytest.mark.parametrize('implicit_tls', [True, False])
def test_strict_signup_validates_smtp_relay_certificate(
        localized_app, signup, monkeypatch, implicit_tls):
    smtp_factory = Mock()
    monkeypatch.setattr(mailer.smtplib, 'SMTP_SSL' if implicit_tls else 'SMTP', smtp_factory)
    with localized_app.app_context():
        smtp = SmtpConfig.query.first()
        smtp.use_ssl = implicit_tls
        smtp.use_tls = not implicit_tls
        db.session.commit()
        assert mailer.send_email(signup['email'], 'Verification', 'Test') == (True, '')
    start_tls = smtp_factory if implicit_tls else smtp_factory.return_value.starttls
    context = start_tls.call_args.kwargs['context']
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED
