"""Outbound Internet Mail subjects stay out of the sysop activity log and
the service log on every compose surface (audit F-20); the user-facing
result (Sent view, flash, redirect) is unchanged."""
import logging
import re
from pathlib import Path
from unittest import mock

from tests.internet_mail_limits_harness import clean_mail, mail_app, make_user

__all__ = ['mail_app', 'clean_mail']

SUBJECT = 'Confidential biopsy results'
SRC = Path(__file__).resolve().parents[1]


def test_web_compose_logs_recipient_but_not_subject(clean_mail, caplog):
    from anetbbs.models import UserActivity, InternetMail
    app = clean_mail
    uid = make_user(app, 'lim_websend')
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['_user_id'] = str(uid)
        sess['_fresh'] = True
    with mock.patch('anetbbs.mailer.smtp_enabled', return_value=True), \
            mock.patch('anetbbs.mailer.send_email_as', return_value=(True, '')), \
            caplog.at_level(logging.DEBUG):
        resp = client.post('/mail/compose', data={
            'to_address': 'friend@example.invalid', 'subject': SUBJECT,
            'content': 'body text'})
    assert resp.status_code == 302 and '/mail/sent' in resp.headers['Location']
    with app.app_context():
        acts = UserActivity.query.filter_by(user_id=uid,
                                            activity_type='internet_mail_sent').all()
        assert [a.details for a in acts] == ['to friend@example.invalid']
        sent = InternetMail.query.filter_by(user_id=uid, direction='outbound').one()
        assert sent.subject == SUBJECT and sent.status == 'sent'   # Sent view unchanged
    assert 'biopsy' not in caplog.text.lower()


def test_terminal_compose_activity_entries_omit_subject():
    for rel in ('anetbbs/features/bbs_ui.py', 'anetbbs/features/petscii_ui.py'):
        text = (SRC / rel).read_text()
        calls = re.findall(r"_log_activity\(\s*'internet_mail_sent',[^)]*\)", text)
        assert calls, rel
        for call in calls:
            assert 'subject' not in call, (rel, call)
