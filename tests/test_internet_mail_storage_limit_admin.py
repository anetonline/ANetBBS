"""Admin -> Internet Mail: sysop configuration of the mailbox quota,
attachment limits and the per-user outbound rate limits (10/hour and
50/day defaults preserved)."""
import pytest

from tests.internet_mail_limits_harness import clean_mail, mail_app

__all__ = ['mail_app', 'clean_mail']


@pytest.fixture
def admin_client(clean_mail):
    from anetbbs.models import db, User
    app = clean_mail
    with app.app_context():
        admin = User.query.filter_by(username='lim_admin').first()
        if admin is None:
            admin = User(username='lim_admin', email='lim_admin@example.invalid',
                         password_hash='x', is_admin=True, access_level=100)
            db.session.add(admin)
            db.session.commit()
        uid = admin.id
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['_user_id'] = str(uid)
        sess['_fresh'] = True
    return app, client


BASE = {'enabled': 'y', 'domain': 'bbs.limits.test', 'min_access_level': '0',
        'max_message_size_mb': '25'}


def _cfg(app):
    from anetbbs.models import MailConfig
    with app.app_context():
        c = MailConfig.get()
        return {k: getattr(c, k) for k in (
            'mailbox_quota_bytes', 'max_attachments', 'max_attachment_bytes',
            'outbound_per_hour', 'outbound_per_day', 'max_recipients')}


def test_rate_limit_defaults_are_10_per_hour_and_50_per_day(mail_app):
    from anetbbs.models import db, MailConfig
    with mail_app.app_context():
        fresh = MailConfig()
        db.session.add(fresh)
        db.session.flush()
        assert (fresh.outbound_per_hour, fresh.outbound_per_day) == (10, 50)
        db.session.rollback()


def test_settings_page_shows_new_fields(admin_client):
    app, client = admin_client
    page = client.get('/admin/mail').get_data(as_text=True)
    for name in ('mailbox_quota_bytes', 'max_attachments', 'max_attachment_bytes',
                 'outbound_per_hour', 'outbound_per_day'):
        assert f'name="{name}"' in page
    assert '104857600' in page and '10485760' in page


def test_sysop_can_set_every_limit(admin_client):
    app, client = admin_client
    resp = client.post('/admin/mail', data=dict(
        BASE, mailbox_quota_bytes='52428800', max_attachments='7',
        max_attachment_bytes='2097152', outbound_per_hour='3', outbound_per_day='12',
        max_recipients='4'))
    assert resp.status_code == 302
    assert _cfg(app) == {'mailbox_quota_bytes': 52428800, 'max_attachments': 7,
                         'max_attachment_bytes': 2097152, 'outbound_per_hour': 3,
                         'outbound_per_day': 12, 'max_recipients': 4}


def test_limits_stay_finite_and_clamped(admin_client):
    from anetbbs.models import MAILBOX_QUOTA_MAX_BYTES, MAX_ATTACHMENTS_CEILING
    app, client = admin_client
    client.post('/admin/mail', data=dict(
        BASE, mailbox_quota_bytes='0', max_attachments='-3', max_attachment_bytes='-1'))
    c = _cfg(app)
    assert c['mailbox_quota_bytes'] == 1 and c['max_attachments'] == 0
    assert c['max_attachment_bytes'] == 1
    client.post('/admin/mail', data=dict(
        BASE, mailbox_quota_bytes=str(10 ** 18), max_attachments='999999',
        max_attachment_bytes=str(10 ** 18)))
    c = _cfg(app)
    assert c['mailbox_quota_bytes'] == MAILBOX_QUOTA_MAX_BYTES
    assert c['max_attachments'] == MAX_ATTACHMENTS_CEILING
    assert c['max_attachment_bytes'] == MAILBOX_QUOTA_MAX_BYTES


def test_invalid_or_missing_values_keep_current_setting(admin_client):
    app, client = admin_client
    client.post('/admin/mail', data=dict(BASE, mailbox_quota_bytes='12345',
                                         max_attachments='4', max_attachment_bytes='999'))
    resp = client.post('/admin/mail', data=dict(BASE, mailbox_quota_bytes='lots',
                                                max_attachments=''), follow_redirects=True)
    assert 'Ignored invalid value' in resp.get_data(as_text=True)
    c = _cfg(app)
    assert (c['mailbox_quota_bytes'], c['max_attachments'], c['max_attachment_bytes']) == (
        12345, 4, 999)


def test_quota_below_message_size_warns(admin_client):
    app, client = admin_client
    resp = client.post('/admin/mail', data=dict(BASE, mailbox_quota_bytes='1000'),
                       follow_redirects=True)
    assert 'smaller than the maximum message size' in resp.get_data(as_text=True)
