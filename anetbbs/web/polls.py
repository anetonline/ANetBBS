# anetbbs/web/polls.py
"""
Voting booth — polls with multi-choice options, one vote per user.

Routes:
    GET  /polls/                  list active + closed polls
    GET  /polls/new               create form (any logged-in user)
    POST /polls/new               create poll
    GET  /polls/<id>              view poll + cast vote
    POST /polls/<id>/vote         record a vote
    POST /polls/<id>/close        close (admin or creator)
"""
import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from ..models import db, Poll, PollOption, PollVote

polls_bp = Blueprint('polls', __name__, url_prefix='/polls')


@polls_bp.route('/')
@login_required
def index():
    active = (Poll.query.filter_by(is_active=True)
              .order_by(Poll.created_at.desc()).all())
    closed = (Poll.query.filter_by(is_active=False)
              .order_by(Poll.created_at.desc()).limit(20).all())
    return render_template('polls/index.html', active=active, closed=closed)


@polls_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new_poll():
    if request.method == 'POST':
        question = (request.form.get('question') or '').strip()
        description = (request.form.get('description') or '').strip()
        options = [o.strip() for o in (request.form.get('options') or '').splitlines()
                   if o.strip()]
        if not question:
            flash('Question is required.', 'danger')
            return render_template('polls/new.html', form=request.form)
        if len(options) < 2:
            flash('Need at least 2 options.', 'danger')
            return render_template('polls/new.html', form=request.form)
        p = Poll(creator_id=current_user.id, question=question,
                 description=description, is_active=True)
        db.session.add(p)
        db.session.flush()
        for i, text in enumerate(options):
            db.session.add(PollOption(poll_id=p.id, text=text, sort_order=i))
        db.session.commit()
        flash('Poll created.', 'success')
        return redirect(url_for('polls.view_poll', poll_id=p.id))
    return render_template('polls/new.html', form={})


@polls_bp.route('/<int:poll_id>')
@login_required
def view_poll(poll_id):
    p = Poll.query.get_or_404(poll_id)
    options = p.options.all()
    user_vote = (PollVote.query.filter_by(
        poll_id=p.id, user_id=current_user.id).first())
    total = sum(o.vote_count for o in options) or 1
    return render_template('polls/view.html',
                           poll=p, options=options,
                           user_vote=user_vote, total=total)


@polls_bp.route('/<int:poll_id>/vote', methods=['POST'])
@login_required
def vote(poll_id):
    p = Poll.query.get_or_404(poll_id)
    if not p.is_active:
        flash('This poll is closed.', 'warning')
        return redirect(url_for('polls.view_poll', poll_id=p.id))

    option_id = request.form.get('option_id', type=int)
    opt = PollOption.query.filter_by(id=option_id, poll_id=p.id).first()
    if opt is None:
        flash('Invalid option.', 'danger')
        return redirect(url_for('polls.view_poll', poll_id=p.id))

    existing = PollVote.query.filter_by(
        poll_id=p.id, user_id=current_user.id).first()
    if existing:
        # Allow vote-change while poll is active.
        existing.option_id = opt.id
    else:
        db.session.add(PollVote(poll_id=p.id, option_id=opt.id,
                                user_id=current_user.id))
    db.session.commit()
    flash('Vote recorded.', 'success')
    return redirect(url_for('polls.view_poll', poll_id=p.id))


@polls_bp.route('/<int:poll_id>/close', methods=['POST'])
@login_required
def close(poll_id):
    p = Poll.query.get_or_404(poll_id)
    if not (current_user.is_admin or p.creator_id == current_user.id):
        abort(403)
    p.is_active = False
    p.closes_at = datetime.datetime.utcnow()
    db.session.commit()
    flash('Poll closed.', 'success')
    return redirect(url_for('polls.view_poll', poll_id=p.id))
