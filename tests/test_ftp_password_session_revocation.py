"""Human FTP sessions revoke on commands and the existing I/O loop timer."""
import ftplib
import threading
from types import SimpleNamespace

from flask import Flask
from pyftpdlib.ioloop import IOLoop
from pyftpdlib.servers import FTPServer
import pytest

from anetbbs.ftp.server import AnetbbsAuthorizer, AnetbbsFTPHandler
from anetbbs.models import User, db


@pytest.fixture
def ftp_server(tmp_path):
    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=(
        f'sqlite:///{tmp_path / "ftp.db"}'))
    db.init_app(app)
    root = tmp_path / 'files'
    root.mkdir()
    (root / 'public.txt').write_bytes(b'local FTP fixture\n')
    with app.app_context():
        db.create_all()
        user = User(username='ftp-recovery', email='ftp@example.test')
        user.set_password('original-password')
        db.session.add(user)
        db.session.commit()
        uid = user.id

    handlers = []

    class Handler(AnetbbsFTPHandler):
        def on_connect(self):
            handlers.append(self)

    Handler.app = app
    Handler.authorizer = AnetbbsAuthorizer(
        app, str(root), str(root), str(root), anon_enabled=True,
        qwk_root=str(tmp_path / 'qwk'))
    loop = IOLoop()
    server = FTPServer(('127.0.0.1', 0), Handler, ioloop=loop)
    server.max_cons = 256
    server.max_cons_per_ip = 8
    # A real OS thread, regardless of whether some earlier-imported test
    # module already called eventlet.monkey_patch() in this same process
    # (anetbbs.web_app does this unconditionally at import time). Once
    # that's happened, a plain threading.Thread here silently becomes a
    # greenlet, and pyftpdlib's own blocking select/epoll poll loop
    # (ioloop.py) is not eventlet-aware -- it stalls the whole cooperative
    # scheduler instead of running concurrently, hanging this fixture's
    # control-socket round trips for the rest of the test (confirmed live:
    # ~56 minutes before being killed, when this file ran in the same CI
    # batch as test_ftp_case_mismatch_crash.py, which imports
    # anetbbs.web_app). Same fix as the SSH door-cleanup regression test.
    from eventlet.patcher import original
    threading = original('threading')
    thread = threading.Thread(target=server.serve_forever, kwargs={
        'timeout': 0.02, 'blocking': True, 'handle_exit': False}, daemon=True)
    thread.start()
    state = SimpleNamespace(app=app, uid=uid, server=server, handlers=handlers,
                            authorizer=Handler.authorizer,
                            loop=loop, port=server.socket.getsockname()[1])
    yield state
    server.close_all()
    thread.join(timeout=3)
    assert not thread.is_alive()
    loop.close()
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


def _login(state, username='ftp-recovery', password='original-password'):
    client = ftplib.FTP()
    client.connect('127.0.0.1', state.port, timeout=8)
    client.login(username, password)
    return client


def _replace_password(state):
    with state.app.app_context():
        user = db.session.get(User, state.uid)
        user.set_password('replacement-password')
        db.session.commit()


def _on_loop(state, callback):
    # Real Event, for the same reason the fixture above needs a real
    # Thread: `run()` below fires on the fixture's (now genuinely OS-
    # level) server thread, while this function's own caller blocks on
    # `finished.wait()` from the main test thread. A monkey-patched
    # (eventlet-green) Event's wait() does not reliably wake up for a
    # set() coming from a real OS thread -- confirmed live: wait(3)
    # returned False even though the Event's own repr already showed
    # "set". Mixing a real Thread with a patched Event here reintroduces
    # a different hang/flake than the one the fixture's fix above closes.
    from eventlet.patcher import original
    finished = original('threading').Event()
    errors = []

    def run():
        try:
            callback()
        except Exception as error:
            errors.append(error)
        finally:
            finished.set()

    state.loop.call_later(0, run)
    assert finished.wait(3)
    assert not errors


@pytest.mark.parametrize('command', ['NOOP', 'RETR public.txt'])
def test_old_ftp_login_cannot_issue_commands_after_password_change(ftp_server, command):
    client = _login(ftp_server)
    try:
        assert client.voidcmd('NOOP').startswith('200')
        if command.startswith('RETR'):
            # Leave a passive listener pending before revocation. Without
            # the guard, RETR would be accepted rather than just fail 425.
            client.makepasv()
        _replace_password(ftp_server)
        with pytest.raises((EOFError, OSError, ftplib.error_temp)):
            client.sendcmd(command)
        assert ftp_server.handlers[-1]._closed
    finally:
        client.close()


def test_fresh_login_and_anonymous_download_remain_usable(ftp_server):
    _replace_password(ftp_server)
    for username, password in [('ftp-recovery', 'replacement-password'),
                               ('anonymous', 'guest@example.test')]:
        client = _login(ftp_server, username, password)
        try:
            result = []
            client.retrbinary('RETR public.txt', result.append)
            assert b''.join(result) == b'local FTP fixture\n'
            if username == 'anonymous':
                assert ftp_server.handlers[-1]._auth_recheck is None
        finally:
            client.close()


@pytest.mark.parametrize('passive', [True, False])
def test_periodic_check_closes_idle_control_and_active_data_channels(ftp_server, passive):
    client = _login(ftp_server)
    client.set_pasv(passive)
    data = client.transfercmd('STOR waiting.bin')
    data.settimeout(8)
    try:
        handler = ftp_server.handlers[-1]
        assert handler._auth_recheck is not None
        assert handler.data_channel is not None
        _replace_password(ftp_server)
        # No command or direct callback invocation: the real 5s timer fires.
        assert data.recv(1) == b''
        assert client.sock.recv(1) == b''
    finally:
        data.close()
        client.close()


def test_rein_and_anonymous_relogin_cannot_unbind_old_active_transfer(ftp_server):
    client = _login(ftp_server)
    data = client.transfercmd('STOR waiting.bin')
    data.settimeout(3)
    try:
        data.sendall(b'still transferring')
        handler = ftp_server.handlers[-1]
        # Confirm the real data connection has processed a byte before REIN.
        _on_loop(ftp_server, lambda: None)
        assert handler.data_channel.transfer_in_progress()
        assert client.sendcmd('REIN').startswith('230')
        client.login('anonymous', 'guest@example.test')
        handler = ftp_server.handlers[-1]
        assert handler._bbs_auth is None
        assert handler._bbs_transfer_auth is not None
        _replace_password(ftp_server)
        _on_loop(ftp_server, handler._recheck_auth)
        assert data.recv(1) == b''
        assert client.sock.recv(1) == b''
    finally:
        data.close()
        client.close()


def test_human_session_closes_on_authentication_database_failure(ftp_server, monkeypatch):
    client = _login(ftp_server)
    try:
        def failed_query(*args, **kwargs):
            raise RuntimeError('database unavailable in test')

        monkeypatch.setattr(db.session, 'get', failed_query)
        with pytest.raises((EOFError, OSError, ftplib.error_temp)):
            client.voidcmd('NOOP')
    finally:
        client.close()


def test_qwk_node_authentication_remains_unbound_to_human_generation(ftp_server):
    from anetbbs.models import QWKNode
    with ftp_server.app.app_context():
        db.session.add(QWKNode(packet_id='TESTQWK', name='Local QWK',
                               password='node-secret', is_active=True))
        db.session.commit()
    handler = SimpleNamespace(remote_ip='127.0.0.1')
    ftp_server.authorizer.validate_authentication('testqwk', 'node-secret', handler)
    assert handler._bbs_auth is None
    assert handler._qwk_node_id is not None
