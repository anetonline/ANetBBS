"""
Up/down voting on messages — board posts, echomail, netmail, PMs.

Single AJAX POST endpoint at /api/vote. The client sends the message
type and id plus the desired value (+1 or -1). The server:
  - creates a vote if the user hasn't voted on this message
  - removes the vote if they're casting the SAME value again (toggle off)
  - flips the vote if they're casting the OPPOSITE value
and returns the new tally + the user's current vote so the UI can update.

A user can only vote on messages from threads they have read access to;
the route checks visibility per message_type.
"""
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from sqlalchemy import func

from ..models import (db, MessageVote, Post, EchomailMessage,
                       NetmailMessage, PrivateMessage)
from ..features.rate_limit import rate_limit, _user_or_ip


votes_bp = Blueprint('votes', __name__, url_prefix='/api')


# Visibility / existence checks per message type. Each returns True if
# the current user is allowed to vote on the message id, False if not.
def _can_vote_on(msg_type, msg_id):
    # Real gap found in a full message-boards security audit: this
    # module's own docstring claims "the route checks visibility per
    # message_type," but the post/echomail branches only checked
    # existence, never the board/area's own min_access_level -- any
    # authenticated user (cast_vote) or even an anonymous visitor
    # (vote_tally, which only pre-blocks netmail/pm) could vote on or
    # read the tally of a sysop-only/VIP-restricted board post or echo
    # area message just by knowing/guessing its id.
    if msg_type == 'post':
        post = Post.query.filter_by(id=msg_id).first()
        if post is None:
            return False
        from ..features.access_control import evaluate_access
        return evaluate_access(current_user, post.board.min_access_level)
    if msg_type == 'echomail':
        msg = EchomailMessage.query.filter_by(id=msg_id).first()
        if msg is None:
            return False
        from ..features.access_control import evaluate_access
        return evaluate_access(current_user, msg.area.min_access_level,
                               is_sysop_only=msg.area.is_sysop_only)
    if msg_type == 'netmail':
        nm = NetmailMessage.query.filter_by(id=msg_id).first()
        if nm is None:
            return False
        # FTN netmail visibility: must be addressed to this user, or
        # they're the sender, or they're an admin.
        if current_user.is_admin:
            return True
        return (nm.to_user_id == current_user.id
                or nm.from_user_id == current_user.id)
    if msg_type == 'pm':
        pm = PrivateMessage.query.filter_by(id=msg_id).first()
        if pm is None:
            return False
        if current_user.is_admin:
            return True
        return (pm.recipient_id == current_user.id
                or pm.sender_id == current_user.id)
    return False


def get_tally(msg_type, msg_id, user_id=None):
    """Return dict with up, down, score, my_vote (or 0 if none / no user)."""
    rows = (db.session.query(
                func.coalesce(func.sum(
                    db.case((MessageVote.value > 0, 1), else_=0)), 0),
                func.coalesce(func.sum(
                    db.case((MessageVote.value < 0, 1), else_=0)), 0))
            .filter(MessageVote.message_type == msg_type,
                    MessageVote.message_id == msg_id)
            .first())
    up, down = (rows or (0, 0))
    my_vote = 0
    if user_id:
        existing = (MessageVote.query
                    .filter_by(user_id=user_id,
                               message_type=msg_type,
                               message_id=msg_id).first())
        if existing:
            my_vote = existing.value
    return {
        'up': int(up or 0),
        'down': int(down or 0),
        'score': int((up or 0) - (down or 0)),
        'my_vote': my_vote,
    }


@votes_bp.route('/vote', methods=['POST'])
@login_required
@rate_limit('vote', limit=60, window=60, key_fn=_user_or_ip)
def cast_vote():
    """Toggle / switch a vote. Body: JSON {type, id, value}."""
    data = request.get_json(silent=True) or {}
    msg_type = (data.get('type') or '').strip().lower()
    try:
        msg_id = int(data.get('id'))
        value = int(data.get('value'))
    except (TypeError, ValueError):
        return jsonify({'error': 'bad input'}), 400

    if msg_type not in ('post', 'echomail', 'netmail', 'pm'):
        return jsonify({'error': 'bad type'}), 400
    if value not in (1, -1):
        return jsonify({'error': 'value must be +1 or -1'}), 400
    if not _can_vote_on(msg_type, msg_id):
        return jsonify({'error': 'not visible / not found'}), 404

    existing = (MessageVote.query
                .filter_by(user_id=current_user.id,
                           message_type=msg_type,
                           message_id=msg_id).first())
    if existing is None:
        db.session.add(MessageVote(
            user_id=current_user.id,
            message_type=msg_type,
            message_id=msg_id,
            value=value,
        ))
    elif existing.value == value:
        # Same value pressed twice = unvote.
        db.session.delete(existing)
    else:
        # Switching directions.
        existing.value = value

    db.session.commit()
    return jsonify(get_tally(msg_type, msg_id, current_user.id))


@votes_bp.route('/vote/tally')
def vote_tally():
    """Read-only tally lookup. ?type=post&id=42 — returns same dict
    as cast_vote. Useful for refreshing without casting."""
    msg_type = (request.args.get('type') or '').strip().lower()
    try:
        msg_id = int(request.args.get('id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'bad id'}), 400
    if msg_type not in ('post', 'echomail', 'netmail', 'pm'):
        return jsonify({'error': 'bad type'}), 400
    # netmail/pm are private 1-on-1 content -- _can_vote_on() enforces the
    # same read-access check cast_vote() does. Without this, anyone (even
    # anonymous) could learn a private message's vote tally just by
    # guessing its id -- a real gap found in a pre-release audit.
    # Anonymous visitors can never pass the netmail/pm ownership check
    # (there's no current_user to own anything), so short-circuit before
    # _can_vote_on() touches current_user.id/.is_admin for those types.
    if msg_type in ('netmail', 'pm') and not current_user.is_authenticated:
        return jsonify({'error': 'not visible / not found'}), 404
    if not _can_vote_on(msg_type, msg_id):
        return jsonify({'error': 'not visible / not found'}), 404
    user_id = current_user.id if current_user.is_authenticated else None
    return jsonify(get_tally(msg_type, msg_id, user_id))
