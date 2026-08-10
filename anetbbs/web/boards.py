# anetbbs/web/boards.py
"""
Message boards blueprint for forum/bulletin board features
"""
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, jsonify
from flask_login import login_required, current_user
from wtforms import StringField, TextAreaField, SubmitField
from wtforms.validators import DataRequired, Length
from flask_wtf import FlaskForm

from sqlalchemy import func, and_, or_

from ..models import db, Board, Post, BoardSubscription, BoardLastRead
from ..features.access_control import evaluate_access
from ..features.rate_limit import rate_limit, _user_or_ip

boards_bp = Blueprint('boards', __name__, url_prefix='/boards')


def _check_board_access(board):
    """Abort 403 if the current user cannot access this board.

    Real gap found in a full application-wide access-control audit:
    Board.min_access_level is admin-configurable and enforced as a
    WRITE-gate fallback in new_post(), but was never checked on any
    READ path at all -- list_boards()/view_board()/view_post()/
    view_post_ansi() had no @login_required and never consulted this
    field, so a sysop restricting a board to registered/VIP/sysop
    users only still left it fully readable (including full text
    search) to anonymous visitors and any logged-in user regardless
    of level."""
    if not evaluate_access(current_user, board.min_access_level):
        abort(403)


class PostForm(FlaskForm):
    """Form for creating/editing posts"""
    subject = StringField('Subject', validators=[DataRequired(), Length(min=3, max=200)])
    content = TextAreaField('Content', validators=[DataRequired(), Length(min=10)])
    submit = SubmitField('Post')


class ReplyForm(FlaskForm):
    """Form for replying to posts"""
    content = TextAreaField('Reply', validators=[DataRequired(), Length(min=1)])
    submit = SubmitField('Reply')


@boards_bp.route('/')
def list_boards():
    """List all message boards"""
    # Real gap found live: boards always sorted by the admin-configured
    # manual Board.order field, with no way to see which boards actually
    # have recent activity without scanning every category by eye --
    # Jerry: "recent posts are kind of hard to find... maybe a filter
    # for order by date." ?sort=activity reorders boards (within their
    # existing category groups) by most-recent-post-first instead.
    sort_mode = (request.args.get('sort') or 'category').strip()
    boards = [b for b in
             Board.query.filter_by(is_active=True).order_by(Board.order).all()
             if evaluate_access(current_user, b.min_access_level)]

    # Per-user last-read map for unread counts.
    last_read = {}
    if current_user.is_authenticated:
        try:
            for r in BoardLastRead.query.filter_by(user_id=current_user.id).all():
                last_read[r.board_id] = r.last_read_at
        except Exception:
            db.session.rollback()

    # Get post counts for each board.
    #
    # Real perf bug found live: this used to run 3 separate .count()
    # queries PER BOARD (post_count, reply_count, unread) -- 3N+1 total
    # DB round trips on the public board index, hit by every visitor
    # (including anonymous ones, since this route has no @login_required)
    # on every single page load. Collapsed to a small, fixed number of
    # grouped-aggregate queries that scale with query count, not board
    # count.
    board_ids = [b.id for b in boards]
    post_counts = {}
    reply_counts = {}
    if board_ids:
        post_counts = dict(
            db.session.query(Post.board_id, func.count(Post.id))
            .filter(Post.board_id.in_(board_ids), Post.parent_id.is_(None))
            .group_by(Post.board_id).all())
        reply_counts = dict(
            db.session.query(Post.board_id, func.count(Post.id))
            .filter(Post.board_id.in_(board_ids), Post.parent_id.isnot(None))
            .group_by(Post.board_id).all())

    last_activity = {}
    if board_ids:
        last_activity = dict(
            db.session.query(Post.board_id, func.max(Post.created_at))
            .filter(Post.board_id.in_(board_ids))
            .group_by(Post.board_id).all())
    if sort_mode == 'activity':
        boards = sorted(boards, key=lambda b: last_activity.get(b.id) or datetime.min,
                        reverse=True)

    unread_counts = {}
    if board_ids and current_user.is_authenticated:
        # Each board has its own "since" threshold (or none), so this is
        # one query with a per-board OR'd condition instead of one query
        # per board.
        conditions = []
        for board in boards:
            since = last_read.get(board.id)
            base = (Post.board_id == board.id, Post.author_id != current_user.id)
            conditions.append(and_(*base, Post.created_at > since) if since
                              else and_(*base))
        unread_counts = dict(
            db.session.query(Post.board_id, func.count(Post.id))
            .filter(or_(*conditions))
            .group_by(Post.board_id).all())

    board_stats = {
        board.id: {
            'post_count': post_counts.get(board.id, 0),
            'reply_count': reply_counts.get(board.id, 0),
            'unread': unread_counts.get(board.id, 0),
            'last_activity': last_activity.get(board.id),
        }
        for board in boards
    }

    # Group by category for a sub-conference style listing.
    by_category = {}
    for b in boards:
        key = (b.category or '').strip() or 'General'
        by_category.setdefault(key, []).append(b)
    # Sort categories alphabetically with "General" first.
    cats = sorted(by_category.keys(),
                  key=lambda k: (0 if k == 'General' else 1, k.lower()))
    grouped = [(c, by_category[c]) for c in cats]
    return render_template('boards/list.html', boards=boards,
                           board_stats=board_stats, grouped=grouped,
                           sort_mode=sort_mode)


@boards_bp.route('/<int:board_id>')
def view_board(board_id):
    """View posts in a specific board"""
    board = Board.query.get_or_404(board_id)

    if not board.is_active:
        abort(404)
    _check_board_access(board)

    # Pagination
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    # Get top-level posts (not replies). Pinned posts always come first.
    pagination = Post.query.filter_by(board_id=board_id, parent_id=None)\
        .order_by(Post.is_pinned.desc(), Post.created_at.desc())\
        .paginate(page=page, per_page=per_page, error_out=False)
    
    posts = pagination.items

    # Mark this board read for the current user.
    if current_user.is_authenticated:
        try:
            row = BoardLastRead.query.filter_by(
                user_id=current_user.id, board_id=board.id).first()
            if row is None:
                row = BoardLastRead(user_id=current_user.id, board_id=board.id)
                db.session.add(row)
            row.last_read_at = datetime.utcnow()
            db.session.commit()
        except Exception:
            db.session.rollback()

    return render_template('boards/view.html',
                         board=board,
                         posts=posts,
                         pagination=pagination)


@boards_bp.route('/<int:board_id>/new', methods=['GET', 'POST'])
@login_required
# Real gap found in a full message-boards security audit: no flood
# protection at all on board posting, unlike /api/vote (60/min) or
# /imsg/send (30/hr) elsewhere in this codebase. GET requests (just
# viewing the form) also count against the bucket, same as /auth/login's
# own rate_limit usage -- generous enough (20/5min) that no legitimate
# user should ever notice it.
@rate_limit('board_post', limit=20, window=300, key_fn=_user_or_ip)
def new_post(board_id):
    """Create a new post in a board"""
    board = Board.query.get_or_404(board_id)

    if not board.is_active:
        abort(404)

    # Enforce write-level access: if min_write_level is set, the user must
    # meet it to post.  Non-admins hitting this get a 403.
    if not current_user.is_admin:
        write_lvl = (board.min_write_level
                     if board.min_write_level is not None
                     else board.min_access_level)
        if (current_user.access_level or 0) < write_lvl:
            abort(403)
    
    form = PostForm()
    if form.validate_on_submit():
        try:
            from ..features import word_filter as _wf
            clean_subject = _wf.apply(form.subject.data or '')
            clean_content = _wf.apply(form.content.data or '')
        except Exception:
            clean_subject = form.subject.data
            clean_content = form.content.data
        _tagline_id = request.form.get('tagline_id', type=int)
        if _tagline_id:
            from ..models import Tagline, format_tagline_append
            _t = Tagline.query.filter_by(id=_tagline_id, is_active=True).first()
            if _t:
                clean_content = clean_content + format_tagline_append(_t.text)
        post = Post(
            board_id=board_id,
            author_id=current_user.id,
            subject=clean_subject,
            content=clean_content
        )
        db.session.add(post)
        db.session.commit()

        try:
            from ..features.webhooks import fire
            fire('post', {'user': current_user.username, 'board_id': board_id,
                          'subject': clean_subject, 'content': clean_content})
        except Exception:
            pass

        try:
            from ..features.notify import notify, notify_mentions
            url = url_for('boards.view_post', post_id=post.id)
            notify_mentions(f'{clean_subject}\n{clean_content}',
                            current_user.username, target_url=url,
                            min_access_level=board.min_access_level)
            # Fan out to board subscribers (excluding the author). Real
            # gap found in a full message-boards security audit:
            # subscribe() previously had no board-access check at all,
            # so a below-level user could subscribe to a restricted
            # board and get every future post's subject/author leaked
            # via this loop -- subscribe() is now gated too, but this
            # also re-checks per-subscriber in case a board's own
            # min_access_level was raised after someone subscribed.
            from ..models import User
            from ..features.access_control import evaluate_access
            subs = (BoardSubscription.query
                    .filter_by(board_id=post.board_id).all())
            for s in subs:
                if s.user_id == current_user.id:
                    continue
                sub_user = User.query.get(s.user_id)
                if sub_user is None or not evaluate_access(
                        sub_user, board.min_access_level):
                    continue
                notify(s.user_id, 'subscription',
                       title=f'New post in {post.board.name}',
                       body=f'{current_user.username}: {clean_subject}',
                       target_url=url)
        except Exception:
            pass
        try:
            from ..features.achievements import check_for_user
            check_for_user(current_user)
        except Exception:
            db.session.rollback()

        flash('Post created successfully!', 'success')
        return redirect(url_for('boards.view_post', post_id=post.id))
    
    from ..models import get_active_taglines
    return render_template('boards/new_post.html', form=form, board=board,
                           taglines=get_active_taglines())


@boards_bp.route('/post/<int:post_id>')
def view_post(post_id):
    """View a specific post and the full reply tree below it."""
    post = Post.query.get_or_404(post_id)
    _check_board_access(post.board)

    # Real perf bug found live: this used to fetch each node's children
    # with its own query -- N+1 for N nodes in the tree, so a popular
    # thread with hundreds of replies cost hundreds of DB round trips.
    # Fetch the whole subtree level-by-level instead: one query per
    # DEPTH, covering every node at that depth via a single IN(...)
    # regardless of how many siblings it has (a thread with 300 replies
    # all at depth 1 now costs 2 queries total, not 301). The final
    # depth-first, chronological-per-level display order is then
    # assembled in memory from the already-fetched data -- identical
    # output to the old per-node-query version, just far fewer queries
    # for wide/bushy threads.
    children_by_parent = {}
    seen = {post.id}
    level_ids = [post.id]
    while level_ids:
        rows = (Post.query.filter(Post.parent_id.in_(level_ids))
                .order_by(Post.created_at.asc()).all())
        next_level_ids = []
        for c in rows:
            if c.id in seen:
                continue
            seen.add(c.id)
            children_by_parent.setdefault(c.parent_id, []).append(c)
            next_level_ids.append(c.id)
        level_ids = next_level_ids

    # Walk the now-fully-fetched tree iteratively (depth-first) so the
    # template can render reply-to-reply threads with indentation. We
    # bound the DISPLAYED depth at 20 to keep abusive nests from
    # blowing up the page layout (matches the original behavior).
    flat = []
    stack = [(post, 0)]
    while stack:
        node, depth = stack.pop()
        if node.id != post.id:
            flat.append((node, min(depth, 20)))
        # Children were fetched oldest-first; push in reverse (newest
        # first) so popping the stack yields chronological order, same
        # as the original "children newest-last" stack discipline.
        for c in reversed(children_by_parent.get(node.id, [])):
            stack.append((c, depth + 1))

    form = ReplyForm()
    from ..models import get_active_taglines
    return render_template('boards/view_post.html',
                           post=post,
                           replies=flat,
                           form=form,
                           taglines=get_active_taglines())


@boards_bp.route('/post/<int:post_id>/markdown')
def view_post_markdown(post_id):
    """Lazy JSON endpoint backing view_post.html's "Toggle Markdown view"
    -- see echomail.py's read_markdown() for why this moved off the
    always-on render path. Same access check as view_post() above."""
    post = Post.query.get_or_404(post_id)
    _check_board_access(post.board)
    from .markdown_render import render_markdown
    return jsonify({'html': str(render_markdown(post.content))})


@boards_bp.route('/post/<int:post_id>/ansi')
def view_post_ansi(post_id):
    """Render a post's body as colorized HTML, interpreting ANSI escape
    codes and CP437 high bytes. Useful for posts that paste ANSI art."""
    from ..features.ansi_html import to_html
    post = Post.query.get_or_404(post_id)
    _check_board_access(post.board)
    rendered = to_html(post.content or '')
    return render_template('boards/view_ansi.html', post=post, rendered=rendered)


@boards_bp.route('/post/<int:post_id>/reply', methods=['POST'])
@login_required
@rate_limit('board_post', limit=20, window=300, key_fn=_user_or_ip)
def reply_post(post_id):
    """Reply to a post (or to one of its descendants for nested threading).

    The URL post_id is the thread the user is viewing; an optional
    `reply_to` form field lets the user reply to a specific descendant,
    so threads can nest beyond one level.
    """
    parent_post = Post.query.get_or_404(post_id)

    # Real gap found in a full message-boards security audit: unlike
    # new_post(), this route had NO board-access check at all -- not
    # even the read-level gate, let alone min_write_level. Any
    # authenticated user (access_level 0 included) could POST a reply
    # into a sysop-only/VIP-restricted board's thread just by knowing
    # or guessing a post_id, bypassing both gates entirely.
    _check_board_access(parent_post.board)
    if not current_user.is_admin:
        board = parent_post.board
        write_lvl = (board.min_write_level
                     if board.min_write_level is not None
                     else board.min_access_level)
        if (current_user.access_level or 0) < write_lvl:
            abort(403)

    # Walk up to find the thread root so we always redirect there.
    root = parent_post
    seen = {root.id}
    while root.parent_id and root.parent_id not in seen:
        nxt = Post.query.get(root.parent_id)
        if not nxt:
            break
        seen.add(nxt.id)
        root = nxt

    # Locked threads: only mods + admins may reply.
    if getattr(root, 'is_locked', False) and not _can_moderate_post(root):
        flash('This thread is locked.', 'warning')
        return redirect(url_for('boards.view_post', post_id=root.id))

    # Optional explicit parent for nested threading.
    target_parent_id = post_id
    raw = (request.form.get('reply_to') or '').strip()
    if raw:
        try:
            cand_id = int(raw)
            cand = Post.query.get(cand_id)
            # Only honor if the candidate is in the same thread.
            if cand and cand.board_id == parent_post.board_id:
                # Verify cand descends from root (or is root).
                cur = cand
                walked = {cur.id}
                ok = False
                while cur is not None:
                    if cur.id == root.id:
                        ok = True
                        break
                    if not cur.parent_id or cur.parent_id in walked:
                        break
                    walked.add(cur.parent_id)
                    cur = Post.query.get(cur.parent_id)
                if ok:
                    target_parent_id = cand.id
        except ValueError:
            pass

    form = ReplyForm()
    if form.validate_on_submit():
        try:
            from ..features import word_filter as _wf
            clean_content = _wf.apply(form.content.data or '')
        except Exception:
            clean_content = form.content.data
        _tagline_id = request.form.get('tagline_id', type=int)
        if _tagline_id:
            from ..models import Tagline, format_tagline_append
            _t = Tagline.query.filter_by(id=_tagline_id, is_active=True).first()
            if _t:
                clean_content = clean_content + format_tagline_append(_t.text)
        reply = Post(
            board_id=parent_post.board_id,
            author_id=current_user.id,
            parent_id=target_parent_id,
            subject=f"Re: {root.subject}",
            content=clean_content
        )
        db.session.add(reply)
        db.session.commit()

        try:
            from ..features.webhooks import fire
            fire('post', {'user': current_user.username, 'board_id': reply.board_id,
                          'subject': reply.subject, 'content': clean_content})
        except Exception:
            pass

        # Notifications: @mentions plus a direct ping to the parent author
        # so they know someone replied to their post (unless self-reply).
        try:
            from ..features.notify import notify, notify_mentions
            url = url_for('boards.view_post', post_id=root.id)
            parent_author_id = None
            cand_parent = Post.query.get(target_parent_id)
            if cand_parent:
                parent_author_id = cand_parent.author_id
            if parent_author_id and parent_author_id != current_user.id:
                notify(parent_author_id, 'reply',
                       title=f'{current_user.username} replied to your post',
                       body=clean_content[:280],
                       target_url=url)
            notify_mentions(clean_content, current_user.username,
                            target_url=url,
                            min_access_level=parent_post.board.min_access_level)
        except Exception:
            pass
        try:
            from ..features.achievements import check_for_user
            check_for_user(current_user)
        except Exception:
            db.session.rollback()

        flash('Reply posted successfully!', 'success')
    else:
        flash('Error posting reply. Please check your input.', 'danger')

    return redirect(url_for('boards.view_post', post_id=root.id))


@boards_bp.route('/post/<int:post_id>/delete', methods=['POST'])
@login_required
def delete_post(post_id):
    """Delete a post (author or admin only)"""
    post = Post.query.get_or_404(post_id)
    
    # Check permissions: author, admin, or board moderator may delete.
    is_mod = False
    try:
        from ..models import BoardModerator
        is_mod = (BoardModerator.query
                  .filter_by(board_id=post.board_id,
                             user_id=current_user.id).first() is not None)
    except Exception:
        db.session.rollback()
    if (post.author_id != current_user.id
            and not current_user.is_admin
            and not is_mod):
        abort(403)
    
    board_id = post.board_id
    parent_id = post.parent_id
    
    db.session.delete(post)
    db.session.commit()
    
    flash('Post deleted successfully!', 'success')
    
    # Redirect appropriately
    if parent_id:
        return redirect(url_for('boards.view_post', post_id=parent_id))
    else:
        return redirect(url_for('boards.view_board', board_id=board_id))


@boards_bp.route('/<int:board_id>/subscribe', methods=['POST'])
@login_required
def subscribe(board_id):
    board = Board.query.get_or_404(board_id)
    existing = BoardSubscription.query.filter_by(
        user_id=current_user.id, board_id=board.id).first()
    if existing:
        # Always allow unsubscribing, even if access was revoked after
        # the fact -- only NEW subscriptions need the access check below.
        db.session.delete(existing); db.session.commit()
        flash(f'Unsubscribed from {board.name}.', 'info')
    else:
        # Real gap found in a full message-boards security audit: this
        # had no board-access check at all -- a below-level user could
        # subscribe to a sysop-only/VIP-restricted board and get every
        # future post's subject/author leaked via new_post()'s
        # subscriber-notification fan-out, with zero further action
        # needed after this one request.
        _check_board_access(board)
        db.session.add(BoardSubscription(
            user_id=current_user.id, board_id=board.id))
        db.session.commit()
        flash(f'Subscribed to {board.name}.', 'success')
    return redirect(url_for('boards.view_board', board_id=board.id))


def _can_moderate_post(post):
    if not current_user.is_authenticated:
        return False
    if getattr(current_user, 'is_admin', False):
        return True
    try:
        from ..models import BoardModerator
        return (BoardModerator.query
                .filter_by(board_id=post.board_id,
                           user_id=current_user.id).first() is not None)
    except Exception:
        db.session.rollback()
        return False


@boards_bp.route('/post/<int:post_id>/pin', methods=['POST'])
@login_required
def toggle_pin(post_id):
    post = Post.query.get_or_404(post_id)
    if not _can_moderate_post(post):
        abort(403)
    post.is_pinned = not bool(post.is_pinned)
    db.session.commit()
    flash('Post ' + ('pinned.' if post.is_pinned else 'unpinned.'), 'success')
    return redirect(url_for('boards.view_post', post_id=post.id))


@boards_bp.route('/post/<int:post_id>/lock', methods=['POST'])
@login_required
def toggle_lock(post_id):
    post = Post.query.get_or_404(post_id)
    if not _can_moderate_post(post):
        abort(403)
    post.is_locked = not bool(post.is_locked)
    db.session.commit()
    flash('Thread ' + ('locked.' if post.is_locked else 'unlocked.'), 'success')
    return redirect(url_for('boards.view_post', post_id=post.id))


@boards_bp.route('/post/<int:post_id>/react', methods=['POST'])
@login_required
def react(post_id):
    """Toggle a reaction on a post."""
    from ..models import PostReaction
    post = Post.query.get_or_404(post_id)
    # Real gap found in a full message-boards security audit: no
    # board-access check at all -- any authenticated user could react
    # to (and thereby confirm the existence/content-adjacent metadata
    # of) a post in a board they can't read, just by knowing post_id.
    _check_board_access(post.board)
    kind = (request.form.get('kind') or '').strip().lower()
    if kind not in ('like', 'heart', 'lol', 'wow', 'sad'):
        abort(400)
    existing = PostReaction.query.filter_by(
        user_id=current_user.id, post_id=post.id, kind=kind).first()
    if existing:
        db.session.delete(existing)
    else:
        db.session.add(PostReaction(
            user_id=current_user.id, post_id=post.id, kind=kind))
    db.session.commit()
    return redirect(url_for('boards.view_post', post_id=post.id))


@boards_bp.route('/search')
def search_posts():
    """Full-text-ish search across local board posts (subject + content).

    Case-insensitive substring match. Limited to 100 hits. Skips posts in
    boards the current user can't see (private boards w/ no subscription).
    """
    q = (request.args.get('q') or '').strip()
    if len(q) < 2:
        return render_template('boards/search.html', q=q, results=[])

    pat = f'%{q.lower()}%'
    # Real gap found in a full application-wide access-control audit:
    # this route's own docstring already claimed to skip inaccessible
    # boards ("Skips posts in boards the current user can't see"), but
    # the query never actually filtered on min_access_level -- fetch a
    # wider batch and drop rows the user can't see, same pattern as
    # list_boards() above.
    rows = (db.session.query(Post, Board)
            .join(Board, Post.board_id == Board.id)
            .filter(Board.is_active.is_(True))
            .filter(db.or_(
                db.func.lower(Post.subject).like(pat),
                db.func.lower(Post.content).like(pat)))
            .order_by(Post.created_at.desc())
            .limit(300)
            .all())
    rows = [(post, board) for post, board in rows
           if evaluate_access(current_user, board.min_access_level)][:100]
    return render_template('boards/search.html', q=q, results=rows)
