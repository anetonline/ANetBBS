"""Password replacement revokes login snapshots on every terminal transport."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def account(tmp_path, monkeypatch):
    import anetbbs.config as config
    from anetbbs.web_app import create_app
    from anetbbs.models import User, db
    from anetbbs.core import user_manager
    from sqlalchemy.orm import sessionmaker

    uri = f'sqlite:///{tmp_path / "terminal.db"}'
    monkeypatch.setenv('FLASK_ENV', 'testing')
    monkeypatch.setenv('DATABASE_URL', uri)
    monkeypatch.setattr(config.TestingConfig, 'SQLALCHEMY_DATABASE_URI', uri)
    app = create_app('testing')
    with app.app_context():
        user = User(username='terminal-recovery', email='terminal@example.test')
        user.set_password('original-password')
        # An upgraded installation starts with NULL for existing users.
        user.auth_version = None
        db.session.add(user)
        db.session.commit()
        uid = user.id
        monkeypatch.setattr(user_manager, '_Session', sessionmaker(
            bind=db.engine, future=True, expire_on_commit=False))
    yield app, uid, user_manager.UserManager()
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


def _terminal(app, snapshot, *, node=True):
    from anetbbs.core.session import BBSSession
    from anetbbs.models import NodeActivity, db

    session = object.__new__(BBSSession)
    session.user = snapshot
    session._node_activity_id = None
    if node:
        with app.app_context():
            row = NodeActivity(slot=1, user_id=snapshot['id'],
                               username=snapshot['username'], protocol='ssh')
            db.session.add(row)
            db.session.commit()
            session._node_activity_id = row.id
    session.writer = SimpleNamespace(closed=False)
    session.writer.close = lambda: setattr(session.writer, 'closed', True)
    session.write = AsyncMock()
    return session


def _poll(app, session):
    async def run():
        polls = 0

        async def sleep(_seconds):
            nonlocal polls
            polls += 1
            if polls > 1:
                raise asyncio.CancelledError

        with patch('anetbbs.core.session.asyncio.sleep', sleep), \
                patch('anetbbs.features.bbs_ui._app', return_value=app):
            session._start_kick_watchdog()
            await asyncio.wait_for(session._kick_task, timeout=3)

    asyncio.run(run())


def _replace_password(app, uid, password='replacement-password'):
    from anetbbs.models import User, db
    with app.app_context():
        user = db.session.get(User, uid)
        user.set_password(password)
        db.session.commit()
        return user.auth_version


def test_password_authentication_preserves_login_generation(account):
    app, uid, manager = account
    old = manager.authenticate('terminal-recovery', 'original-password')
    assert old['auth_version'] is None
    version = _replace_password(app, uid)
    assert version
    assert manager.authenticate('terminal-recovery', 'original-password') is None
    fresh = manager.authenticate('terminal-recovery', 'replacement-password')
    assert fresh['auth_version'] == version
    assert old['auth_version'] is None


def test_ssh_key_authentication_captures_generation(account):
    app, uid, manager = account
    from anetbbs.models import UserSSHKey, db
    with app.app_context():
        db.session.add(UserSSHKey(user_id=uid, public_key='test-public-key',
                                 fingerprint='test-fingerprint', label='test'))
        db.session.commit()
    old = manager.authenticate_by_public_key('terminal-recovery', 'test-fingerprint')
    version = _replace_password(app, uid)
    fresh = manager.authenticate_by_public_key('terminal-recovery', 'test-fingerprint')
    assert old['auth_version'] is None
    assert fresh['auth_version'] == version
    session = _terminal(app, old)
    _poll(app, session)
    assert session.writer.closed


@pytest.mark.parametrize('node', [True, False])
def test_password_recovery_revokes_old_terminal_even_without_presence_row(account, node):
    app, uid, manager = account
    session = _terminal(app, manager.authenticate(
        'terminal-recovery', 'original-password'), node=node)
    _replace_password(app, uid)
    _poll(app, session)
    assert session.writer.closed
    if node:
        from anetbbs.models import NodeActivity, db
        with app.app_context():
            assert db.session.get(NodeActivity, session._node_activity_id) is None


def test_fresh_terminal_after_password_change_remains_connected(account):
    app, uid, manager = account
    _replace_password(app, uid)
    session = _terminal(app, manager.authenticate(
        'terminal-recovery', 'replacement-password'))
    _poll(app, session)
    assert not session.writer.closed


def test_revocation_closes_transport_even_if_client_stops_reading(account):
    app, uid, manager = account
    session = _terminal(app, manager.authenticate(
        'terminal-recovery', 'original-password'))
    _replace_password(app, uid)

    async def blocked_write(_text):
        await asyncio.Future()

    session.write = blocked_write
    _poll(app, session)
    assert session.writer.closed


def test_legacy_terminal_snapshot_remains_connected_until_password_changes(account):
    app, uid, manager = account
    snapshot = manager.authenticate('terminal-recovery', 'original-password')
    snapshot.pop('auth_version')
    session = _terminal(app, snapshot)
    _poll(app, session)
    assert not session.writer.closed
    _replace_password(app, uid)
    _poll(app, session)
    assert session.writer.closed


def test_terminal_profile_password_change_rotates_generation_and_revokes_session(account):
    app, uid, manager = account
    from anetbbs.features.bbs_ui import BBSMenuUI
    session = _terminal(app, manager.authenticate(
        'terminal-recovery', 'original-password'))
    session.read_password = AsyncMock(side_effect=[
        'original-password', 'replacement-password', 'replacement-password'])
    session.read_line = AsyncMock(return_value='')
    ui = object.__new__(BBSMenuUI)
    ui.session = session
    with patch('anetbbs.features.bbs_ui._app', return_value=app):
        asyncio.run(ui.change_password())
    fresh = manager.authenticate('terminal-recovery', 'replacement-password')
    assert fresh['auth_version']
    _poll(app, session)
    assert session.writer.closed


def test_terminal_sysop_reset_rotates_target_generation(account):
    app, uid, manager = account
    from anetbbs.features.bbs_ui import BBSMenuUI
    snapshot = manager.authenticate('terminal-recovery', 'original-password')
    target_session = _terminal(app, snapshot)
    ui = object.__new__(BBSMenuUI)
    ui.session = SimpleNamespace(
        user={'id': uid + 100}, window_size=(80, 25),
        write=AsyncMock(), read_line=AsyncMock(side_effect=[
            'P', 'replacement-password', 'Q']))
    with patch('anetbbs.features.bbs_ui._app', return_value=app):
        asyncio.run(ui._sysop_edit_user(uid))
    assert manager.authenticate('terminal-recovery', 'replacement-password')['auth_version']
    _poll(app, target_session)
    assert target_session.writer.closed
