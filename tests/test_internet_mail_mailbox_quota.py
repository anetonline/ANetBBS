"""Per-user inbound mailbox storage quota (MailConfig.mailbox_quota_bytes).

Covers: finite default + migration, exactly what is charged (raw payload +
decoded body, no double counting, UTF-8 bytes, legacy NULL rows), exact
boundary at RCPT and at end of DATA, repeated transactions in one
session, multiple recipients (per-mailbox scope, all-or-nothing), deletion
recovery through the real web delete route, disabled vs full mailboxes,
and concurrent delivery (in-process SMTP sessions and separate OS
processes sharing one SQLite file).
"""
import asyncio

import pytest
from sqlalchemy import inspect

from tests.internet_mail_limits_harness import (
    addr, charge_of, clean_mail, inbound_rows, mail_app, make_user,
    plain_message, run_with_server, run_workers, set_cfg, transaction, usage)

__all__ = ['mail_app', 'clean_mail']   # fixtures re-exported for pytest


def _msg(n, body=None):
    return plain_message(f'quota {n}', body if body is not None else 'q' * 1000)


# -- defaults, schema, accounting ------------------------------------------

def test_default_quota_is_finite_and_migrated(mail_app):
    from anetbbs.models import db, MailConfig, MAILBOX_QUOTA_DEFAULT_BYTES
    assert MAILBOX_QUOTA_DEFAULT_BYTES == 100 * 1024 * 1024
    with mail_app.app_context():
        cols = {c['name']: c for c in inspect(db.engine).get_columns('mail_config')}
        assert 'mailbox_quota_bytes' in cols and not cols['mailbox_quota_bytes']['nullable']
        assert 'stored_bytes' in {c['name'] for c in inspect(db.engine).get_columns('internet_mail')}
        fresh = MailConfig()
        db.session.add(fresh)
        db.session.flush()
        assert fresh.mailbox_quota_bytes == MAILBOX_QUOTA_DEFAULT_BYTES
        db.session.rollback()


def test_quota_falls_back_to_finite_default_when_unset():
    from types import SimpleNamespace
    from anetbbs.mail.limits import mailbox_quota
    from anetbbs.models import MAILBOX_QUOTA_DEFAULT_BYTES
    for bad in (None, 0, -5, 'junk'):
        assert mailbox_quota(SimpleNamespace(mailbox_quota_bytes=bad)) == MAILBOX_QUOTA_DEFAULT_BYTES


def test_charge_is_raw_plus_decoded_body_utf8_without_double_counting(clean_mail):
    app = clean_mail
    uid = make_user(app, 'lim_acct')
    body = 'héllo ☃ ' * 50          # multi-byte UTF-8
    raw = plain_message('acct', body)
    out = run_with_server(lambda c: transaction(c, [addr('lim_acct')], raw))
    assert out['data'].startswith('250'), out
    [(_id, _uid, _s, stored, raw_len, body_len)] = inbound_rows(app, uid)
    assert raw_len == len(raw)
    assert stored == raw_len + body_len == charge_of(app, raw)
    assert body_len > len(body.rstrip('\r\n'))              # bytes, not characters
    assert usage(app, uid) == stored                        # raw_size never added


def test_legacy_rows_without_stored_bytes_are_counted_from_columns(clean_mail):
    from anetbbs.models import db, InternetMail
    app = clean_mail
    uid = make_user(app, 'lim_legacy')
    with app.app_context():
        db.session.add(InternetMail(user_id=uid, direction='inbound', status='read',
                                    raw=b'x' * 700, raw_size=700, body='é' * 100,
                                    stored_bytes=None))
        db.session.add(InternetMail(user_id=uid, direction='inbound', status='received',
                                    raw=None, body=None, stored_bytes=None))
        db.session.commit()
    assert usage(app, uid) == 700 + 200


def test_scope_is_all_rows_owned_by_the_user_including_sent(clean_mail):
    from anetbbs.models import db, InternetMail
    app = clean_mail
    me = make_user(app, 'lim_me')
    other = make_user(app, 'lim_other')
    with app.app_context():
        db.session.add(InternetMail(user_id=me, direction='outbound', status='sent',
                                    body='s' * 5000))          # legacy Sent row, no stored_bytes
        db.session.add(InternetMail(user_id=other, direction='inbound', status='received',
                                    raw=b'o' * 5000, body='', stored_bytes=5000))
        db.session.commit()
    assert usage(app, me) == 5000           # Sent copies count toward the owner's quota
    assert usage(app, other) == 5000        # ...and never toward anyone else's


def test_sent_copies_fill_the_quota_for_inbound_too(clean_mail):
    from anetbbs.models import db, InternetMail
    app = clean_mail
    uid = make_user(app, 'lim_sentfull')
    raw = _msg(1)
    set_cfg(app, mailbox_quota_bytes=charge_of(app, raw))
    with app.app_context():
        db.session.add(InternetMail(user_id=uid, direction='outbound', status='sent',
                                    body='s' * 10, stored_bytes=10))
        db.session.commit()
    out = run_with_server(lambda c: transaction(c, [addr('lim_sentfull')], raw))
    assert out['data'].startswith('452 4.2.2'), out


def test_outbound_send_refused_when_sent_copy_would_exceed_quota(clean_mail):
    from unittest import mock
    from anetbbs.mail.outbound import send_internet_mail
    from anetbbs.models import db, User, InternetMail
    app = clean_mail
    uid = make_user(app, 'lim_outq')
    set_cfg(app, mailbox_quota_bytes=100)
    send = mock.Mock(return_value=(True, ''))
    with mock.patch('anetbbs.mailer.smtp_enabled', return_value=True), \
            mock.patch('anetbbs.mailer.send_email_as', send):
        with app.app_context():
            user = db.session.get(User, uid)
            assert send_internet_mail(user, 'a@example.invalid', 's', 'x' * 100) == (True, '')
            ok, err = send_internet_mail(user, 'a@example.invalid', 's', 'y')
            assert ok is False and 'quota' in err
            rows = InternetMail.query.filter_by(user_id=uid).all()
            assert [(r.status, r.stored_bytes) for r in rows] == [('sent', 100)]
    assert send.call_count == 1             # refused before the relay was contacted
    assert usage(app, uid) == 100


# -- exact boundary ----------------------------------------------------------

def test_exact_boundary_fill_to_quota_then_rcpt_452(clean_mail):
    app = clean_mail
    uid = make_user(app, 'lim_edge')
    raw = _msg(1)
    charge = charge_of(app, raw)
    set_cfg(app, mailbox_quota_bytes=2 * charge)         # exactly two copies fit

    async def scenario(connect):
        first = await transaction(connect, [addr('lim_edge')], raw)
        second = await transaction(connect, [addr('lim_edge')], raw)
        third = await transaction(connect, [addr('lim_edge')], raw)
        return first, second, third

    first, second, third = run_with_server(scenario)
    assert first['data'].startswith('250') and second['data'].startswith('250')
    assert usage(app, uid) == 2 * charge                 # == quota is allowed
    assert third['rcpt'][0].startswith('452 4.2.2'), third
    assert third['data'] is None
    assert len(inbound_rows(app, uid)) == 2


def test_exact_boundary_one_byte_short_refused_after_data(clean_mail):
    app = clean_mail
    uid = make_user(app, 'lim_short')
    raw = _msg(1)
    charge = charge_of(app, raw)
    set_cfg(app, mailbox_quota_bytes=2 * charge - 1)

    async def scenario(connect):
        return (await transaction(connect, [addr('lim_short')], raw),
                await transaction(connect, [addr('lim_short')], raw))

    first, second = run_with_server(scenario)
    assert first['data'].startswith('250')
    assert second['rcpt'][0].startswith('250')           # not full yet at RCPT
    assert second['data'].startswith('452 4.2.2'), second  # but this copy won't fit
    assert usage(app, uid) == charge
    assert len(inbound_rows(app, uid)) == 1


def test_smaller_message_still_fits_after_larger_is_refused(clean_mail):
    app = clean_mail
    uid = make_user(app, 'lim_fit')
    big, small = _msg('big', 'b' * 3000), _msg('small', 's' * 10)
    set_cfg(app, mailbox_quota_bytes=charge_of(app, big) + charge_of(app, small))

    async def scenario(connect):
        return [await transaction(connect, [addr('lim_fit')], m) for m in (big, big, small)]

    r = run_with_server(scenario)
    assert [x['data'][:3] for x in r] == ['250', '452', '250'], r
    assert usage(app, uid) == charge_of(app, big) + charge_of(app, small)


# -- repeated transactions in one session ------------------------------------

def test_repeated_transactions_in_one_session(clean_mail):
    app = clean_mail
    uid = make_user(app, 'lim_repeat')
    raw = _msg(1)
    charge = charge_of(app, raw)
    set_cfg(app, mailbox_quota_bytes=3 * charge + charge // 2)

    async def scenario(connect):
        c = await connect()
        await c.send('EHLO client.test')
        replies = []
        for _ in range(5):
            assert (await c.send('MAIL FROM:<sender@remote.test>')).startswith('250')
            rcpt = await c.send(f'RCPT TO:<{addr("lim_repeat")}>')
            if rcpt.startswith('250'):
                assert (await c.send('DATA')).startswith('354')
                replies.append(('data', await c.send_data(raw)))
            else:
                replies.append(('rcpt', rcpt))
                assert (await c.send('RSET')).startswith('250')
        # the session (and its DB transaction state) is still healthy
        assert (await c.send('NOOP')).startswith('250')
        await c.send('QUIT')
        await c.close()
        return replies

    replies = run_with_server(scenario)
    assert [(k, v[:3]) for k, v in replies] == [
        ('data', '250'), ('data', '250'), ('data', '250'), ('data', '452'), ('data', '452')], replies
    assert usage(app, uid) == 3 * charge


# -- multiple recipients -------------------------------------------------------

def test_multi_recipient_each_mailbox_charged_once_for_its_own_copy(clean_mail):
    app = clean_mail
    a, b = make_user(app, 'lim_ma'), make_user(app, 'lim_mb')
    raw = _msg(1)
    charge = charge_of(app, raw)
    set_cfg(app, mailbox_quota_bytes=charge)             # exactly one copy each
    out = run_with_server(lambda c: transaction(c, [addr('lim_ma'), addr('lim_mb'),
                                                    addr('lim_ma').upper()], raw))
    assert out['data'].startswith('250'), out
    assert usage(app, a) == charge and usage(app, b) == charge


def test_multi_recipient_full_mailbox_refused_at_rcpt_others_delivered(clean_mail):
    app = clean_mail
    a, full = make_user(app, 'lim_room'), make_user(app, 'lim_full')
    raw = _msg(1)
    charge = charge_of(app, raw)
    set_cfg(app, mailbox_quota_bytes=charge)
    assert run_with_server(lambda c: transaction(c, [addr('lim_full')], raw))['data'].startswith('250')
    out = run_with_server(lambda c: transaction(c, [addr('lim_full'), addr('lim_room')], raw))
    assert out['rcpt'][0].startswith('452 4.2.2') and out['rcpt'][1].startswith('250'), out
    assert out['data'].startswith('250')
    assert usage(app, a) == charge and usage(app, full) == charge


def test_multi_recipient_insufficient_room_is_all_or_nothing(clean_mail):
    app = clean_mail
    a, tight = make_user(app, 'lim_ok'), make_user(app, 'lim_tight')
    small, raw = _msg('s', 'x'), _msg(1)
    set_cfg(app, mailbox_quota_bytes=charge_of(app, raw))
    assert run_with_server(lambda c: transaction(c, [addr('lim_tight')], small))['data'].startswith('250')
    out = run_with_server(lambda c: transaction(c, [addr('lim_ok'), addr('lim_tight')], raw))
    assert all(r.startswith('250') for r in out['rcpt']), out
    assert out['data'].startswith('452 4.2.2'), out
    assert inbound_rows(app, a) == []                     # no partial delivery
    # the sender's retry for the recipient that has room succeeds
    assert run_with_server(lambda c: transaction(c, [addr('lim_ok')], raw))['data'].startswith('250')
    assert len(inbound_rows(app, a)) == 1


# -- deletion recovery (real web route) --------------------------------------

def test_deleting_mail_through_web_frees_quota(clean_mail):
    app = clean_mail
    uid = make_user(app, 'lim_del')
    raw = _msg(1)
    set_cfg(app, mailbox_quota_bytes=charge_of(app, raw))
    assert run_with_server(lambda c: transaction(c, [addr('lim_del')], raw))['data'].startswith('250')
    assert run_with_server(lambda c: transaction(c, [addr('lim_del')], raw))['rcpt'][0].startswith('452')

    client = app.test_client()
    with client.session_transaction() as sess:
        sess['_user_id'] = str(uid)
        sess['_fresh'] = True
    [(msg_id, *_rest)] = inbound_rows(app, uid)
    resp = client.post(f'/mail/{msg_id}/delete')
    assert resp.status_code == 302, resp.status_code
    assert usage(app, uid) == 0
    assert run_with_server(lambda c: transaction(c, [addr('lim_del')], raw))['data'].startswith('250')


# -- disabled vs full ----------------------------------------------------------

def test_disabled_mailbox_is_550_not_452_and_keeps_its_rows(clean_mail):
    from anetbbs.models import db, User
    app = clean_mail
    uid = make_user(app, 'lim_off')
    raw = _msg(1)
    set_cfg(app, mailbox_quota_bytes=charge_of(app, raw))
    assert run_with_server(lambda c: transaction(c, [addr('lim_off')], raw))['data'].startswith('250')
    with app.app_context():
        db.session.get(User, uid).email_enabled = False
        db.session.commit()
    out = run_with_server(lambda c: transaction(c, [addr('lim_off')], raw))
    assert out['rcpt'][0].startswith('550'), out          # unknown/disabled, not "full"
    assert len(inbound_rows(app, uid)) == 1


def test_full_mailbox_after_rcpt_disable_still_quota_checked(clean_mail):
    """F-18 keeps delivering to a recipient disabled after RCPT; the quota
    must still bind on that path."""
    from anetbbs.mail import smtp_server, limits
    app = clean_mail
    uid = make_user(app, 'lim_f18')
    raw = _msg(1)
    set_cfg(app, mailbox_quota_bytes=charge_of(app, raw))
    with app.app_context():
        smtp_server._persist('s@remote.test', [addr('lim_f18')], raw,
                             recipient_ids=((uid, addr('lim_f18')),))
        with pytest.raises(limits.MailboxFull) as exc:
            smtp_server._persist('s@remote.test', [addr('lim_f18')], raw,
                                 recipient_ids=((uid, addr('lim_f18')),))
    assert exc.value.reply.startswith('452 4.2.2')
    assert len(inbound_rows(app, uid)) == 1


def test_quota_change_by_sysop_applies_to_next_delivery(clean_mail):
    app = clean_mail
    uid = make_user(app, 'lim_sysop')
    raw = _msg(1)
    set_cfg(app, mailbox_quota_bytes=charge_of(app, raw))
    assert run_with_server(lambda c: transaction(c, [addr('lim_sysop')], raw))['data'].startswith('250')
    assert run_with_server(lambda c: transaction(c, [addr('lim_sysop')], raw))['rcpt'][0].startswith('452')
    set_cfg(app, mailbox_quota_bytes=10 * charge_of(app, raw))
    assert run_with_server(lambda c: transaction(c, [addr('lim_sysop')], raw))['data'].startswith('250')
    assert len(inbound_rows(app, uid)) == 2


# -- concurrency ---------------------------------------------------------------

def test_concurrent_smtp_sessions_never_exceed_quota(clean_mail):
    app = clean_mail
    uid = make_user(app, 'lim_conc')
    raw = _msg(1)
    charge = charge_of(app, raw)
    set_cfg(app, mailbox_quota_bytes=3 * charge)

    async def scenario(connect):
        return await asyncio.gather(*[
            transaction(connect, [addr('lim_conc')], raw, local_ip=f'127.0.1.{i}')
            for i in range(1, 9)])

    results = run_with_server(scenario)
    finals = sorted((r['data'] or r['rcpt'][0])[:3] for r in results)
    assert finals == ['250'] * 3 + ['452'] * 5, results
    assert usage(app, uid) == 3 * charge
    assert len(inbound_rows(app, uid)) == 3


def test_concurrent_processes_never_exceed_quota(clean_mail, tmp_path):
    app = clean_mail
    uid = make_user(app, 'lim_target')
    raw = _msg(1, 'p' * 4000)
    charge = charge_of(app, raw)
    set_cfg(app, mailbox_quota_bytes=7 * charge + charge // 2)
    results = run_workers(app, 'deliver', uid, procs=4, per_proc=5,
                          tmp_path=tmp_path, raw=raw)
    print(f'[quota-concurrency] results={sorted(results)}')
    assert [r for r in results if r.startswith('error')] == []
    assert results.count('ok') == 7 and results.count('full') == 13
    assert usage(app, uid) == 7 * charge <= 7 * charge + charge // 2
