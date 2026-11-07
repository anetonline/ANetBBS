"""Recovery links use administrator configuration, never request authority."""
from unittest import mock
from urllib.parse import urlsplit

import pytest


@pytest.fixture(scope='module')
def recovery_app(tmp_path_factory):
    import anetbbs.config as cfg
    from anetbbs.web_app import create_app
    from anetbbs.models import db, User, PasswordRecoverySettings

    old_uri = cfg.TestingConfig.SQLALCHEMY_DATABASE_URI
    cfg.TestingConfig.SQLALCHEMY_DATABASE_URI = (
        'sqlite:///' + str(tmp_path_factory.mktemp('recovery-origin') / 'bbs.db'))
    try:
        app = create_app('testing')
        with app.app_context():
            user = User(username='originuser', email='originuser@example.invalid')
            user.set_password('old-password')
            db.session.add(user)
            PasswordRecoverySettings.get().security_questions_enabled = False
            db.session.commit()
        yield app
    finally:
        cfg.TestingConfig.SQLALCHEMY_DATABASE_URI = old_uri


@pytest.fixture
def app(recovery_app):
    from anetbbs.models import db, PasswordResetToken

    old_config = recovery_app.config.copy()
    old_wsgi = recovery_app.wsgi_app
    recovery_app.config.update(PUBLIC_BASE_URL='https://bbs.gks.invalid:8443/',
                               BBS_DOMAIN='')
    with recovery_app.app_context():
        PasswordResetToken.query.delete()
        db.session.commit()
    yield recovery_app
    recovery_app.config.clear()
    recovery_app.config.update(old_config)
    recovery_app.wsgi_app = old_wsgi


def _request_email(app, base_url, headers=None):
    """Capture the real background callback, without SMTP or network traffic."""
    with mock.patch('anetbbs.mailer.smtp_enabled', return_value=True), \
            mock.patch('anetbbs.mailer.send_password_reset_email') as send, \
            mock.patch('anetbbs.web.auth.threading.Thread') as thread:
        response = app.test_client().post(
            '/auth/forgot', base_url=base_url,
            headers=headers, data={'identifier': 'originuser'})
        if thread.called:
            call = thread.call_args.kwargs
            call['target'](*call['args'])
        urls = [call.args[1] for call in send.call_args_list]
    return response, urls


def test_email_uses_canonical_scheme_host_and_port(app):
    response, urls = _request_email(app, 'http://bbs.gks.invalid:8443')
    assert response.status_code == 200
    assert len(urls) == 1
    parsed = urlsplit(urls[0])
    assert (parsed.scheme, parsed.netloc) == ('https', 'bbs.gks.invalid:8443')
    assert parsed.path.startswith('/auth/reset/')


@pytest.mark.parametrize('path', [
    '/auth/forgot', '/auth/forgot/verify', '/auth/reset/fictitious-token',
])
def test_recovery_rejects_untrusted_host_before_issuing_tokens(app, path):
    from anetbbs.models import PasswordResetToken

    response = app.test_client().get(path, base_url='https://attacker.invalid')
    assert response.status_code == 400
    with app.app_context():
        assert PasswordResetToken.query.count() == 0


@pytest.mark.parametrize('untrusted', [
    'https://attacker.invalid', 'https://bbs.gks.invalid',
    'https://bbs.gks.invalid.attacker.invalid:8443',
])
def test_host_poisoned_post_is_rejected_without_mail(app, untrusted):
    response, urls = _request_email(app, untrusted)
    assert response.status_code == 400
    assert urls == []


def test_untrusted_forwarding_headers_cannot_change_email_origin(app):
    response, urls = _request_email(
        app, 'https://bbs.gks.invalid:8443',
        {'X-Forwarded-Host': 'attacker.invalid', 'X-Forwarded-Proto': 'http',
         'Forwarded': 'host=attacker.invalid;proto=http'})
    assert response.status_code == 200
    assert urlsplit(urls[0]).netloc == 'bbs.gks.invalid:8443'
    assert urlsplit(urls[0]).scheme == 'https'


def test_proxy_rewritten_host_still_must_match_configured_origin(app):
    from werkzeug.middleware.proxy_fix import ProxyFix

    app.wsgi_app = ProxyFix(app.wsgi_app, x_host=1, x_proto=1)
    response, urls = _request_email(
        app, 'https://bbs.gks.invalid:8443',
        {'X-Forwarded-Host': 'attacker.invalid', 'X-Forwarded-Proto': 'https'})
    assert response.status_code == 400
    assert urls == []


def test_existing_bbs_domain_supplies_https_origin(app):
    app.config.update(PUBLIC_BASE_URL='', BBS_DOMAIN='legacy.gks.invalid')
    response, urls = _request_email(app, 'https://legacy.gks.invalid')
    assert response.status_code == 200
    assert urlsplit(urls[0]).scheme == 'https'
    assert urlsplit(urls[0]).netloc == 'legacy.gks.invalid'


def test_missing_origin_is_generic_and_issues_no_token_or_email(app):
    from bs4 import BeautifulSoup
    from anetbbs.models import PasswordResetToken

    app.config.update(PUBLIC_BASE_URL='', BBS_DOMAIN='')
    response, urls = _request_email(app, 'https://attacker.invalid')
    unknown = app.test_client().post(
        '/auth/forgot', base_url='https://attacker.invalid',
        data={'identifier': 'unknownuser'})
    assert response.status_code == unknown.status_code == 200
    # The layout has an unrelated random quote; compare the recovery content.
    visible = BeautifulSoup(response.data, 'html.parser').select_one('.card-body')
    other = BeautifulSoup(unknown.data, 'html.parser').select_one('.card-body')
    assert visible.get_text(' ', strip=True) == other.get_text(' ', strip=True)
    assert 'If an account matches what you entered' in visible.get_text()
    assert urls == []
    with app.app_context():
        assert PasswordResetToken.query.count() == 0
    assert app.test_client().get('/auth/reset/fictitious-token').status_code == 503
    assert app.test_client().get('/auth/forgot/verify').status_code == 503


@pytest.mark.parametrize('invalid', [
    'ftp://bbs.gks.invalid', '//bbs.gks.invalid',
    'https://user:password@bbs.gks.invalid', 'https://bbs.gks.invalid/path',
    'https://bbs.gks.invalid?next=attacker', 'https://bbs.gks.invalid#fragment',
    'https://bbs.gks.invalid:bad', 'https://bbs.gks.invalid:65536',
    'https://bbs.gks.invalid:0', 'https://bbs.gks.invalid\\@attacker.invalid',
    'https://bbs.gks.invalid\n', 'https://', 'https://bbs%2einvalid',
])
def test_invalid_configured_origin_fails_closed(app, invalid):
    from anetbbs.models import PasswordResetToken

    app.config['PUBLIC_BASE_URL'] = invalid
    response, urls = _request_email(app, 'https://attacker.invalid')
    assert response.status_code == 200
    assert urls == []
    with app.app_context():
        assert PasswordResetToken.query.count() == 0


def test_recovery_host_restriction_does_not_apply_to_login(app):
    response = app.test_client().get('/auth/login', base_url='https://other.invalid')
    assert response.status_code == 200


def test_international_origin_preserves_modern_dns_identity(app):
    from anetbbs.web.auth import _password_recovery_origin, _check_password_recovery_host

    app.config['PUBLIC_BASE_URL'] = 'https://faß.invalid'
    with app.test_request_context('/auth/forgot', base_url='https://localhost',
                                  headers={'Host': 'xn--fa-hia.invalid'}):
        assert _password_recovery_origin() == 'https://xn--fa-hia.invalid'
        assert _check_password_recovery_host() is None


def test_international_origin_never_issues_tokens_to_idna2003_alias(app):
    from anetbbs.models import PasswordResetToken

    app.config['PUBLIC_BASE_URL'] = 'https://faß.invalid'
    response, urls = _request_email(app, 'https://fass.invalid')
    assert response.status_code == 400
    assert urls == []
    with app.app_context():
        assert PasswordResetToken.query.count() == 0


def test_international_origin_email_uses_the_browser_ascii_hostname(app):
    app.config['PUBLIC_BASE_URL'] = 'https://faß.invalid'
    # Dispatch the real endpoint without the test client's cookie jar,
    # whose URL decoding itself uses IDNA2003 and rejects this valid host.
    with app.test_request_context(
            '/auth/forgot', method='POST', base_url='https://localhost',
            headers={'Host': 'xn--fa-hia.invalid'},
            data={'identifier': 'originuser'}), \
            mock.patch('anetbbs.mailer.smtp_enabled', return_value=True), \
            mock.patch('anetbbs.mailer.send_password_reset_email') as send, \
            mock.patch('anetbbs.web.auth.threading.Thread') as thread:
        response = app.full_dispatch_request()
        callback = thread.call_args.kwargs
        callback['target'](*callback['args'])
    assert response.status_code == 200
    assert urlsplit(send.call_args.args[1]).netloc == 'xn--fa-hia.invalid'


@pytest.mark.parametrize(('configured', 'requested'), [
    ('[0:0:0:0:0:0:0:1]', '[::1]'),
    ('[::1]', '[0:0:0:0:0:0:0:1]'),
])
def test_equivalent_ipv6_authorities_use_one_canonical_origin(app, configured, requested):
    app.config['PUBLIC_BASE_URL'] = f'http://{configured}:5000'
    response, urls = _request_email(app, f'http://{requested}:5000')
    assert response.status_code == 200
    assert urlsplit(urls[0]).netloc == '[::1]:5000'
