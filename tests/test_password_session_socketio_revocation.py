"""Password changes revoke active sockets, including passive listeners."""
from flask import Flask
from flask_login import LoginManager, current_user
from flask_socketio import join_room
import pytest

from anetbbs.models import User, db
from anetbbs.session_auth import RevokingSocketIO


@pytest.fixture
def socket_app():
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='socket-revocation-test',
                      SQLALCHEMY_DATABASE_URI='sqlite:///:memory:')
    db.init_app(app)
    login_manager = LoginManager(app)

    @login_manager.user_loader
    def load_user(identifier):
        return db.session.get(User, int(identifier.partition(':')[0]))

    io = RevokingSocketIO(app, async_mode='threading')
    written, disconnected = [], []

    @io.on('connect', namespace='/term')
    def connect_term():
        return current_user.is_authenticated

    @io.on('term_input', namespace='/term')
    def term_input(data):
        # Intentionally mirrors handlers that rely on the connected SID.
        written.append(data['data'])

    @io.on('disconnect', namespace='/term')
    def disconnect_term():
        disconnected.append(True)

    @io.on('connect')
    def connect_default():
        if current_user.is_authenticated:
            join_room(str(current_user.id))

    with app.app_context():
        db.create_all()
        for name in ('alice', 'bob'):
            user = User(username=name, email=name + '@example.com')
            user.set_password('original-password')
            db.session.add(user)
        db.session.commit()
    yield app, io, written, disconnected
    with app.app_context():
        db.session.remove()
        db.drop_all()


def _client(app, io, username, namespace='/term'):
    client = app.test_client()
    if username:
        with app.app_context():
            identifier = User.query.filter_by(username=username).one().get_id()
        with client.session_transaction() as sess:
            sess['_user_id'] = identifier
            sess['_fresh'] = True
    return io.test_client(app, flask_test_client=client, namespace=namespace)


def _change_password(app, username='alice'):
    with app.app_context():
        user = User.query.filter_by(username=username).one()
        user.set_password('replacement-password')
        db.session.commit()


def test_old_socket_cannot_send_after_password_change(socket_app):
    app, io, written, disconnected = socket_app
    old = _client(app, io, 'alice')
    old.emit('term_input', {'data': 'before'}, namespace='/term')
    assert written == ['before']
    _change_password(app)
    old.emit('term_input', {'data': 'after'}, namespace='/term')
    assert not old.is_connected('/term')
    assert written == ['before']
    assert disconnected == [True]


def test_idle_socket_is_revoked_without_sending_another_event(socket_app):
    app, io, _, _ = socket_app
    old = _client(app, io, 'alice', namespace='/')
    other = _client(app, io, 'bob', namespace='/')
    _change_password(app)
    with app.app_context():
        io.revoke_expired_auth_connections()
    assert not old.is_connected('/')
    assert other.is_connected('/')
    other.disconnect()


def test_new_login_works_and_a_second_change_revokes_it(socket_app):
    app, io, written, _ = socket_app
    _change_password(app)
    fresh = _client(app, io, 'alice')
    fresh.emit('term_input', {'data': 'fresh'}, namespace='/term')
    assert written == ['fresh']
    _change_password(app)
    fresh.emit('term_input', {'data': 'stale'}, namespace='/term')
    assert not fresh.is_connected('/term')
    assert written == ['fresh']


def test_anonymous_public_socket_and_testing_timer_remain_unchanged(socket_app):
    app, io, _, _ = socket_app
    guest = _client(app, io, None, namespace='/')
    user = _client(app, io, 'alice')
    with app.app_context():
        io.revoke_expired_auth_connections()
    assert guest.is_connected('/')
    assert user.is_connected('/term')
    assert io._auth_sweep_task is None
    guest.disconnect()
    user.disconnect(namespace='/term')


def test_disconnected_socket_without_disconnect_handler_is_pruned(socket_app):
    app, io, _, _ = socket_app
    user = _client(app, io, 'alice', namespace='/')
    assert len(io._auth_connections) == 1
    user.disconnect()
    with app.app_context():
        io.revoke_expired_auth_connections()
    assert io._auth_connections == {}
