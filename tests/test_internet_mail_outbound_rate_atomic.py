"""Atomic per-user outbound rate limits (MailConfig.outbound_per_hour /
outbound_per_day, defaults 10 and 50).

Concurrent sends run in separate OS processes against one SQLite file
with the relay replaced by an in-process fake (no network). A
check-then-send-then-insert limiter lets every concurrent caller pass the
same "N used" check; the locked reservation must admit exactly the limit.
"""
from unittest import mock

import pytest

from tests.internet_mail_limits_harness import (
    clean_mail, mail_app, make_user, run_workers, set_cfg)

__all__ = ['mail_app', 'clean_mail']


def _outbound(app, uid):
    from anetbbs.models import InternetMail
    with app.app_context():
        return [m.status for m in InternetMail.query.filter_by(
            user_id=uid, direction='outbound').order_by(InternetMail.id)]


@pytest.mark.parametrize('per_hour,per_day,expected', [(10, 50, 10), (100, 6, 6)])
def test_concurrent_sends_admit_exactly_the_limit(clean_mail, tmp_path, per_hour, per_day,
                                                  expected):
    app = clean_mail
    uid = make_user(app, 'lim_target')
    set_cfg(app, outbound_per_hour=per_hour, outbound_per_day=per_day)
    results = run_workers(app, 'send', uid, procs=4, per_proc=5, tmp_path=tmp_path)
    print(f'[rate-concurrency] hour={per_hour} day={per_day} results={sorted(results)}')
    assert [r for r in results if not r in ('ok', 'limit')] == []
    assert results.count('ok') == expected
    assert results.count('limit') == 20 - expected
    assert _outbound(app, uid) == ['sent'] * expected


def test_reservation_is_finalised_and_refusal_writes_no_row(clean_mail):
    from anetbbs.mail.outbound import send_internet_mail
    from anetbbs.models import db, User
    app = clean_mail
    uid = make_user(app, 'lim_seq')
    set_cfg(app, outbound_per_hour=2)
    seen = []

    def fake_send(*_a, **_k):
        seen.append(_outbound(app, uid))       # row already reserved mid-send
        return (len(seen) != 2, '' if len(seen) != 2 else 'relay said no')

    with mock.patch('anetbbs.mailer.smtp_enabled', return_value=True), \
            mock.patch('anetbbs.mailer.send_email_as', side_effect=fake_send):
        with app.app_context():
            user = db.session.get(User, uid)
            r1 = send_internet_mail(user, 'a@example.invalid', 's', 'b')
            r2 = send_internet_mail(user, 'a@example.invalid', 's', 'b')
            r3 = send_internet_mail(user, 'a@example.invalid', 's', 'b')
    assert seen == [['sending'], ['sent', 'sending']]
    assert r1 == (True, '') and r2 == (False, 'relay said no')
    assert r3[0] is False and 'limit reached (2 per hour)' in r3[1]
    assert _outbound(app, uid) == ['sent', 'failed']     # failed attempts still count


def test_relay_exception_marks_reservation_failed(clean_mail):
    from anetbbs.mail.outbound import send_internet_mail
    from anetbbs.models import db, User
    app = clean_mail
    uid = make_user(app, 'lim_boom')
    with mock.patch('anetbbs.mailer.smtp_enabled', return_value=True), \
            mock.patch('anetbbs.mailer.send_email_as', side_effect=RuntimeError('boom')):
        with app.app_context():
            with pytest.raises(RuntimeError):
                send_internet_mail(db.session.get(User, uid), 'a@example.invalid', 's', 'b')
    assert _outbound(app, uid) == ['failed']


@pytest.mark.parametrize('state', ['optout', 'inactive', 'locked', 'feature_off', 'zero'])
def test_access_rules_still_refuse_before_any_reservation(clean_mail, state):
    from anetbbs.mail.outbound import send_internet_mail
    from anetbbs.models import db, User
    app = clean_mail
    uid = make_user(app, 'lim_rules')
    with app.app_context():
        u = db.session.get(User, uid)
        if state == 'optout':
            u.email_enabled = False
        elif state == 'inactive':
            u.is_active = False
        elif state == 'locked':
            u.is_locked = True
        db.session.commit()
    if state == 'feature_off':
        set_cfg(app, enabled=False)
    if state == 'zero':
        set_cfg(app, outbound_per_hour=0)
    send = mock.Mock(return_value=(True, ''))
    with mock.patch('anetbbs.mailer.smtp_enabled', return_value=True), \
            mock.patch('anetbbs.mailer.send_email_as', send):
        with app.app_context():
            ok, err = send_internet_mail(db.session.get(User, uid), 'a@example.invalid', 's', 'b')
    assert ok is False and err
    send.assert_not_called()
    assert _outbound(app, uid) == []
