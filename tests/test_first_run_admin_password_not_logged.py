"""First-run fallback admin: the generated password must reach the sysop
only through data/admin_password.txt (owner-only), never a log record
(logs/anetbbs.log is 664, and journald/docker logs are widely readable).
"""
import logging
import os
import stat

import pytest


@pytest.fixture
def fresh_app(tmp_path, monkeypatch):
    import anetbbs.config as cfg_mod
    data_dir = tmp_path / 'data'
    monkeypatch.setattr(cfg_mod.TestingConfig, 'SQLALCHEMY_DATABASE_URI',
                        f'sqlite:///{tmp_path / "firstrun.db"}')
    monkeypatch.setattr(cfg_mod.TestingConfig, 'DATA_DIR', str(data_dir))
    monkeypatch.setenv('FLASK_ENV', 'testing')
    return data_dir


def _all_log_text(caplog):
    return '\n'.join(r.getMessage() for r in caplog.records) + caplog.text


def test_generated_password_is_in_owner_only_file_and_never_logged(fresh_app, caplog, capfd):
    from anetbbs.models import User
    from anetbbs.web_app import create_app
    with caplog.at_level(logging.DEBUG):
        app = create_app('testing')
    pw_file = fresh_app / 'admin_password.txt'
    password = pw_file.read_text().strip()
    assert len(password) >= 16
    assert stat.S_IMODE(os.stat(pw_file).st_mode) == 0o600
    with app.app_context():
        admin = User.query.filter_by(username='admin', is_admin=True).one()
        assert admin.check_password(password)           # sysop can still log in
    text = _all_log_text(caplog)
    assert 'INITIAL ADMIN ACCOUNT CREATED' in text      # banner kept
    assert str(pw_file) in text                         # tells the sysop where
    assert password not in text                         # ...but not the secret
    out, err = capfd.readouterr()                       # any handler writing to a tty/journal
    assert password not in out and password not in err


def test_existing_world_readable_file_is_tightened(fresh_app):
    from anetbbs.web_app import create_app
    fresh_app.mkdir(parents=True)
    pw_file = fresh_app / 'admin_password.txt'
    pw_file.write_text('stale\n')
    os.chmod(pw_file, 0o644)
    create_app('testing')
    assert stat.S_IMODE(os.stat(pw_file).st_mode) == 0o600
    assert pw_file.read_text().strip() != 'stale'


def test_unwritable_password_file_creates_no_admin_and_logs_no_secret(fresh_app, caplog,
                                                                     monkeypatch):
    import secrets as _secrets
    from anetbbs.models import db, User
    from anetbbs.web_app import create_app, _create_default_data
    app = create_app('testing')
    blocker = fresh_app.parent / 'not-a-dir'
    blocker.write_text('x')
    sentinel = 'SENTINEL-first-run-password-value'
    monkeypatch.setattr(_secrets, 'token_urlsafe', lambda n=None: sentinel)
    records = []
    handler = logging.Handler(level=logging.DEBUG)
    handler.emit = records.append
    app.logger.addHandler(handler)     # create_app() may stop propagation
    try:
        with app.app_context():
            User.query.filter_by(is_admin=True).delete()
            db.session.commit()
            app.config['DATA_DIR'] = str(blocker / 'data')
            with caplog.at_level(logging.DEBUG):
                _create_default_data()
            assert User.query.filter_by(is_admin=True).count() == 0
    finally:
        app.logger.removeHandler(handler)
    text = _all_log_text(caplog) + '\n'.join(r.getMessage() for r in records)
    assert sentinel not in text
    assert 'no fallback admin account was created' in text
