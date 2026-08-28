# anetbbs/features/social_queue.py
"""
Detects notable events and queues a draft SocialPost for a sysop to
review at /admin/social/ -- see that blueprint and models.SocialPost's
own docstring for the review/approve flow. Nothing in this module ever
posts anything; it only creates 'pending' rows.

Every function here is meant to be called right after the event it
detects already committed successfully (a new GameScore, a new User, a
new Post) -- and every function swallows its own exceptions, since a
social-queue hiccup must never break the real action it's attached to.
"""
import logging
import os
import uuid

from flask import current_app

from ..models import db, SocialPost
from .social_card import render_highlight_card

logger = logging.getLogger(__name__)

# Round-number thresholds worth announcing. Checked with `count % N == 0`
# right after a row is created, so a count that JUMPS over a threshold
# (e.g. a bulk import going from 95 to 110 users in one transaction)
# simply doesn't trigger for that gap -- acceptable; this is a nice-to-
# have announcement, not an audit log, and the next natural threshold
# still fires normally.
USER_MILESTONE_STEP = 100
POST_MILESTONE_STEP = 1000


def _images_dir():
    install_root = current_app.config.get('INSTALL_DIR') or \
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    d = os.path.join(install_root, 'data', 'social_posts')
    os.makedirs(d, exist_ok=True)
    return d


def _save_image(png_bytes):
    name = f'{uuid.uuid4().hex}.png'
    path = os.path.join(_images_dir(), name)
    with open(path, 'wb') as f:
        f.write(png_bytes)
    return path


def _queue(dedupe_key, trigger_kind, trigger_label, text, png_bytes):
    """Shared insert, with the dedupe check as the actual source of
    truth (a unique DB constraint backs it up) rather than trusting
    each caller's own "is this really new" logic alone."""
    if SocialPost.query.filter_by(dedupe_key=dedupe_key).first() is not None:
        return None
    image_path = _save_image(png_bytes) if png_bytes else None
    post = SocialPost(
        trigger_kind=trigger_kind,
        trigger_label=trigger_label,
        dedupe_key=dedupe_key,
        text=text,
        image_path=image_path,
        status='pending',
    )
    db.session.add(post)
    try:
        db.session.commit()
    except Exception:
        # Most likely the unique constraint on dedupe_key, from a
        # concurrent request queuing the same event at the same time.
        db.session.rollback()
        return None
    # Real gap Jerry hit live: nothing told a sysop a post was waiting --
    # the queue page was the only way to find out, and there was no
    # answer to "will I get a notification or do I have to manually
    # check this page." Reuses the same admin-notification path other
    # review queues already use (NUV pending users, bad echomail areas,
    # etc.) -- a persistent bell-badge notification plus a live toast
    # for any admin with a browser tab already open, honoring each
    # admin's own per-kind notify_prefs toggle same as every other kind.
    try:
        from .notify import notify_admins
        notify_admins('social_post_queued',
                      title=f'Social post queued: {trigger_label}',
                      body=text[:200],
                      target_url='/admin/social/')
    except Exception:
        logger.exception('social_post_queued notify failed for post %r',
                         getattr(post, 'id', None))
    return post


def queue_manual_post(text, png_bytes=None, label=None):
    """Sysop-composed post -- the manual counterpart to the automatic
    high-score/milestone triggers above, for announcing a version bump,
    a new feature, or anything else that isn't a detectable in-app
    event. No SOCIAL_POSTING_ENABLED gate: unlike the automatic
    triggers (which fire unattended and need an explicit opt-in), this
    only ever runs in direct response to a sysop's own click on the
    already-admin-gated queue page -- consistent with save()/skip()/
    approve() on that same page, none of which check the flag either.
    Has no natural business key to dedupe on (unlike a specific game
    score or milestone count), so each call gets its own dedupe_key.
    """
    import uuid
    return _queue(f'manual:{uuid.uuid4().hex}', 'manual',
                 label or 'Manual post', text, png_bytes)


def maybe_queue_high_score(score):
    """score: the just-saved GameScore (with .game and .user already
    loadable). Queues a post only if this score is now the #1 entry for
    its game."""
    try:
        if not current_app.config.get('SOCIAL_POSTING_ENABLED'):
            return
        game = score.game
        if game is None:
            return
        from ..models import GameScore
        best = (GameScore.query.filter_by(game_id=game.id)
                .order_by(GameScore.score.desc(), GameScore.achieved_at.asc())
                .first())
        if best is None or best.id != score.id:
            return  # not the #1 entry

        username = score.display_username if hasattr(score, 'display_username') \
            else (score.user.username if score.user else '?')
        bbs_name = current_app.config.get('BBS_NAME', 'ANetBBS')
        headline = 'New High Score!'
        detail = [f'{username} scored {score.score:,} on {game.name}']
        png = render_highlight_card(headline, detail, bbs_name)
        text = f'\U0001F3AE New high score on {bbs_name}! {username} just scored ' \
              f'{score.score:,} on {game.name}. Can you beat it?'
        _queue(f'high_score:{game.id}:{score.id}', 'high_score',
              f'{game.name} — new #1', text, png)
    except Exception:
        logger.exception('maybe_queue_high_score failed for score %r',
                         getattr(score, 'id', None))


def _maybe_queue_milestone(count, step, kind_label, dedupe_prefix, headline):
    if not current_app.config.get('SOCIAL_POSTING_ENABLED'):
        return
    if step <= 0 or count % step != 0 or count == 0:
        return
    bbs_name = current_app.config.get('BBS_NAME', 'ANetBBS')
    detail = [f'{count:,} {kind_label}!']
    png = render_highlight_card(headline, detail, bbs_name)
    text = f'\U0001F389 {bbs_name} just hit {count:,} {kind_label}!'
    _queue(f'{dedupe_prefix}:{count}', 'milestone',
          f'{count:,} {kind_label}', text, png)


def maybe_queue_user_milestone(user_count):
    """user_count: User.query.count() AFTER the new registration committed."""
    try:
        _maybe_queue_milestone(user_count, USER_MILESTONE_STEP,
                               'registered users', 'milestone:users',
                               'BBS Milestone!')
    except Exception:
        logger.exception('maybe_queue_user_milestone failed for count=%r', user_count)


def maybe_queue_post_milestone(post_count):
    """post_count: Post.query.count() AFTER the new post committed."""
    try:
        _maybe_queue_milestone(post_count, POST_MILESTONE_STEP,
                               'board posts', 'milestone:posts',
                               'BBS Milestone!')
    except Exception:
        logger.exception('maybe_queue_post_milestone failed for count=%r', post_count)
