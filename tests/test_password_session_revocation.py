"""H06: real session/remember cookies stop working after password replacement."""
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def account(tmp_path, monkeypatch):
    import anetbbs.config as config
    from anetbbs.models import User, db
    from anetbbs.web_app import create_app

    uri = f'sqlite:///{tmp_path / "auth.db"}'
    monkeypatch.setenv('FLASK_ENV', 'testing')
    monkeypatch.setattr(config.TestingConfig, 'SQLALCHEMY_DATABASE_URI', uri)
    app = create_app('testing')
    with app.app_context():
        user = User(username='recovery-user', email='recovery@example.test')
        user.set_password('original-password')
        other = User(username='other-user', email='other@example.test')
        other.set_password('original-password')
        db.session.add_all([user, other])
        db.session.commit()
        uid = user.id
    yield app, uid
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


def _login(app, username='recovery-user', password='original-password'):
    client = app.test_client()
    response = client.post('/auth/login', data={
        'username': username, 'password': password})
    assert response.status_code == 302
    assert client.get('/profile/change-password').status_code == 200
    return client


def _remember_only(app, client):
    cookie = client.get_cookie('remember_token')
    assert cookie is not None
    remembered = app.test_client()
    remembered.set_cookie('remember_token', cookie.value)
    return remembered


def _assert_revoked(client):
    response = client.get('/profile/change-password', follow_redirects=False)
    assert response.status_code == 302
    assert '/auth/login' in response.location


def test_change_password_revokes_old_cookies_but_keeps_current_browser(account):
    app, _ = account
    current = _login(app)
    old = _login(app)
    remembered = _remember_only(app, old)
    unaffected = _login(app, 'other-user')
    response = current.post('/profile/change-password', data={
        'current_password': 'original-password',
        'new_password': 'replacement-password',
        'confirm_password': 'replacement-password'})
    assert response.status_code == 302
    assert current.get('/profile/change-password').status_code == 200
    _assert_revoked(old)
    _assert_revoked(remembered)
    assert unaffected.get('/profile/change-password').status_code == 200
    _login(app, password='replacement-password')


def test_reset_password_revokes_session_and_remember_cookie(account):
    app, uid = account
    old = _login(app)
    remembered = _remember_only(app, old)
    from anetbbs.models import PasswordResetToken, User, db
    with app.app_context():
        db.session.add(PasswordResetToken(
            user_id=uid, token='local-reset-token',
            expires_at=datetime.utcnow() + timedelta(hours=1)))
        db.session.commit()
    response = app.test_client().post('/auth/reset/local-reset-token', data={
        'password': 'reset-password', 'password2': 'reset-password'})
    assert response.status_code == 302
    assert '/auth/login' in response.location
    _assert_revoked(old)
    _assert_revoked(remembered)
    _login(app, password='reset-password')
    with app.app_context():
        assert db.session.get(User, uid).auth_version
        assert PasswordResetToken.query.filter_by(
            token='local-reset-token').one().used_at is not None


def test_wrong_current_password_does_not_revoke_other_sessions(account):
    app, uid = account
    current, old = _login(app), _login(app)
    current.post('/profile/change-password', data={
        'current_password': 'wrong-password',
        'new_password': 'replacement-password',
        'confirm_password': 'replacement-password'})
    assert old.get('/profile/change-password').status_code == 200
    from anetbbs.models import User, db
    with app.app_context():
        assert db.session.get(User, uid).auth_version is None


def test_each_replacement_revokes_even_previous_versioned_remember_cookies(account):
    app, uid = account
    from anetbbs.models import User, db
    with app.app_context():
        user = db.session.get(User, uid)
        user.set_password('second-password')
        first_version = user.auth_version
        db.session.commit()
    old = _login(app, password='second-password')
    remembered = _remember_only(app, old)
    with app.app_context():
        user = db.session.get(User, uid)
        user.set_password('third-password')
        assert user.auth_version != first_version
        db.session.commit()
    _assert_revoked(old)
    _assert_revoked(remembered)
    _login(app, password='third-password')


@pytest.mark.parametrize('identifier', ['bad', '1:wrong-version', ':', '1:ñ', None])
def test_malformed_or_unknown_login_identifier_is_rejected(account, identifier):
    app, _ = account
    from anetbbs.web_app import login_manager
    with app.app_context():
        assert login_manager._user_callback(identifier) is None


def test_existing_database_migrates_without_mass_logout_or_password_change(account):
    app, uid = account
    from anetbbs.models import User, db
    from anetbbs.web_app import _lightweight_migrate, login_manager
    from sqlalchemy import inspect, text
    with app.app_context():
        user = db.session.get(User, uid)
        password_hash = user.password_hash
        assert user.get_id() == str(uid)
        db.session.remove()
        with db.engine.begin() as connection:
            connection.execute(text('ALTER TABLE users DROP COLUMN auth_version'))
        _lightweight_migrate(app)
        _lightweight_migrate(app)
        columns = {c['name']: c for c in inspect(db.engine).get_columns('users')}
        assert columns['auth_version']['nullable']
        user = login_manager._user_callback(str(uid))
        assert user is not None
        assert user.password_hash == password_hash
        assert user.auth_version is None
        user.set_password('new-password')
        db.session.commit()
        assert login_manager._user_callback(str(uid)) is None
        assert login_manager._user_callback(user.get_id()).id == uid
