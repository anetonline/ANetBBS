"""Subprocess worker for the Internet Mail concurrency tests (see
internet_mail_limits_harness.run_workers). Not a test module.

argv: db_path mode user_id count start_at payload_path rcpt_addr out_json

mode=deliver: calls smtp_server._persist() `count` times for user_id,
    exactly as the SMTP session does after DATA.
mode=send:    calls outbound.send_internet_mail() `count` times as user_id
    with the relay replaced by an in-process fake that sleeps briefly
    (widening the window a check-then-insert limiter would lose) and
    reports success. Nothing touches the network.

Each process opens its own SQLAlchemy engine on the shared SQLite file,
so contention is real OS-level concurrency (not greenlets).
"""
import json
import os
import sys
import time


def main():
    (db_path, mode, user_id, count, start_at, payload_path,
     rcpt_addr, out_path) = sys.argv[1:9]
    user_id, count, start_at = int(user_id), int(count), float(start_at)
    os.environ['FLASK_ENV'] = 'testing'

    import anetbbs.config as cfg_mod
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    from flask import Flask
    from anetbbs.models import db, User
    app = Flask('limits-worker')
    app.config.from_object(cfg_mod.TestingConfig)
    db.init_app(app)

    raw = open(payload_path, 'rb').read()
    results = []

    # Widen the check-to-insert window deterministically (50 ms after the
    # limit check passes). With correct locking the other processes wait
    # behind it; a check-then-insert implementation without the lock lets
    # every process pass the check during this sleep and overshoot.
    from anetbbs.mail import limits as _limits, outbound as _outbound
    _orig_quota = _limits.check_quota_locked

    def _slow_quota(*a, **k):
        result = _orig_quota(*a, **k)
        time.sleep(0.05)
        return result
    _limits.check_quota_locked = _slow_quota

    _orig_rate = _outbound._outbound_limit_error

    def _slow_rate(*a, **k):
        result = _orig_rate(*a, **k)
        time.sleep(0.05)
        return result
    _outbound._outbound_limit_error = _slow_rate

    if mode == 'send':
        from anetbbs import mailer

        def _fake_send(*_a, **_k):
            time.sleep(0.05)
            return True, ''
        mailer.send_email_as = _fake_send
        mailer.smtp_enabled = lambda: True

    while time.time() < start_at:
        time.sleep(0.001)

    with app.app_context():
        for _ in range(count):
            if mode == 'deliver':
                from anetbbs.mail import smtp_server, limits
                try:
                    smtp_server._persist('sender@remote.test', [rcpt_addr], raw,
                                         recipient_ids=((user_id, rcpt_addr),))
                    results.append('ok')
                except limits.MailboxFull:
                    results.append('full')
                except Exception as exc:          # reported, test asserts none
                    db.session.rollback()
                    results.append(f'error:{type(exc).__name__}:{exc}')
            else:
                from anetbbs.mail.outbound import send_internet_mail
                user = db.session.get(User, user_id)
                try:
                    ok, err = send_internet_mail(user, 'friend@example.invalid',
                                                 'concurrency', 'body')
                    results.append('ok' if ok else
                                   ('limit' if 'limit reached' in err else f'refused:{err}'))
                except Exception as exc:
                    db.session.rollback()
                    results.append(f'error:{type(exc).__name__}:{exc}')

    with open(out_path, 'w') as f:
        json.dump(results, f)


if __name__ == '__main__':
    main()
