"""The web download must be a complete, independent SQLite snapshot.

In particular, a successful integrity_check does not prove that a copy
of the live main file contains transactions that are still in its WAL.
These tests exercise the real authenticated HTTP route with separate
SQLite reader/writer connections and inspect the downloaded database.
"""
from contextlib import closing
import inspect
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace

import pytest
from sqlalchemy import event


@pytest.fixture
def backup_env(tmp_path, monkeypatch):
    import anetbbs.config as cfg_mod
    from anetbbs.models import User, db
    from anetbbs.web_app import create_app

    source = tmp_path / 'live.db'
    data_dir = tmp_path / 'data'
    monkeypatch.setattr(cfg_mod.TestingConfig, 'SQLALCHEMY_DATABASE_URI',
                        f'sqlite:///{source}')
    for name, value in {
        'DATA_DIR': data_dir,
        'UPLOADS_DIR': data_dir / 'uploads',
        'AVATARS_DIR': data_dir / 'avatars',
        'NETWORK_JOIN_DIR': data_dir / 'network_join',
        'GAMES_DATA_DIR': data_dir / 'games',
        'ECHOMAIL_DATA_DIR': data_dir / 'echomail',
        'LOG_FILE': tmp_path / 'bbs.log',
    }.items():
        monkeypatch.setattr(cfg_mod.TestingConfig, name, str(value))

    app = create_app('testing')
    with app.app_context():
        engine = db.engine
        admin_id = User.query.filter_by(is_admin=True).first().id
        ordinary = User(username='backup-reader', email='reader@example.test',
                        password_hash='unused', is_active=True,
                        is_verified=True, is_admin=False)
        db.session.add(ordinary)
        db.session.commit()
        ordinary_id = ordinary.id

    # models.py's connect hook forces a 15-second busy timeout. There
    # is deliberately a reader blocking checkpoints in the regression;
    # avoid waiting 15 seconds to observe SQLite's real busy result.
    def no_busy_wait(connection, _record, _proxy):
        connection.execute('PRAGMA busy_timeout=0')

    event.listen(engine, 'checkout', no_busy_wait)

    def client_as(user_id):
        client = app.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True
        return client

    writer = sqlite3.connect(source, isolation_level=None, timeout=0)
    writer.execute('PRAGMA wal_autocheckpoint=0')
    writer.execute('CREATE TABLE backup_probe '
                   '(id INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
    writer.execute('INSERT INTO backup_probe VALUES (1, ?)', ('baseline',))
    assert writer.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone() == (0, 0, 0)
    try:
        yield SimpleNamespace(app=app, engine=engine, source=source,
                              writer=writer, admin=client_as(admin_id),
                              ordinary=client_as(ordinary_id))
    finally:
        writer.close()
        with app.app_context():
            db.session.remove()
            engine.dispose()
        event.remove(engine, 'checkout', no_busy_wait)


def _restore_download(tmp_path, content):
    """Open the sole downloaded file without allowing WAL sidecars."""
    restore_dir = tmp_path / 'offline-restore'
    restore_dir.mkdir()
    restored_path = restore_dir / 'restored.db'
    restored_path.write_bytes(content)
    uri = restored_path.as_uri() + '?mode=ro&immutable=1'
    with closing(sqlite3.connect(uri, uri=True)) as restored:
        assert restored.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
        rows = restored.execute(
            'SELECT id, payload FROM backup_probe ORDER BY id').fetchall()
    assert list(restore_dir.iterdir()) == [restored_path]
    return rows


def test_http_backup_includes_committed_wal_rows_when_checkpoint_is_busy(
        backup_env, tmp_path):
    env = backup_env
    with closing(sqlite3.connect(env.source, isolation_level=None,
                                timeout=0)) as reader:
        reader.execute('BEGIN')
        assert reader.execute('SELECT * FROM backup_probe').fetchall() == [
            (1, 'baseline')]
        env.writer.execute('INSERT INTO backup_probe VALUES (2, ?)',
                           ('committed-after-reader',))
        checkpoint = env.writer.execute(
            'PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
        assert checkpoint[0] == 1 and checkpoint[1] > checkpoint[2], checkpoint

        response = env.admin.get('/admin/db-backup')
        try:
            assert response.status_code == 200
            assert response.mimetype == 'application/octet-stream'
            assert 'attachment;' in response.headers['Content-Disposition']
            assert 'anetbbs-backup-' in response.headers['Content-Disposition']
            content = response.get_data()
        finally:
            response.close()

    # The restore consumes only the downloaded bytes, after closing
    # every live connection and removing the disposable source file.
    env.writer.close()
    env.engine.dispose()
    env.source.unlink()
    rows = _restore_download(tmp_path, content)
    assert rows == [(1, 'baseline'), (2, 'committed-after-reader')], (
        f'checkpoint={checkpoint}; downloaded probe rows={rows}')


def test_http_backup_does_not_change_while_live_database_is_updated(
        backup_env, tmp_path):
    env = backup_env
    original = 'a' * 8192
    updated = 'b' * len(original)
    env.writer.execute('UPDATE backup_probe SET payload=?', (original,))
    env.writer.executemany('INSERT INTO backup_probe VALUES (?, ?)',
                           [(number, original) for number in range(2, 65)])
    assert env.writer.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone() == (0, 0, 0)

    response = env.admin.get('/admin/db-backup', buffered=False)
    try:
        assert response.status_code == 200
        chunks = iter(response.response)
        first = next(chunks)
        assert len(first) < env.source.stat().st_size

        # The HTTP response has started, but most bytes have not been
        # consumed. A checkpoint now changes pages that a live-file
        # response would still read; a snapshot must remain unchanged.
        env.writer.execute('UPDATE backup_probe SET payload=?', (updated,))
        assert env.writer.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone() == (0, 0, 0)
        assert env.writer.execute('SELECT DISTINCT payload FROM backup_probe').fetchall() == [
            (updated,)]
        content = first + b''.join(chunks)
    finally:
        response.close()

    assert _restore_download(tmp_path, content) == [
        (number, original) for number in range(1, 65)]


@pytest.mark.parametrize('user_kind, expected_status', [
    ('anonymous', 302), ('ordinary', 403),
])
def test_http_backup_requires_admin(backup_env, user_kind, expected_status):
    client = (backup_env.app.test_client() if user_kind == 'anonymous'
              else backup_env.ordinary)
    response = client.get('/admin/db-backup')
    try:
        assert response.status_code == expected_status
        assert 'Content-Disposition' not in response.headers
        if user_kind == 'anonymous':
            assert '/auth/login' in response.headers['Location']
    finally:
        response.close()


def _track_snapshots(monkeypatch):
    snapshots = []
    real_named_temporary_file = tempfile.NamedTemporaryFile

    def track(*args, **kwargs):
        snapshot = real_named_temporary_file(*args, **kwargs)
        snapshots.append(snapshot)
        return snapshot

    monkeypatch.setattr(tempfile, 'NamedTemporaryFile', track)
    return snapshots


@pytest.mark.parametrize('abort_download', [False, True])
def test_http_backup_releases_snapshot_on_response_close(
        backup_env, monkeypatch, abort_download):
    snapshots = _track_snapshots(monkeypatch)
    response = backup_env.admin.get('/admin/db-backup', buffered=False)
    try:
        assert response.status_code == 200
        assert len(snapshots) == 1
        assert not snapshots[0].closed
        assert Path(snapshots[0].name).exists()
        if not abort_download:
            assert response.get_data().startswith(b'SQLite format 3\0')
    finally:
        response.close()

    assert snapshots[0].closed
    assert not Path(snapshots[0].name).exists()


def test_http_backup_destination_failure_does_not_download_live_database(
        backup_env, monkeypatch):
    snapshots = _track_snapshots(monkeypatch)

    def destination_unavailable(*_args, **_kwargs):
        raise sqlite3.OperationalError('test destination unavailable')

    monkeypatch.setattr(sqlite3, 'connect', destination_unavailable)
    response = backup_env.admin.get('/admin/db-backup')
    try:
        assert response.status_code == 302
        assert response.headers['Location'].rstrip('/') == '/admin'
        assert 'Content-Disposition' not in response.headers
        assert not response.get_data().startswith(b'SQLite format 3\0')
    finally:
        response.close()

    assert len(snapshots) == 1
    assert snapshots[0].closed
    assert not Path(snapshots[0].name).exists()
    assert backup_env.writer.execute('SELECT * FROM backup_probe').fetchall() == [
        (1, 'baseline')]


def test_http_backup_rejects_snapshot_that_fails_integrity_check(
        backup_env, monkeypatch):
    snapshots = _track_snapshots(monkeypatch)
    real_connect = sqlite3.connect

    class FailedIntegrityConnection(sqlite3.Connection):
        def execute(self, statement, *args, **kwargs):
            if statement.strip().lower() == 'pragma integrity_check':
                return SimpleNamespace(fetchall=lambda: [('test integrity failure',)])
            return super().execute(statement, *args, **kwargs)

    def connect_destination(*args, **kwargs):
        kwargs['factory'] = FailedIntegrityConnection
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, 'connect', connect_destination)
    response = backup_env.admin.get('/admin/db-backup')
    try:
        assert response.status_code == 302
        assert 'Content-Disposition' not in response.headers
    finally:
        response.close()

    assert len(snapshots) == 1
    assert snapshots[0].closed
    assert not Path(snapshots[0].name).exists()


def test_backup_rejects_active_source_write_without_rolling_it_back(backup_env):
    from anetbbs.models import db
    from sqlalchemy import text

    # A request normally commits its presence update before the view.
    # Exercise the safety guard directly with a real pending write so
    # a shared connection cannot make SQLite backup retry forever or
    # lose the caller's transaction when a second proxy is closed.
    with backup_env.app.test_request_context('/admin/db-backup'):
        db.session.execute(text(
            "INSERT INTO backup_probe VALUES (2, 'uncommitted')"))
        source = db.session.connection().connection.driver_connection
        assert source.in_transaction
        try:
            view = inspect.unwrap(backup_env.app.view_functions['admin.db_backup'])
            response = view()
            try:
                assert response.status_code == 302
                assert 'Content-Disposition' not in response.headers
            finally:
                response.close()
            assert source.in_transaction
            assert db.session.execute(text(
                'SELECT payload FROM backup_probe WHERE id=2')).scalar_one() == 'uncommitted'
            assert backup_env.writer.execute(
                'SELECT payload FROM backup_probe WHERE id=2').fetchall() == []
        finally:
            db.session.rollback()


def test_backup_aborts_when_another_connection_holds_exclusive_lock(
        backup_env, monkeypatch, tmp_path, caplog):
    from eventlet.patcher import original
    from anetbbs.models import db
    from anetbbs.web import admin

    database = tmp_path / 'rollback-journal.db'
    # A native thread can release the lock even while SQLite blocks the event loop.
    threading = original('threading')
    lock_acquired = threading.Event()
    release_lock = threading.Event()
    real_snapshot = tempfile.NamedTemporaryFile
    snapshots = []

    def hold_lock():
        with closing(sqlite3.connect(database, timeout=0)) as writer:
            writer.execute('BEGIN EXCLUSIVE')
            lock_acquired.set()
            # Release even if the backup regresses and waits indefinitely.
            release_lock.wait(timeout=3)

    lock_thread = threading.Thread(target=hold_lock)

    def create_snapshot(*args, **kwargs):
        snapshot = real_snapshot(*args, **kwargs)
        snapshots.append(snapshot)
        lock_thread.start()
        assert lock_acquired.wait(timeout=2)
        return snapshot

    with closing(sqlite3.connect(database, timeout=0)) as source, \
            backup_env.app.test_request_context('/admin/db-backup'):
        source.execute('CREATE TABLE sample (id INTEGER)')
        source.commit()
        source.execute('PRAGMA busy_timeout=15000')
        connection = SimpleNamespace(connection=SimpleNamespace(driver_connection=source))
        monkeypatch.setattr(db.session, 'connection', lambda: connection)
        monkeypatch.setattr(tempfile, 'NamedTemporaryFile', create_snapshot)
        # Advance the backup's clock without extending the test's real runtime.
        clock = iter((0, 6))
        monkeypatch.setattr(admin, 'monotonic', lambda: next(clock))

        try:
            view = inspect.unwrap(backup_env.app.view_functions['admin.db_backup'])
            response = view()
            assert source.execute('PRAGMA busy_timeout').fetchone() == (15000,)
            try:
                assert lock_acquired.is_set()
                assert response.status_code == 302
                assert 'Content-Disposition' not in response.headers
            finally:
                response.close()
        finally:
            release_lock.set()
            if lock_thread.ident is not None:
                lock_thread.join()

    assert 'SQLite backup exceeded its retry deadline' in caplog.text
    assert len(snapshots) == 1
    assert snapshots[0].closed
    assert not Path(snapshots[0].name).exists()
