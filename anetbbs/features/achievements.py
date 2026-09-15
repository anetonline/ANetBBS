# anetbbs/features/achievements.py
"""
Achievement engine — checks rules and awards badges.

Defaults seeded on first call to ensure_seeded(). Add more rules in
`_RULES` below — each gets a code, friendly name, and a check function
that returns True if the current user qualifies."""
from datetime import datetime, timedelta

from sqlalchemy import func

from ..models import (db, Achievement, UserAchievement, Post,
                      ShoutboxPost, EchomailMessage, NetmailMessage,
                      PrivateMessage, GameScore, GameSession,
                      WikiRevision, FileUpload)


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

def _check_high_score(user):
    # True if any of this user's GameScore rows is the current #1 for
    # its game -- checked per-row rather than a single aggregate query
    # since a user typically has only a handful of scored games, same
    # simple-query style as every other rule here.
    for s in GameScore.query.filter_by(user_id=user.id).all():
        top = (GameScore.query.filter_by(game_id=s.game_id)
               .order_by(GameScore.score.desc()).first())
        if top is not None and top.user_id == user.id:
            return True
    return False

def _check_game_explorer(user):
    count = (db.session.query(func.count(func.distinct(GameScore.game_id)))
             .filter(GameScore.user_id == user.id).scalar())
    return (count or 0) >= 5

def _check_door_diver(user):
    count = (db.session.query(func.count(func.distinct(GameSession.game_id)))
             .filter(GameSession.user_id == user.id).scalar())
    return (count or 0) >= 5

def _check_first_wiki_edit(user):
    return WikiRevision.query.filter_by(author_id=user.id).count() >= 1

def _check_wiki_10(user):
    return WikiRevision.query.filter_by(author_id=user.id).count() >= 10

def _check_first_upload(user):
    return FileUpload.query.filter_by(uploader_id=user.id).count() >= 1

def _check_upload_10(user):
    return FileUpload.query.filter_by(uploader_id=user.id).count() >= 10

def _check_shout_100(user):
    return ShoutboxPost.query.filter_by(user_id=user.id).count() >= 100


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
    ('high_score',       'High Scorer',           'Held the #1 spot on a Game Center leaderboard', 'trophy', _check_high_score),
    ('game_explorer',    'Game Explorer',         'Scored in 5 different Game Center titles', 'controller', _check_game_explorer),
    ('door_diver',       'Door Diver',            'Played 5 different door games', 'door-closed',  _check_door_diver),
    ('wiki_editor',      'Wiki Editor',           'Made your first wiki edit',     'journal-text',  _check_first_wiki_edit),
    ('wiki_scribe',      'Wiki Scribe',           '10 wiki edits',                 'journal-richtext', _check_wiki_10),
    ('file_uploader',    'File Uploader',         'Uploaded your first file',      'cloud-upload',  _check_first_upload),
    ('file_librarian',   'Librarian',             '10 file uploads',               'archive',       _check_upload_10),
    ('town_legend',      'Town Legend',           '100 shoutbox posts',            'megaphone-fill', _check_shout_100),
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
    newly_named = []  # (code, name) -- for the webhook payload below
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
                    newly_named.append((code, a.name))
        except Exception:
            db.session.rollback()
    if newly:
        db.session.commit()
        try:
            from .webhooks import fire
            for code, name in newly_named:
                fire('achievement', {'user': user.username, 'code': code, 'name': name})
        except Exception:
            pass
    return newly
