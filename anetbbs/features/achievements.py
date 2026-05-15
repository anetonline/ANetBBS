# anetbbs/features/achievements.py
"""
Achievement engine — checks rules and awards badges.

Defaults seeded on first call to ensure_seeded(). Add more rules in
`_RULES` below — each gets a code, friendly name, and a check function
that returns True if the current user qualifies."""
from datetime import datetime, timedelta

from ..models import (db, Achievement, UserAchievement, Post,
                      ShoutboxPost, EchomailMessage, NetmailMessage,
                      PrivateMessage)


# (code, name, description, icon, check)
def _check_first_post(user):
    return Post.query.filter_by(author_id=user.id).count() >= 1

def _check_post_10(user):
    return Post.query.filter_by(author_id=user.id).count() >= 10

def _check_post_100(user):
    return Post.query.filter_by(author_id=user.id).count() >= 100

def _check_first_login(user):
    return (user.login_count or 0) >= 1

def _check_login_30(user):
    return (user.login_count or 0) >= 30

def _check_first_pm(user):
    return PrivateMessage.query.filter_by(sender_id=user.id).count() >= 1

def _check_first_echomail(user):
    return (EchomailMessage.query
            .filter_by(direction='outbound')
            .filter(EchomailMessage.from_name.ilike(user.username))
            .count() >= 1)

def _check_first_netmail(user):
    return NetmailMessage.query.filter_by(from_user_id=user.id).count() >= 1

def _check_shouter(user):
    return ShoutboxPost.query.filter_by(user_id=user.id).count() >= 25

def _check_veteran(user):
    if user.created_at is None:
        return False
    return (datetime.utcnow() - user.created_at) > timedelta(days=365)


_RULES = [
    ('first_login',      'First Login',           'Logged in once',                'door-open',     _check_first_login),
    ('login_30',         '30-Day Caller',         '30 logins to the BBS',          'calendar-check', _check_login_30),
    ('first_post',       'First Post',            'Made your first board post',    'pencil',        _check_first_post),
    ('post_10',          'Frequent Poster',       '10 board posts',                'chat-text',     _check_post_10),
    ('post_100',         'Centurion Poster',      '100 board posts',               'patch-check',   _check_post_100),
    ('first_pm',         'PM Sent',               'Sent your first private message','envelope',     _check_first_pm),
    ('first_echomail',   'Echomail Hatchling',    'Sent your first echomail',      'broadcast-pin', _check_first_echomail),
    ('first_netmail',    'Netmail Pioneer',       'Sent your first FidoNet netmail','envelope-paper', _check_first_netmail),
    ('shouter',          'Town Crier',            '25 shoutbox posts',             'megaphone',     _check_shouter),
    ('veteran',          'Veteran',               '1+ year on this BBS',           'award',         _check_veteran),
]


def ensure_seeded():
    """Idempotent: insert any Achievement rows from _RULES that are missing."""
    for code, name, desc, icon, _ in _RULES:
        if not Achievement.query.filter_by(code=code).first():
            db.session.add(Achievement(code=code, name=name,
                                        description=desc, icon=icon,
                                        is_active=True))
    db.session.commit()


def check_for_user(user):
    """Run all rules for one user, awarding any newly-earned badges.
    Returns the list of newly-awarded Achievement.code values."""
    if user is None:
        return []
    ensure_seeded()
    newly = []
    earned_codes = {ua.achievement.code for ua in user.achievements_earned
                    if ua.achievement}
    for code, _, _, _, fn in _RULES:
        if code in earned_codes:
            continue
        try:
            if fn(user):
                a = Achievement.query.filter_by(code=code).first()
                if a is not None:
                    db.session.add(UserAchievement(user_id=user.id,
                                                    achievement_id=a.id))
                    newly.append(code)
        except Exception:
            db.session.rollback()
    if newly:
        db.session.commit()
    return newly
