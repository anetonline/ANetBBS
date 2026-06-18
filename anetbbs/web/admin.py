"""
Admin panel blueprint
"""
import sys
import os
import json
from datetime import datetime, timedelta
from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, current_app
from flask_login import login_required, current_user
from wtforms import StringField, TextAreaField, SubmitField, BooleanField, PasswordField, IntegerField
from wtforms.validators import DataRequired, Length, Optional, ValidationError, NumberRange
from flask_wtf import FlaskForm

from .validators import PermissiveEmail as Email
from ..models import (db, User, Board, Post, Message, Theme, UserSession,
                       UserActivity, RegistrationAttempt, PasswordResetToken)

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


def admin_required(f):
    """Decorator to require admin access"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('Admin access required.', 'danger')
            return redirect(url_for('main.index'))
        return f(*args, **kwargs)
    return decorated_function


# ---- Forms ----

class AddUserForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=80)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    is_admin = BooleanField('Admin')
    submit = SubmitField('Add User')

    def validate_username(self, field):
        if User.query.filter_by(username=field.data).first():
            raise ValidationError('Username already taken.')

    def validate_email(self, field):
        if User.query.filter_by(email=field.data).first():
            raise ValidationError('Email already registered.')


class EditUserForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=80)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    is_admin = BooleanField('Admin')
    is_active = BooleanField('Active')
    new_password = PasswordField('New Password (leave blank to keep current)', validators=[Optional(), Length(min=6)])
    submit = SubmitField('Update User')


class BoardForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(max=100)])
    description = TextAreaField('Description')
    category = StringField('Category / sub-conference (e.g. General, Tech)',
                           validators=[Length(max=80)])
    order = StringField('Order', validators=[DataRequired()])
    min_access_level = IntegerField(
        'Min Access Level (0=all, 10=registered, 50=VIP, 100=sysop)',
        validators=[Optional(), NumberRange(min=0, max=255)], default=10)
    is_active = BooleanField('Active', default=True)
    submit = SubmitField('Save Board')


class BulletinForm(FlaskForm):
    title = StringField('Title', validators=[DataRequired(), Length(max=200)])
    content = TextAreaField('Content', validators=[DataRequired()])
    is_pinned = BooleanField('Pinned')
    expires_at = StringField('Expires At (YYYY-MM-DD HH:MM, leave blank for no expiry)', validators=[Optional()])
    submit = SubmitField('Save Bulletin')


class ThemeForm(FlaskForm):
    name = StringField('Name (slug)', validators=[DataRequired(), Length(max=50)])
    display_name = StringField('Display Name', validators=[DataRequired(), Length(max=100)])
    description = TextAreaField('Description')
    css_variables = TextAreaField('CSS Variables (JSON)', validators=[DataRequired()])
    is_default = BooleanField('Default Theme')
    is_active = BooleanField('Active', default=True)
    submit = SubmitField('Save Theme')


# ---- Routes ----

@admin_bp.route('/')
@login_required
@admin_required
def dashboard():
    """Admin dashboard"""
    five_min_ago = datetime.utcnow() - timedelta(minutes=5)
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    one_day_ago = datetime.utcnow() - timedelta(days=1)

    # File queue + caller log are optional — guard imports so a partial DB
    # migration doesn't 500 the dashboard.
    pending_files = 0
    recent_callers = []
    try:
        from ..models import FileQueueEntry
        pending_files = FileQueueEntry.query.filter_by(status='pending').count()
    except Exception:
        db.session.rollback()
    try:
        from ..models import CallerLog
        recent_callers = (CallerLog.query
                          .order_by(CallerLog.started_at.desc())
                          .limit(5).all())
    except Exception:
        db.session.rollback()

    stats = {
        'total_users': User.query.count(),
        'active_users': User.query.filter(User.last_login >= thirty_days_ago).count(),
        'total_posts': Post.query.count(),
        'total_boards': Board.query.count(),
        'total_messages': Message.query.count(),
        'online_users': UserSession.query.filter(UserSession.last_seen >= five_min_ago).count(),
        'new_users_24h': User.query.filter(User.created_at >= one_day_ago).count(),
        'new_posts_24h': Post.query.filter(Post.created_at >= one_day_ago).count(),
        'pending_files': pending_files,
    }

    recent_users = User.query.order_by(User.created_at.desc()).limit(10).all()
    recent_logins = User.query.filter(User.last_login.isnot(None)).order_by(User.last_login.desc()).limit(10).all()
    online_sessions = UserSession.query.filter(UserSession.last_seen >= five_min_ago).all()

    # Recent scheduled-event firings — last 5 across all enabled events.
    # Surfaces TW2 maint / VACUUM / log rotate / anything else the
    # scheduler is running so the sysop notices failures without
    # navigating to /admin/events/. Guard the import + query: a missing
    # table on a fresh-from-.43-or-earlier install shouldn't 500 the
    # dashboard.
    recent_events = []
    try:
        from ..models import ScheduledEvent
        recent_events = (ScheduledEvent.query
                         .filter(ScheduledEvent.last_run_at.isnot(None))
                         .order_by(ScheduledEvent.last_run_at.desc())
                         .limit(5).all())
        # Pre-format the relative age so the template stays dumb.
        for ev in recent_events:
            delta = datetime.utcnow() - ev.last_run_at
            if delta < timedelta(minutes=1):
                ev.relative_age = 'just now'
            elif delta < timedelta(hours=1):
                ev.relative_age = f'{int(delta.total_seconds() // 60)}m ago'
            elif delta < timedelta(days=1):
                ev.relative_age = f'{int(delta.total_seconds() // 3600)}h ago'
            else:
                ev.relative_age = f'{delta.days}d ago'
    except Exception:
        db.session.rollback()
        recent_events = []

    import flask
    system_info = {
        'python_version': sys.version.split()[0],
        'flask_version': flask.__version__,
        'db_type': 'SQLite' if 'sqlite' in str(db.engine.url) else 'PostgreSQL',
    }

    # "Last upgraded" = mtime of the VERSION file in the install root.
    # update.sh rewrites VERSION on every upgrade (auto-update wrapper +
    # manual `bash update.sh` both refresh it), so its mtime is a good
    # proxy for "when did this install last receive a patch." Falls
    # back to None on permission errors or fresh-installs-from-checkout.
    try:
        install_root = current_app.config.get('INSTALL_DIR') or os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        ver_mtime = os.path.getmtime(os.path.join(install_root, 'VERSION'))
        upgraded_at = datetime.utcfromtimestamp(ver_mtime)
        delta = datetime.utcnow() - upgraded_at
        if delta.total_seconds() < 60:
            relative = 'just now'
        elif delta < timedelta(hours=1):
            relative = f'{int(delta.total_seconds() // 60)} min ago'
        elif delta < timedelta(days=1):
            relative = f'{int(delta.total_seconds() // 3600)}h ago'
        else:
            relative = f'{delta.days}d ago'
        system_info['last_upgraded_at'] = upgraded_at.strftime('%Y-%m-%d %H:%M UTC')
        system_info['last_upgraded_relative'] = relative
    except OSError:
        system_info['last_upgraded_at'] = None
        system_info['last_upgraded_relative'] = None

    # "What's new" panel — pull the current VERSION's section out of
    # RELEASE.md so sysops see what changed when they upgrade. Pure best-
    # effort: a missing/unreadable RELEASE.md just hides the panel.
    whats_new = _extract_release_notes_for(current_app.config)

    # "Update available" banner — peek at the cached upstream-check the
    # /admin/upgrades/ page uses. If an upgrade is waiting, the dashboard
    # surfaces it without making the sysop go look. Reuses the in-process
    # 30s TTL cache so this doesn't add a network round-trip per page load.
    update_banner = None
    try:
        from .upgrades import _fetch_upstream, _version_newer
        from ..version import VERSION
        upstream, err = _fetch_upstream(current_app.config, force=False)
        if upstream and not err and _version_newer(upstream.get('version', ''), VERSION):
            update_banner = {
                'current': VERSION,
                'available': upstream.get('version'),
                'size_mb': round((upstream.get('size') or 0) / 1024 / 1024, 1),
            }
    except Exception:
        # Network hiccup or upgrades module not imported — never block
        # the dashboard for a banner that's nice-to-have.
        update_banner = None

    return render_template('admin/dashboard.html',
                           stats=stats,
                           recent_users=recent_users,
                           recent_logins=recent_logins,
                           online_sessions=online_sessions,
                           recent_callers=recent_callers,
                           system_info=system_info,
                           whats_new=whats_new,
                           update_banner=update_banner,
                           recent_events=recent_events)


def _extract_release_notes_for(cfg):
    """Find the section of RELEASE.md that names the running VERSION.

    RELEASE.md sections look like:
        # ANetBBS v1.0a2.42 — alpha 2
        ...
        ## Changes since v1.0a2.41
        ...
        ## Changes since v1.0a2.40

    We grab everything from the first section header that mentions the
    running VERSION down to the next header that mentions a different
    version (so the panel shows the "what landed in this build" delta,
    not the entire history). Returns markdown ready to be rendered, or
    None to hide the panel.
    """
    try:
        from ..version import VERSION
    except Exception:
        return None
    import os, re
    install_root = cfg.get('INSTALL_DIR') or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(install_root, 'RELEASE.md')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read()
    except OSError:
        return None
    # Find the first occurrence of "vX.YaZ.NN" matching VERSION; grab
    # from the line it lives on through to the next header that
    # references a DIFFERENT version, or end-of-file.
    ver_pat = re.escape(VERSION)
    m = re.search(rf'^[#\s]*[^\n]*{ver_pat}[^\n]*$', text, re.MULTILINE)
    if not m:
        return None
    start = m.start()
    # Anchor the closing boundary at "## Changes since v1.0aZ.NN" lines
    # that don't mention our current version.
    rest = text[m.end():]
    end_m = re.search(r'\n#{1,3} .*v\d+\.\d+a\d+\.\d+', rest)
    if end_m:
        end = m.end() + end_m.start()
    else:
        end = len(text)
    return text[start:end].strip()


@admin_bp.route('/users')
@login_required
@admin_required
def users():
    """User management list"""
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '')

    query = User.query
    if search:
        query = query.filter(
            (User.username.ilike(f'%{search}%')) | (User.email.ilike(f'%{search}%'))
        )

    pagination = query.order_by(User.created_at.desc()).paginate(page=page, per_page=20, error_out=False)

    return render_template('admin/users.html', users=pagination.items, pagination=pagination, search=search)


@admin_bp.route('/users/add', methods=['GET', 'POST'])
@login_required
@admin_required
def add_user():
    """Add new user"""
    form = AddUserForm()
    if form.validate_on_submit():
        user = User(
            username=form.username.data,
            email=form.email.data,
            is_admin=form.is_admin.data
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        flash(f'User {user.username} created successfully!', 'success')
        return redirect(url_for('admin.users'))
    return render_template('admin/user_form.html', form=form, title='Add User')


@admin_bp.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_user(user_id):
    """Edit user"""
    user = User.query.get_or_404(user_id)
    form = EditUserForm(obj=user)

    if form.validate_on_submit():
        # Check username uniqueness
        existing = User.query.filter_by(username=form.username.data).first()
        if existing and existing.id != user.id:
            flash('Username already taken.', 'danger')
            return render_template('admin/user_form.html', form=form, title='Edit User', user=user)

        # Check email uniqueness
        existing = User.query.filter_by(email=form.email.data).first()
        if existing and existing.id != user.id:
            flash('Email already registered.', 'danger')
            return render_template('admin/user_form.html', form=form, title='Edit User', user=user)

        user.username = form.username.data
        user.email = form.email.data
        user.is_admin = form.is_admin.data
        user.is_active = form.is_active.data

        if form.new_password.data:
            user.set_password(form.new_password.data)

        db.session.commit()
        flash(f'User {user.username} updated successfully!', 'success')
        return redirect(url_for('admin.users'))
    elif request.method == 'GET':
        form.username.data = user.username
        form.email.data = user.email
        form.is_admin.data = user.is_admin
        form.is_active.data = user.is_active

    return render_template('admin/user_form.html', form=form, title='Edit User', user=user)


@admin_bp.route('/users/<int:user_id>/delete', methods=['GET', 'POST'])
@login_required
@admin_required
def delete_user(user_id):
    """Delete user"""
    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash('You cannot delete your own account.', 'danger')
        return redirect(url_for('admin.users'))

    if request.method == 'POST':
        db.session.delete(user)
        db.session.commit()
        flash(f'User {user.username} deleted.', 'success')
        return redirect(url_for('admin.users'))

    return render_template('admin/confirm_delete.html',
                           item=user,
                           item_type='user',
                           item_name=user.username)


@admin_bp.route('/users/<int:user_id>/toggle-ban', methods=['POST'])
@login_required
@admin_required
def toggle_ban(user_id):
    """Toggle user ban status"""
    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash('You cannot ban yourself.', 'danger')
        return redirect(url_for('admin.users'))

    user.is_active = not user.is_active
    db.session.commit()

    status = 'unbanned' if user.is_active else 'banned'
    flash(f'User {user.username} has been {status}.', 'success')
    return redirect(url_for('admin.users'))


@admin_bp.route('/users/bulk', methods=['POST'])
@login_required
@admin_required
def bulk_users():
    """Bulk user actions"""
    action = request.form.get('action')
    user_ids = request.form.getlist('user_ids')

    if not user_ids:
        flash('No users selected.', 'warning')
        return redirect(url_for('admin.users'))

    users = User.query.filter(User.id.in_(user_ids)).all()
    # Filter out current user
    users = [u for u in users if u.id != current_user.id]

    if action == 'delete':
        for user in users:
            db.session.delete(user)
        db.session.commit()
        flash(f'Deleted {len(users)} users.', 'success')
    elif action == 'ban':
        for user in users:
            user.is_active = False
        db.session.commit()
        flash(f'Banned {len(users)} users.', 'success')
    elif action == 'unban':
        for user in users:
            user.is_active = True
        db.session.commit()
        flash(f'Unbanned {len(users)} users.', 'success')

    return redirect(url_for('admin.users'))


@admin_bp.route('/boards')
@login_required
@admin_required
def boards():
    """Board management"""
    boards = Board.query.order_by(Board.order).all()
    board_stats = {b.id: Post.query.filter_by(board_id=b.id).count() for b in boards}
    return render_template('admin/boards.html', boards=boards, board_stats=board_stats)


@admin_bp.route('/boards/add', methods=['GET', 'POST'])
@login_required
@admin_required
def add_board():
    """Add board"""
    form = BoardForm()
    if form.validate_on_submit():
        board = Board(
            name=form.name.data,
            description=form.description.data,
            category=(form.category.data or '').strip(),
            order=int(form.order.data),
            min_access_level=form.min_access_level.data or 10,
            is_active=form.is_active.data
        )
        db.session.add(board)
        db.session.commit()
        flash(f'Board "{board.name}" created!', 'success')
        return redirect(url_for('admin.boards'))
    return render_template('admin/board_form.html', form=form, title='Add Board')


@admin_bp.route('/boards/<int:board_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_board(board_id):
    """Edit board"""
    board = Board.query.get_or_404(board_id)
    form = BoardForm(obj=board)

    if form.validate_on_submit():
        board.name = form.name.data
        board.description = form.description.data
        board.category = (form.category.data or '').strip()
        board.order = int(form.order.data)
        board.min_access_level = form.min_access_level.data or 10
        board.is_active = form.is_active.data
        db.session.commit()
        flash(f'Board "{board.name}" updated!', 'success')
        return redirect(url_for('admin.boards'))
    elif request.method == 'GET':
        form.name.data = board.name
        form.description.data = board.description
        form.category.data = board.category or ''
        form.order.data = str(board.order)
        form.min_access_level.data = board.min_access_level if board.min_access_level is not None else 10
        form.is_active.data = board.is_active

    return render_template('admin/board_form.html', form=form, title='Edit Board', board=board)


@admin_bp.route('/boards/<int:board_id>/delete', methods=['GET', 'POST'])
@login_required
@admin_required
def delete_board(board_id):
    """Delete board"""
    board = Board.query.get_or_404(board_id)
    post_count = Post.query.filter_by(board_id=board_id).count()

    if request.method == 'POST':
        db.session.delete(board)
        db.session.commit()
        flash(f'Board "{board.name}" deleted.', 'success')
        return redirect(url_for('admin.boards'))

    return render_template('admin/confirm_delete.html',
                           item=board,
                           item_type='board',
                           item_name=board.name,
                           warning=f'This board contains {post_count} posts which will also be deleted.')


@admin_bp.route('/boards/<int:board_id>/moderators', methods=['GET', 'POST'])
@login_required
@admin_required
def board_moderators(board_id):
    """Assign or revoke moderators on a single board."""
    from ..models import BoardModerator
    board = Board.query.get_or_404(board_id)

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            uname = (request.form.get('username') or '').strip()
            u = User.query.filter(User.username.ilike(uname)).first()
            if not u:
                flash('User not found.', 'danger')
            elif BoardModerator.query.filter_by(board_id=board.id,
                                                user_id=u.id).first():
                flash('Already a moderator.', 'info')
            else:
                db.session.add(BoardModerator(board_id=board.id,
                                              user_id=u.id))
                db.session.commit()
                flash(f'{u.username} added as moderator.', 'success')
        elif action == 'remove':
            mod_id = request.form.get('mod_id')
            row = BoardModerator.query.get(mod_id)
            if row and row.board_id == board.id:
                db.session.delete(row); db.session.commit()
                flash('Moderator removed.', 'success')
        return redirect(url_for('admin.board_moderators', board_id=board.id))

    mods = BoardModerator.query.filter_by(board_id=board.id).all()
    return render_template('admin/board_moderators.html',
                           board=board, mods=mods)


@admin_bp.route('/boards/<int:board_id>/move/<direction>', methods=['POST'])
@login_required
@admin_required
def move_board(board_id, direction):
    """Move board up or down in order"""
    board = Board.query.get_or_404(board_id)
    boards = Board.query.order_by(Board.order).all()
    idx = next((i for i, b in enumerate(boards) if b.id == board_id), None)

    if direction == 'up' and idx is not None and idx > 0:
        boards[idx].order, boards[idx-1].order = boards[idx-1].order, boards[idx].order
    elif direction == 'down' and idx is not None and idx < len(boards) - 1:
        boards[idx].order, boards[idx+1].order = boards[idx+1].order, boards[idx].order

    db.session.commit()
    return redirect(url_for('admin.boards'))


@admin_bp.route('/bulletins')
@login_required
@admin_required
def bulletins():
    """Bulletin management"""
    messages = Message.query.order_by(Message.is_pinned.desc(), Message.created_at.desc()).all()
    return render_template('admin/bulletins.html', messages=messages)


@admin_bp.route('/bulletins/add', methods=['GET', 'POST'])
@login_required
@admin_required
def add_bulletin():
    """Add bulletin"""
    form = BulletinForm()
    if form.validate_on_submit():
        expires_at = None
        if form.expires_at.data:
            try:
                expires_at = datetime.strptime(form.expires_at.data, '%Y-%m-%d %H:%M')
            except ValueError:
                flash('Invalid date format. Use YYYY-MM-DD HH:MM', 'danger')
                return render_template('admin/bulletin_form.html', form=form, title='Add Bulletin')

        message = Message(
            author_id=current_user.id,
            title=form.title.data,
            content=form.content.data,
            is_pinned=form.is_pinned.data,
            expires_at=expires_at
        )
        db.session.add(message)
        db.session.commit()
        flash('Bulletin created!', 'success')
        return redirect(url_for('admin.bulletins'))
    return render_template('admin/bulletin_form.html', form=form, title='Add Bulletin')


@admin_bp.route('/bulletins/<int:bulletin_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_bulletin(bulletin_id):
    """Edit bulletin"""
    message = Message.query.get_or_404(bulletin_id)
    form = BulletinForm(obj=message)

    if form.validate_on_submit():
        expires_at = None
        if form.expires_at.data:
            try:
                expires_at = datetime.strptime(form.expires_at.data, '%Y-%m-%d %H:%M')
            except ValueError:
                flash('Invalid date format. Use YYYY-MM-DD HH:MM', 'danger')
                return render_template('admin/bulletin_form.html', form=form, title='Edit Bulletin', message=message)

        message.title = form.title.data
        message.content = form.content.data
        message.is_pinned = form.is_pinned.data
        message.expires_at = expires_at
        db.session.commit()
        flash('Bulletin updated!', 'success')
        return redirect(url_for('admin.bulletins'))
    elif request.method == 'GET':
        form.title.data = message.title
        form.content.data = message.content
        form.is_pinned.data = message.is_pinned
        if message.expires_at:
            form.expires_at.data = message.expires_at.strftime('%Y-%m-%d %H:%M')

    return render_template('admin/bulletin_form.html', form=form, title='Edit Bulletin', message=message)


@admin_bp.route('/bulletins/<int:bulletin_id>/delete', methods=['GET', 'POST'])
@login_required
@admin_required
def delete_bulletin(bulletin_id):
    """Delete bulletin"""
    message = Message.query.get_or_404(bulletin_id)

    if request.method == 'POST':
        db.session.delete(message)
        db.session.commit()
        flash('Bulletin deleted.', 'success')
        return redirect(url_for('admin.bulletins'))

    return render_template('admin/confirm_delete.html',
                           item=message,
                           item_type='bulletin',
                           item_name=message.title)


@admin_bp.route('/bulletins/<int:bulletin_id>/toggle-pin', methods=['POST'])
@login_required
@admin_required
def toggle_pin(bulletin_id):
    """Toggle bulletin pin status"""
    message = Message.query.get_or_404(bulletin_id)
    message.is_pinned = not message.is_pinned
    db.session.commit()
    status = 'pinned' if message.is_pinned else 'unpinned'
    flash(f'Bulletin {status}.', 'success')
    return redirect(url_for('admin.bulletins'))


@admin_bp.route('/themes')
@login_required
@admin_required
def themes():
    """Theme management"""
    themes = Theme.query.order_by(Theme.name).all()
    return render_template('admin/themes.html', themes=themes)


# ── Federation registry admin ────────────────────────────────────────
# Only useful on the BBS designated as the federation hub
# (REGISTRY_MODE_ENABLED=true). On peer installs these views render an
# explanatory "not a hub" message instead of empty tables.

@admin_bp.route('/registry/')
@login_required
@admin_required
def registry_index():
    """List every registered ANetBBS peer with its current state.
    Sysop uses this to approve / reject / edit / delete entries that
    flow in via /registry/api/v1/register.
    """
    from ..models import RegistryEntry
    is_hub = bool(current_app.config.get('REGISTRY_MODE_ENABLED'))
    rows = (RegistryEntry.query
            .order_by(RegistryEntry.is_listed.desc(),
                      RegistryEntry.is_approved.desc(),
                      RegistryEntry.registered_at.desc())
            .all()) if is_hub else []
    return render_template('admin/registry.html', rows=rows, is_hub=is_hub)


@admin_bp.route('/registry/<int:entry_id>/approve', methods=['POST'])
@login_required
@admin_required
def registry_approve(entry_id):
    from ..models import db, RegistryEntry
    e = RegistryEntry.query.get_or_404(entry_id)
    if not e.is_verified:
        flash(f'{e.host}: cannot approve — registrant has not '
              f'verified their contact email yet.', 'warning')
        return redirect(url_for('admin.registry_index'))
    e.is_approved = True
    e.is_listed = True
    db.session.commit()
    flash(f'Approved + listed {e.host}.', 'success')
    return redirect(url_for('admin.registry_index'))


@admin_bp.route('/registry/<int:entry_id>/reject', methods=['POST'])
@login_required
@admin_required
def registry_reject(entry_id):
    from ..models import db, RegistryEntry
    e = RegistryEntry.query.get_or_404(entry_id)
    e.is_approved = False
    e.is_listed = False
    db.session.commit()
    flash(f'Rejected (delisted) {e.host}. Row kept — delete '
          f'separately if you want it gone for good.', 'info')
    return redirect(url_for('admin.registry_index'))


@admin_bp.route('/registry/<int:entry_id>/delete', methods=['POST'])
@login_required
@admin_required
def registry_delete(entry_id):
    from ..models import db, RegistryEntry
    e = RegistryEntry.query.get_or_404(entry_id)
    host = e.host
    db.session.delete(e)
    db.session.commit()
    flash(f'Deleted {host}. They can re-register with a fresh token.',
          'success')
    return redirect(url_for('admin.registry_index'))


@admin_bp.route('/registry/self')
@login_required
@admin_required
def registry_self():
    """Status of THIS BBS's self-registration against the upstream hub.
    Shows the last register/heartbeat response so the sysop can copy
    the verify URL or see why we're not listed."""
    from ..msp.registry_client import _load_state, _our_metadata
    state = _load_state(current_app)
    meta = _our_metadata(current_app)
    return render_template('admin/registry_self.html',
                           state=state,
                           meta=meta,
                           enabled=current_app.config.get('REGISTRY_SELF_REGISTER'),
                           hub=current_app.config.get('REGISTRY_URL'))


@admin_bp.route('/registry/self/register-now', methods=['POST'])
@login_required
@admin_required
def registry_self_register_now():
    """Force a self-register against the hub right now (useful after
    changing SYSOP_EMAIL or BBS metadata so the new info propagates
    without waiting for the daily heartbeat)."""
    from ..msp.registry_client import _tick
    try:
        _tick(current_app._get_current_object())
        flash('Self-registration tick triggered. Check the status below '
              'for the hub response.', 'success')
    except Exception as exc:
        flash(f'Self-registration failed: {exc}', 'danger')
    return redirect(url_for('admin.registry_self'))


@admin_bp.route('/registry/<int:entry_id>/edit', methods=['POST'])
@login_required
@admin_required
def registry_edit(entry_id):
    """Sysop edits the soft-metadata fields. host + contact_email +
    verification state are immutable from here — fixing those requires
    a delete + re-register (intentional to prevent sysop typos from
    masquerading as ownership transfers)."""
    from ..models import db, RegistryEntry
    e = RegistryEntry.query.get_or_404(entry_id)
    e.name = (request.form.get('name') or e.name).strip()
    e.sysop = (request.form.get('sysop') or '').strip()
    e.location = (request.form.get('location') or '').strip()
    e.software = (request.form.get('software') or 'ANetBBS').strip()
    e.software_version = (request.form.get('software_version') or '').strip()
    e.notes = (request.form.get('notes') or '').strip()
    try:
        e.msp_port = int(request.form.get('msp_port', e.msp_port))
        e.systat_port = int(request.form.get('systat_port', e.systat_port))
    except (TypeError, ValueError):
        flash('Ports must be integers.', 'danger')
        return redirect(url_for('admin.registry_index'))
    db.session.commit()
    flash(f'Updated {e.host}.', 'success')
    return redirect(url_for('admin.registry_index'))


@admin_bp.route('/themes/add', methods=['GET', 'POST'])
@login_required
@admin_required
def add_theme():
    """Add theme"""
    form = ThemeForm()
    if form.validate_on_submit():
        # Validate JSON
        try:
            json.loads(form.css_variables.data)
        except json.JSONDecodeError:
            flash('CSS Variables must be valid JSON.', 'danger')
            return render_template('admin/theme_form.html', form=form, title='Add Theme')

        if form.is_default.data:
            Theme.query.update({'is_default': False}, synchronize_session='fetch')

        theme = Theme(
            name=form.name.data,
            display_name=form.display_name.data,
            description=form.description.data,
            css_variables=form.css_variables.data,
            is_default=form.is_default.data,
            is_active=form.is_active.data
        )
        db.session.add(theme)
        db.session.commit()
        flash('Theme created!', 'success')
        return redirect(url_for('admin.themes'))
    return render_template('admin/theme_form.html', form=form, title='Add Theme')


@admin_bp.route('/themes/<int:theme_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_theme(theme_id):
    """Edit theme"""
    theme = Theme.query.get_or_404(theme_id)
    form = ThemeForm(obj=theme)

    if form.validate_on_submit():
        try:
            json.loads(form.css_variables.data)
        except json.JSONDecodeError:
            flash('CSS Variables must be valid JSON.', 'danger')
            return render_template('admin/theme_form.html', form=form, title='Edit Theme', theme=theme)

        if form.is_default.data and not theme.is_default:
            Theme.query.update({'is_default': False}, synchronize_session='fetch')

        theme.name = form.name.data
        theme.display_name = form.display_name.data
        theme.description = form.description.data
        theme.css_variables = form.css_variables.data
        theme.is_default = form.is_default.data
        theme.is_active = form.is_active.data
        db.session.commit()
        flash('Theme updated!', 'success')
        return redirect(url_for('admin.themes'))
    elif request.method == 'GET':
        form.name.data = theme.name
        form.display_name.data = theme.display_name
        form.description.data = theme.description
        form.css_variables.data = theme.css_variables
        form.is_default.data = theme.is_default
        form.is_active.data = theme.is_active

    return render_template('admin/theme_form.html', form=form, title='Edit Theme', theme=theme)


@admin_bp.route('/themes/<int:theme_id>/toggle-active', methods=['POST'])
@login_required
@admin_required
def toggle_theme_active(theme_id):
    """Toggle theme active status"""
    theme = Theme.query.get_or_404(theme_id)
    theme.is_active = not theme.is_active
    db.session.commit()
    status = 'activated' if theme.is_active else 'deactivated'
    flash(f'Theme {status}.', 'success')
    return redirect(url_for('admin.themes'))


EDITABLE_SETTINGS = [
    # (key, label, type, requires_restart)
    ('BBS_NAME', 'BBS Name', 'text', False),
    ('BBS_DESCRIPTION', 'BBS Description', 'text', False),
    ('BBS_DOMAIN', 'Public domain (e.g. bbs.example.com)', 'text', False),
    ('BBS_NODES', 'Number of nodes (1-100)', 'text', True),
    ('PERSONAL_PAGES_ENABLED', 'Personal Web Pages (true/false)', 'text', True),
    ('NUV_ENABLED', 'New User Verification — sysop approves new users (true/false)', 'text', True),
    ('RATIO_MIN', 'File ratio min (0.05 = 5%, 0 = off)', 'text', False),
    ('IDLE_TIMEOUT_SECONDS', 'Terminal idle timeout (sec, 0 = never)', 'text', True),
    ('BOT_GATE_TIMEOUT', 'Bot-gate timeout (sec) — pre-login challenge wait', 'text', True),
    ('LOG_LEVEL', 'Log Level (DEBUG/INFO/WARNING/ERROR)', 'text', False),
    ('TELNET_ENABLED', 'Telnet Enabled (true/false)', 'text', True),
    ('TELNET_PORT', 'Telnet Port', 'text', True),
    ('SSH_ENABLED', 'SSH Enabled (true/false)', 'text', True),
    ('SSH_PORT', 'SSH Port', 'text', True),
    ('RLOGIN_ENABLED', 'rlogin Enabled (true/false)', 'text', True),
    ('RLOGIN_PORT', 'rlogin Port', 'text', True),
    ('FTP_ENABLED', 'FTP Enabled (true/false)', 'text', True),
    ('FTP_PORT', 'FTP Port (21 needs CAP_NET_BIND_SERVICE)', 'text', True),
    ('FTP_ANON_ENABLED', 'FTP Anonymous Login (true/false)', 'text', True),
    ('FTP_PASV_PORTS', 'FTP Passive Port Range (e.g. 40000-40050)', 'text', True),
    ('FTP_TLS_CERTFILE', 'FTP TLS Cert path (blank = plain FTP)', 'text', True),
    ('FTP_TLS_KEYFILE', 'FTP TLS Key path (blank = plain FTP)', 'text', True),
    ('FTP_ROOT_DIR', 'FTP Root Dir (symlink tree)', 'text', True),
    ('FTP_BANNER', 'FTP Connect Banner', 'text', True),
    ('GAMES_ENABLED', 'Door Games Enabled (true/false)', 'text', False),
    ('GAMES_MAX_NODES', 'Door Games Max Nodes', 'text', False),
    ('DOSBOX_PATH', 'DOSBox path', 'text', False),
    ('NODEJS_PATH', 'Node.js path', 'text', False),
    ('MYSTIC_PYTHON_PATH', 'Mystic Python path', 'text', False),
    ('MYSTIC_BBS_PATH', 'Mystic BBS path', 'text', False),
    ('ECHOMAIL_ENABLED', 'Echomail Enabled (true/false)', 'text', False),
    ('ECHOMAIL_POLL_ENABLED', 'Echomail Auto-Poll (true/false)', 'text', False),
    ('MRC_BRIDGE_HOST', 'MRC Bridge Host', 'text', False),
    ('MRC_BRIDGE_PORT', 'MRC Bridge Port', 'text', False),
    ('MRC_BRIDGE_WS_PATH', 'MRC Bridge WS Path', 'text', False),
]


def _read_env_file(env_path):
    """Parse a simple KEY=VALUE .env file into a dict (preserving order)."""
    out = {}
    if not os.path.exists(env_path):
        return out
    try:
        with open(env_path, 'r') as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith('#') or '=' not in stripped:
                    continue
                k, _, v = stripped.partition('=')
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def _write_env_keys(env_path, updates):
    """Update specific KEY=VALUE pairs in an .env file, preserving existing
    lines (comments + blanks + unrelated keys) and order. Adds missing keys
    at the bottom."""
    existing_lines = []
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            existing_lines = f.readlines()

    seen = set()
    new_lines = []
    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith('#') and '=' in stripped:
            k = stripped.split('=', 1)[0].strip()
            if k in updates:
                new_lines.append(f'{k}={updates[k]}\n')
                seen.add(k)
                continue
        new_lines.append(line)

    for k, v in updates.items():
        if k not in seen:
            new_lines.append(f'{k}={v}\n')

    # Write atomically
    tmp = env_path + '.new'
    with open(tmp, 'w') as f:
        f.writelines(new_lines)
    os.replace(tmp, env_path)


def _env_path():
    """Locate the install dir's .env (one level up from the package)."""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '..', '.env')


@admin_bp.route('/settings', methods=['GET', 'POST'])
@login_required
@admin_required
def settings():
    """System settings view AND editor — POST writes back to .env."""
    from flask import current_app, request, flash, redirect, url_for

    env_path = os.path.abspath(_env_path())
    needs_restart = False

    if request.method == 'POST':
        updates = {}
        for key, _, _, restart_flag in EDITABLE_SETTINGS:
            new_val = request.form.get(key, '')
            if new_val:
                updates[key] = new_val
                if restart_flag:
                    needs_restart = True
        if updates:
            try:
                _write_env_keys(env_path, updates)
                # Update live config too (for non-restart-required keys)
                for k, v in updates.items():
                    if k in current_app.config:
                        current_app.config[k] = v
                if needs_restart:
                    flash('Settings saved. Some changes require a service '
                          'restart: sudo systemctl restart anetbbs.service '
                          'anetbbs-telnet.service anetbbs-ssh.service '
                          'anetbbs-rlogin.service', 'warning')
                else:
                    flash('Settings saved.', 'success')
            except Exception as exc:
                flash(f'Failed to save settings: {exc}', 'danger')
        return redirect(url_for('admin.settings'))

    # Read last 100 lines of log file
    log_lines = []
    log_file = current_app.config.get('LOG_FILE', '')
    if log_file and os.path.exists(log_file):
        try:
            with open(log_file, 'r') as f:
                log_lines = f.readlines()[-100:]
        except Exception:
            log_lines = ['Could not read log file.']

    # Mask database URI
    db_uri = str(db.engine.url)
    if '@' in db_uri:
        parts = db_uri.split('@')
        db_uri = '***@' + parts[-1]

    # Read CURRENT values from the .env file (so the form shows what's persisted)
    env_values = _read_env_file(env_path)
    editable = []
    for key, label, kind, restart_flag in EDITABLE_SETTINGS:
        editable.append({
            'key': key,
            'label': label,
            'kind': kind,
            'requires_restart': restart_flag,
            'value': env_values.get(key, current_app.config.get(key, '')),
        })

    config_info = {
        'BBS_NAME': current_app.config.get('BBS_NAME'),
        'BBS_DESCRIPTION': current_app.config.get('BBS_DESCRIPTION'),
        'DATA_DIR': current_app.config.get('DATA_DIR'),
        'UPLOADS_DIR': current_app.config.get('UPLOADS_DIR'),
        'AVATARS_DIR': current_app.config.get('AVATARS_DIR'),
        'LOG_LEVEL': current_app.config.get('LOG_LEVEL'),
        'DB_URI': db_uri,
        'DEBUG': current_app.config.get('DEBUG'),
        'TESTING': current_app.config.get('TESTING'),
        'UPLOAD_MAX_SIZE': current_app.config.get('UPLOAD_MAX_SIZE'),
        'AVATAR_MAX_SIZE': current_app.config.get('AVATAR_MAX_SIZE'),
        'ENV_FILE': env_path,
    }

    return render_template('admin/settings.html',
                           config_info=config_info,
                           log_lines=log_lines,
                           editable=editable)


@admin_bp.route('/activity')
@login_required
@admin_required
def activity():
    """User activity audit log — paginated, filterable.

    Filters:
        ?type=login     show only one activity_type
        ?user=<id>      show only one user
        ?ip=<ip>        show only one IP
    """
    page = max(1, int(request.args.get('page', 1) or 1))
    per_page = 100
    q = UserActivity.query

    activity_type = request.args.get('type', '').strip()
    if activity_type:
        q = q.filter(UserActivity.activity_type == activity_type)

    user_filter = request.args.get('user', '').strip()
    if user_filter.isdigit():
        q = q.filter(UserActivity.user_id == int(user_filter))

    ip_filter = request.args.get('ip', '').strip()
    if ip_filter:
        q = q.filter(UserActivity.ip_address == ip_filter)

    pagination = (q.order_by(UserActivity.created_at.desc())
                   .paginate(page=page, per_page=per_page, error_out=False))

    distinct_types = (db.session.query(UserActivity.activity_type)
                      .distinct().order_by(UserActivity.activity_type).all())
    return render_template('admin/activity.html',
                           pagination=pagination,
                           distinct_types=[t[0] for t in distinct_types],
                           current_type=activity_type,
                           current_user_filter=user_filter,
                           current_ip=ip_filter)


@admin_bp.route('/registration-attempts')
@login_required
@admin_required
def registration_attempts():
    """Sysop-visible log of registration attempts (success + failure)."""
    page = max(1, int(request.args.get('page', 1) or 1))
    per_page = 100
    pagination = (RegistrationAttempt.query
                  .order_by(RegistrationAttempt.created_at.desc())
                  .paginate(page=page, per_page=per_page, error_out=False))
    return render_template('admin/registration_attempts.html',
                           pagination=pagination)


@admin_bp.route('/chat-bans', methods=['GET', 'POST'])
@login_required
@admin_required
def chat_bans():
    """List + create MRC chat bans (kick/ban users from rooms with optional expiry)."""
    from ..models import ChatBan
    from datetime import timedelta as _td

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            user_id = request.form.get('user_id', type=int)
            target = User.query.get(user_id)
            if target is None:
                flash('User not found.', 'danger')
                return redirect(url_for('admin.chat_bans'))
            room = (request.form.get('room') or '').strip() or None
            reason = (request.form.get('reason') or '').strip()
            duration_h = request.form.get('duration_hours', type=int)
            expires = (datetime.utcnow() + _td(hours=duration_h)
                       if duration_h else None)
            db.session.add(ChatBan(
                user_id=target.id, room=room, reason=reason or None,
                issued_by_id=current_user.id, expires_at=expires))
            db.session.commit()
            flash(f'Banned {target.username} from {room or "all rooms"}.', 'success')
        elif action == 'remove':
            ban_id = request.form.get('ban_id', type=int)
            b = ChatBan.query.get(ban_id)
            if b is not None:
                db.session.delete(b)
                db.session.commit()
                flash('Ban lifted.', 'success')
        return redirect(url_for('admin.chat_bans'))

    # Auto-clean expired bans on page render — natural moment since the
    # admin is checking enforcement state. Permanent (NULL expires_at) and
    # not-yet-expired bans are kept.
    cutoff = datetime.utcnow()
    expired = (ChatBan.query
               .filter(ChatBan.expires_at.isnot(None))
               .filter(ChatBan.expires_at <= cutoff)
               .all())
    if expired:
        for b in expired:
            db.session.delete(b)
        db.session.commit()

    bans = ChatBan.query.order_by(ChatBan.created_at.desc()).all()
    users = User.query.order_by(User.username).all()
    return render_template('admin/chat_bans.html', bans=bans, users=users)


@admin_bp.route('/default-echos', methods=['GET', 'POST'])
@login_required
@admin_required
def default_echos():
    """Sysop sets which echo areas auto-subscribe for new users.

    Implementation: we just toggle EchoArea.is_subscribed at the global level —
    new echo messages from those areas are received from the network. Per-user
    subscription is a future refinement once the user_echo_subscriptions
    table is added; for now this is the global default."""
    from ..models import EchoArea, EchomailNetwork

    if request.method == 'POST':
        # Form posts a list of area_ids that should be subscribed.
        wanted = set(request.form.getlist('area_id', type=int))
        all_areas = EchoArea.query.all()
        changed = 0
        for a in all_areas:
            new_state = a.id in wanted
            if a.is_subscribed != new_state:
                a.is_subscribed = new_state
                changed += 1
        db.session.commit()
        flash(f'Updated {changed} echo area subscription(s).', 'success')
        return redirect(url_for('admin.default_echos'))

    networks = EchomailNetwork.query.filter_by(is_active=True).all()
    by_network = {n.id: list(n.areas.order_by('tag').all()) for n in networks}
    return render_template('admin/default_echos.html',
                           networks=networks, by_network=by_network)


@admin_bp.route('/tic-log')
@login_required
@admin_required
def tic_log():
    """Sysop view of inbound TIC processing — successes, errors, current state."""
    from ..models import TicFile
    page = max(1, int(request.args.get('page', 1) or 1))
    pagination = (TicFile.query
                  .order_by(TicFile.received_at.desc())
                  .paginate(page=page, per_page=50, error_out=False))
    return render_template('admin/tic_log.html', pagination=pagination)


@admin_bp.route('/tic-log/<int:tic_id>')
@login_required
@admin_required
def tic_detail(tic_id):
    from ..models import TicFile
    tic = TicFile.query.get_or_404(tic_id)
    import json as _json
    seenby = _json.loads(tic.seenby) if tic.seenby else []
    path = _json.loads(tic.path) if tic.path else []
    return render_template('admin/tic_detail.html',
                           tic=tic, seenby=seenby, path=path)


@admin_bp.route('/file-areas', methods=['GET', 'POST'])
@login_required
@admin_required
def file_areas_admin():
    """List/edit file areas — sysop tool."""
    from ..models import FileArea, EchomailNetwork

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'create':
            tag = (request.form.get('tag') or '').strip().upper()
            name = (request.form.get('name') or '').strip()
            storage_path = (request.form.get('storage_path') or '').strip()
            net_id = request.form.get('network_id', type=int) or None
            if not tag:
                flash('Tag required.', 'danger')
            elif FileArea.query.filter_by(tag=tag).first():
                flash(f'Area {tag} already exists.', 'warning')
            else:
                fa = FileArea(
                    tag=tag, name=name or tag,
                    storage_path=storage_path or None,
                    network_id=net_id,
                    is_active=True, is_subscribed=True)
                db.session.add(fa)
                db.session.commit()
                flash(f'Created area {tag}.', 'success')
        elif action == 'update':
            fa = FileArea.query.get_or_404(request.form.get('area_id', type=int))
            fa.name = (request.form.get('name') or fa.name).strip()
            fa.storage_path = (request.form.get('storage_path') or '').strip() or None
            fa.description = (request.form.get('description') or '').strip() or None
            fa.is_active = bool(request.form.get('is_active'))
            fa.is_subscribed = bool(request.form.get('is_subscribed'))
            fa.is_sysop_only = bool(request.form.get('is_sysop_only'))
            fa.upload_permission = (request.form.get('upload_permission')
                                    or fa.upload_permission)
            fa.password = (request.form.get('password') or '').strip() or None
            fa.is_nodelist_source = bool(request.form.get('is_nodelist_source'))
            fa.nodelist_domain = (request.form.get('nodelist_domain')
                                  or '').strip().lower() or None
            fa.min_access_level = int(request.form.get('min_access_level') or 10)
            db.session.commit()
            flash(f'Updated {fa.tag}.', 'success')
        elif action == 'delete':
            fa = FileArea.query.get_or_404(request.form.get('area_id', type=int))
            tag = fa.tag
            db.session.delete(fa)
            db.session.commit()
            flash(f'Deleted {tag}.', 'success')
        return redirect(url_for('admin.file_areas_admin'))

    areas = FileArea.query.order_by(FileArea.tag).all()
    networks = EchomailNetwork.query.filter_by(is_active=True).all()
    return render_template('admin/file_areas.html', areas=areas, networks=networks)


@admin_bp.route('/file-areas/networks/<int:network_id>/subscribe_all',
                methods=['POST'])
@login_required
@admin_required
def file_areas_subscribe_all(network_id):
    """Mark every FileArea on this network as locally subscribed AND queue
    FileFix `+TAG1 +TAG2 ...` for each known tag. Mystic does NOT honor
    `+ALL` as a magic keyword (it's interpreted as a literal area named
    "ALL"), so we enumerate the tags from our local FileArea rows. Run
    `Bulk Import` for FILEBONE first to populate the list."""
    from ..models import FileArea, EchomailNetwork
    network = EchomailNetwork.query.get_or_404(network_id)
    if network.network_type != 'binkp':
        flash('Subscribe-all only applies to BinkP networks.', 'warning')
        return redirect(url_for('admin.file_areas_admin'))
    tags = [a.tag for a in
            FileArea.query.filter_by(network_id=network.id).all()]
    n_local = (FileArea.query.filter_by(network_id=network.id)
               .update({FileArea.is_subscribed: True},
                       synchronize_session=False))
    db.session.commit()
    if not tags:
        flash('No local file areas configured for this network. Use '
              '"Bulk Import" first to load a FILEBONE file.', 'warning')
        return redirect(url_for('admin.file_areas_admin'))
    from ..echomail.areafix import send_areafix_request
    nm_id = send_areafix_request(network, plus_tags=tags,
                                  robot_name='FileFix',
                                  subject='FileFix request')
    if nm_id:
        flash(f'Marked {n_local} local file areas subscribed; queued FileFix '
              f'request with {len(tags)} +TAG entries to '
              f'{network.hub_address} (netmail #{nm_id}).', 'success')
    else:
        flash(f'Marked {n_local} local file areas subscribed, but could not '
              f'queue FileFix request (no hub address or no password '
              f'configured).', 'warning')
    return redirect(url_for('admin.file_areas_admin'))


@admin_bp.route('/file-areas/networks/<int:network_id>/unsubscribe_all',
                methods=['POST'])
@login_required
@admin_required
def file_areas_unsubscribe_all(network_id):
    """Mark every FileArea on this network as locally unsubscribed AND queue
    FileFix `-TAG1 -TAG2 ...` for each known tag."""
    from ..models import FileArea, EchomailNetwork
    network = EchomailNetwork.query.get_or_404(network_id)
    if network.network_type != 'binkp':
        flash('Unsubscribe-all only applies to BinkP networks.', 'warning')
        return redirect(url_for('admin.file_areas_admin'))
    tags = [a.tag for a in
            FileArea.query.filter_by(network_id=network.id).all()]
    n_local = (FileArea.query.filter_by(network_id=network.id)
               .update({FileArea.is_subscribed: False},
                       synchronize_session=False))
    db.session.commit()
    if not tags:
        flash('No local file areas to unsubscribe from.', 'info')
        return redirect(url_for('admin.file_areas_admin'))
    from ..echomail.areafix import send_areafix_request
    nm_id = send_areafix_request(network, minus_tags=tags,
                                  robot_name='FileFix',
                                  subject='FileFix request')
    flash(f'Marked {n_local} local file areas unsubscribed; queued FileFix '
          f'request with {len(tags)} -TAG entries (netmail #{nm_id or "n/a"}).',
          'success')
    return redirect(url_for('admin.file_areas_admin'))


@admin_bp.route('/file-areas/bulk_import', methods=['GET', 'POST'])
@login_required
@admin_required
def file_areas_bulk_import():
    """Bulk-create FileArea rows from a FILEBONE-style backbone file or
    pasted echolist.

    Accepts both:
        TAG  Display Name
        TAG  - Display Name        (FidoNet backbone style)

    Lines starting with ; or # are skipped. Lines whose first token isn't
    a plausible area tag (alnum + ._-) are also skipped.
    """
    from ..models import FileArea, EchomailNetwork
    from ..web.echomail_admin import _parse_backbone

    networks = EchomailNetwork.query.filter_by(is_active=True).all()

    if request.method == 'POST':
        net_id = request.form.get('network_id', type=int) or None
        text = request.form.get('echolist') or ''
        upload = request.files.get('backbone_file')
        is_subscribed = bool(request.form.get('is_subscribed'))
        is_active = True
        if upload and upload.filename:
            raw = upload.read()
            try:
                text = (text + '\n' + raw.decode('utf-8')).strip()
            except UnicodeDecodeError:
                text = (text + '\n' + raw.decode('cp437', errors='replace')).strip()
        if not text.strip():
            flash('Paste an echolist or upload a backbone file.', 'warning')
            return redirect(url_for('admin.file_areas_bulk_import'))

        entries = _parse_backbone(text)
        imported = 0
        skipped = 0
        for tag, name in entries:
            if FileArea.query.filter_by(tag=tag).first():
                skipped += 1
                continue
            fa = FileArea(
                tag=tag,
                name=name,
                description=None,
                network_id=net_id,
                is_active=is_active,
                is_subscribed=is_subscribed,
                upload_permission='users',
            )
            db.session.add(fa)
            imported += 1
        db.session.commit()
        flash(f'Imported {imported} file areas, skipped {skipped} duplicates.',
              'success')
        return redirect(url_for('admin.file_areas_admin'))

    return render_template('admin/file_areas_bulk_import.html',
                           networks=networks)


@admin_bp.route('/checklist')
@login_required
@admin_required
def checklist():
    """First-launch sysop sanity checklist — what's configured vs. not.

    Each item is (label, ok, hint) — the template renders green/red
    checkmarks and the hint tells the sysop how to satisfy the check.
    Every check is wrapped so a stale schema can't 500 the page.
    """
    try:
        return _checklist_impl()
    except Exception as exc:
        import traceback as _tb
        try:
            db.session.rollback()
        except Exception:
            pass
        flash(f'Checklist error: {exc}', 'danger')
        return render_template('admin/setup_wizard_error.html',
                               error=str(exc),
                               traceback=_tb.format_exc())


def _checklist_impl():
    items = []

    def _safe(fn, default=False):
        try:
            return fn()
        except Exception:
            db.session.rollback()
            return default

    # 1. Admin password changed (no longer the seeded admin123).
    def _pw_check():
        seeded_admin = User.query.filter_by(username='admin').first()
        if seeded_admin and seeded_admin.check_password('admin123'):
            return False
        return True
    items.append((
        "Default admin password changed",
        _safe(_pw_check, True),
        'Edit the admin user (Admin → Users) and set a strong password.'))

    # 2. At least one non-default board exists.
    def _custom_board():
        default_names = {'General Discussion', 'Announcements',
                         'Technical Support', 'Off-Topic'}
        return Board.query.filter(
            ~Board.name.in_(default_names)).first() is not None
    items.append((
        "At least one custom board",
        _safe(_custom_board, False),
        'Admin → Boards → Add Board, name something specific to your community.'))

    # 3. At least one bulletin posted.
    items.append((
        "At least one bulletin posted",
        _safe(lambda: Message.query.first() is not None, False),
        'Admin → Bulletins → Add Bulletin to welcome new users.'))

    # 4. BBS_NAME is not the default.
    bbs_name = current_app.config.get('BBS_NAME', '')
    items.append((
        "BBS_NAME customized",
        bool(bbs_name) and bbs_name.lower() not in ('ANetBBS',
                                                    'my bbs', 'bbs'),
        'Edit .env and set BBS_NAME / BBS_DESCRIPTION.'))

    # 5. SECRET_KEY isn't the dev default.
    secret = current_app.config.get('SECRET_KEY', '')
    items.append((
        "SECRET_KEY set (production-ready)",
        bool(secret) and secret not in ('dev-secret', 'change-me',
                                        'secret', 'changeme'),
        'install.sh generates one. If you bypassed it, set SECRET_KEY in .env.'))

    # 6. At least one MOTD entry seeded.
    motd_count = 0
    try:
        from ..models import MotdEntry
        motd_count = MotdEntry.query.count()
    except Exception:
        db.session.rollback()
    items.append((
        "MOTD pool populated",
        motd_count > 0,
        'Admin → MOTD entries (or seed via DB).'))

    # 7. At least one theme is marked default.
    items.append((
        "Default theme picked",
        _safe(lambda: Theme.query.filter_by(is_default=True).first()
              is not None, False),
        'Admin → Themes; mark one as default.'))

    # 8. At least one file area exists.
    fa_count = 0
    try:
        from ..models import FileArea
        fa_count = FileArea.query.count()
    except Exception:
        db.session.rollback()
    items.append((
        "At least one file area",
        fa_count > 0,
        'Admin → File Areas → Add Area.'))

    completed = sum(1 for _, ok, _ in items if ok)
    return render_template('admin/checklist.html',
                           items=items,
                           completed=completed,
                           total=len(items))


@admin_bp.route('/bulletins/purge-expired', methods=['POST'])
@login_required
@admin_required
def purge_expired_bulletins():
    """Permanently delete expired bulletins."""
    now = datetime.utcnow()
    rows = Message.query.filter(Message.expires_at.isnot(None),
                                Message.expires_at < now).all()
    n = len(rows)
    for r in rows:
        db.session.delete(r)
    db.session.commit()
    flash(f'Purged {n} expired bulletin(s).', 'success')
    return redirect(url_for('admin.bulletins'))


@admin_bp.route('/ip-bans', methods=['GET', 'POST'])
@login_required
@admin_required
def ip_bans():
    from ..models import IpBan
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            cidr = (request.form.get('cidr') or '').strip()
            reason = (request.form.get('reason') or '').strip() or None
            ttl_days = request.form.get('ttl_days', type=int) or 0
            if not cidr:
                flash('CIDR required.', 'danger')
            elif IpBan.query.filter_by(cidr=cidr).first():
                flash('Already banned.', 'info')
            else:
                expires = (datetime.utcnow() + timedelta(days=ttl_days)) if ttl_days else None
                db.session.add(IpBan(cidr=cidr, reason=reason,
                                     banned_by_id=current_user.id,
                                     expires_at=expires))
                db.session.commit()
                flash(f'Banned {cidr}.', 'success')
        elif action == 'remove':
            bid = request.form.get('ban_id', type=int)
            row = IpBan.query.get(bid)
            if row:
                db.session.delete(row); db.session.commit()
                flash('Ban removed.', 'success')
        return redirect(url_for('admin.ip_bans'))
    rows = IpBan.query.order_by(IpBan.created_at.desc()).all()
    return render_template('admin/ip_bans.html', rows=rows)


@admin_bp.route('/users/<int:user_id>/manage', methods=['GET', 'POST'])
@login_required
@admin_required
def manage_user(user_id):
    """Detailed sysop user editor — access level, time budget, ratio,
    verification flag, lock state, plus private notes."""
    from ..models import (UserTimeBudget, FileRatio, UserNote)
    user = User.query.get_or_404(user_id)
    budget = UserTimeBudget.query.filter_by(user_id=user.id).first()
    ratio = FileRatio.query.filter_by(user_id=user.id).first()
    notes = (UserNote.query.filter_by(user_id=user.id)
             .order_by(UserNote.created_at.desc()).all())

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'save':
            user.email = (request.form.get('email') or user.email)[:120]
            user.display_name = (request.form.get('display_name') or '')[:100] or None
            try:
                user.access_level = int(request.form.get('access_level') or 10)
            except ValueError:
                pass
            user.is_admin = bool(request.form.get('is_admin'))
            user.is_active = bool(request.form.get('is_active'))
            user.is_locked = bool(request.form.get('is_locked'))
            user.is_verified = bool(request.form.get('is_verified'))
            # Time budget upsert
            try:
                tlim = int(request.form.get('time_limit_min') or 0)
                dlim = int(request.form.get('daily_limit_min') or 0)
                bank = int(request.form.get('bank_minutes') or 0)
                if budget is None:
                    budget = UserTimeBudget(user_id=user.id)
                    db.session.add(budget)
                budget.time_limit_min = tlim
                budget.daily_limit_min = dlim
                budget.bank_minutes = bank
            except ValueError:
                pass
            db.session.commit()
            flash(f'Saved {user.username}.', 'success')
        elif action == 'note':
            txt = (request.form.get('note') or '').strip()
            if txt:
                db.session.add(UserNote(
                    user_id=user.id,
                    author_id=current_user.id,
                    note=txt[:2000]))
                db.session.commit()
                flash('Note added.', 'success')
        elif action == 'note_delete':
            nid = request.form.get('note_id', type=int)
            n = UserNote.query.get(nid)
            if n and n.user_id == user.id:
                db.session.delete(n); db.session.commit()
                flash('Note deleted.', 'success')
        elif action == 'reset_password':
            new = (request.form.get('new_password') or '').strip()
            if len(new) >= 6:
                user.set_password(new)
                db.session.commit()
                flash(f'Password reset for {user.username}.', 'success')
            else:
                flash('Password too short.', 'danger')
        return redirect(url_for('admin.manage_user', user_id=user.id))

    return render_template('admin/edit_user.html',
                           user=user, budget=budget, ratio=ratio, notes=notes)


@admin_bp.route('/pending-users')
@login_required
@admin_required
def pending_users():
    """List users awaiting NUV sysop approval."""
    rows = (User.query.filter_by(is_verified=False)
            .order_by(User.created_at.desc()).all())
    return render_template('admin/pending_users.html', rows=rows)


@admin_bp.route('/pending-users/<int:user_id>/<action>', methods=['POST'])
@login_required
@admin_required
def pending_user_action(user_id, action):
    user = User.query.get_or_404(user_id)
    if action == 'approve':
        user.is_verified = True
        db.session.commit()
        # Welcome PM
        try:
            from ..models import PrivateMessage
            sysop = current_user
            db.session.add(PrivateMessage(
                sender_id=sysop.id,
                recipient_id=user.id,
                subject='Welcome — your account has been approved',
                content='Your account has been approved. Welcome aboard!'))
            db.session.commit()
        except Exception:
            db.session.rollback()
        flash(f'Approved {user.username}.', 'success')
    elif action == 'reject':
        # Soft-reject: deactivate but keep the row for audit.
        user.is_active = False
        db.session.commit()
        flash(f'Rejected {user.username}.', 'warning')
    elif action == 'delete':
        db.session.delete(user); db.session.commit()
        flash(f'Deleted {user.username}.', 'info')
    return redirect(url_for('admin.pending_users'))


@admin_bp.route('/inactive-users', methods=['GET', 'POST'])
@login_required
@admin_required
def inactive_users():
    """List users that haven't logged in for N days. Sysop can mass-PM
    them, soft-deactivate, or hard-delete (with confirmation)."""
    days = request.args.get('days', 90, type=int)
    if days < 1:
        days = 90
    cutoff = datetime.utcnow() - timedelta(days=days)
    rows = (User.query.filter(
                db.or_(User.last_login.is_(None),
                       User.last_login < cutoff))
            .filter(User.is_admin.is_(False))
            .filter(User.is_active.is_(True))
            .order_by(User.last_login.asc().nulls_first()).all())

    if request.method == 'POST':
        action = request.form.get('action')
        ids = request.form.getlist('user_id', type=int)
        n = 0
        if action == 'pm':
            from ..models import PrivateMessage
            body = (request.form.get('body') or '').strip()
            subject = (request.form.get('subject') or 'We miss you!').strip()
            if not body:
                flash('Body required.', 'danger')
                return redirect(url_for('admin.inactive_users', days=days))
            for uid in ids:
                db.session.add(PrivateMessage(
                    sender_id=current_user.id,
                    recipient_id=uid,
                    subject=subject[:200],
                    content=body))
                n += 1
            db.session.commit()
            flash(f'Sent {n} PM(s).', 'success')
        elif action == 'deactivate':
            for uid in ids:
                u = User.query.get(uid)
                if u and not u.is_admin:
                    u.is_active = False; n += 1
            db.session.commit()
            flash(f'Deactivated {n} user(s).', 'warning')
        elif action == 'delete':
            for uid in ids:
                u = User.query.get(uid)
                if u and not u.is_admin:
                    db.session.delete(u); n += 1
            db.session.commit()
            flash(f'Deleted {n} user(s).', 'info')
        return redirect(url_for('admin.inactive_users', days=days))

    return render_template('admin/inactive_users.html', rows=rows, days=days)


@admin_bp.route('/newsletter', methods=['GET', 'POST'])
@login_required
@admin_required
def newsletter():
    """Compose + send a newsletter as PM to every active verified user."""
    from ..models import Newsletter, PrivateMessage
    if request.method == 'POST':
        subject = (request.form.get('subject') or '').strip()
        body = (request.form.get('body') or '').strip()
        if not subject or not body:
            flash('Subject and body required.', 'danger')
            return redirect(url_for('admin.newsletter'))
        nl = Newsletter(subject=subject[:200], body=body,
                        sent_by_id=current_user.id)
        db.session.add(nl); db.session.flush()
        recipients = (User.query.filter_by(is_active=True)
                      .filter(db.or_(User.is_verified.is_(True),
                                     User.is_verified.is_(None)))
                      .all())
        n = 0
        for u in recipients:
            db.session.add(PrivateMessage(
                sender_id=current_user.id,
                recipient_id=u.id,
                subject=f'[Newsletter] {subject}'[:200],
                body=body))
            n += 1
        from datetime import datetime as _dt
        nl.sent_at = _dt.utcnow()
        nl.recipients_count = n
        db.session.commit()
        flash(f'Newsletter sent to {n} user(s).', 'success')
        return redirect(url_for('admin.newsletter'))
    history = (Newsletter.query.order_by(Newsletter.created_at.desc())
               .limit(50).all())
    return render_template('admin/newsletter.html', history=history)


@admin_bp.route('/users/<int:user_id>/lock', methods=['POST'])
@login_required
@admin_required
def lock_user(user_id):
    user = User.query.get_or_404(user_id)
    user.is_locked = not bool(user.is_locked)
    db.session.commit()
    flash('User ' + ('locked.' if user.is_locked else 'unlocked.'), 'success')
    return redirect(url_for('admin.users'))


@admin_bp.route('/caller-log')
@login_required
@admin_required
def caller_log():
    """Pageable caller log with filters."""
    from ..models import CallerLog
    page = request.args.get('page', 1, type=int)
    user = (request.args.get('user') or '').strip()
    ip = (request.args.get('ip') or '').strip()
    service = (request.args.get('service') or '').strip()
    date_from = (request.args.get('date_from') or '').strip()
    date_to = (request.args.get('date_to') or '').strip()

    q = CallerLog.query
    if user:
        q = q.filter(CallerLog.username.ilike(f'%{user}%'))
    if ip:
        q = q.filter(CallerLog.ip_address.ilike(f'%{ip}%'))
    if service:
        q = q.filter(CallerLog.service == service)
    try:
        if date_from:
            q = q.filter(CallerLog.started_at >= datetime.strptime(date_from, '%Y-%m-%d'))
        if date_to:
            q = q.filter(CallerLog.started_at < datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1))
    except ValueError:
        pass
    pagination = q.order_by(CallerLog.started_at.desc()).paginate(
        page=page, per_page=50, error_out=False)
    return render_template('admin/caller_log.html',
                           pagination=pagination,
                           user=user, ip=ip, service=service,
                           date_from=date_from, date_to=date_to)


@admin_bp.route('/broadcast', methods=['GET', 'POST'])
@login_required
@admin_required
def broadcast():
    """Sysop one-liner broadcast — pushed live to all socketio-connected users."""
    from ..models import SysopBroadcast
    from datetime import timedelta as _td

    if request.method == 'POST':
        text = (request.form.get('text') or '').strip()
        ttl = request.form.get('ttl_minutes', type=int) or 0
        if not text:
            flash('Empty broadcast.', 'warning')
            return redirect(url_for('admin.broadcast'))
        expires = (datetime.utcnow() + _td(minutes=ttl)) if ttl else None
        bcast = SysopBroadcast(sender_id=current_user.id, text=text,
                               expires_at=expires)
        db.session.add(bcast)
        db.session.commit()
        # Push live via socketio so web users see it immediately.
        try:
            from ..web_app import socketio
            socketio.emit('sysop_broadcast', {
                'id': bcast.id,
                'sender': current_user.username,
                'text': text,
                'when': bcast.created_at.isoformat() + 'Z',
            }, namespace='/')
        except Exception:
            pass
        flash('Broadcast sent.', 'success')
        return redirect(url_for('admin.broadcast'))

    recent = (SysopBroadcast.query
              .order_by(SysopBroadcast.created_at.desc())
              .limit(20).all())
    return render_template('admin/broadcast.html', recent=recent)


@admin_bp.route('/pages')
@login_required
@admin_required
def pages():
    """List sysop pages from telnet/SSH/rlogin users."""
    from ..models import SysopPage
    rows = (SysopPage.query
            .order_by(SysopPage.created_at.desc())
            .limit(200).all())
    return render_template('admin/pages.html', rows=rows)


@admin_bp.route('/pages/<int:page_id>/answer', methods=['POST'])
@login_required
@admin_required
def answer_page(page_id):
    from ..models import SysopPage
    p = SysopPage.query.get_or_404(page_id)
    p.answered = True
    p.answered_at = datetime.utcnow()
    db.session.commit()
    flash(f'Marked page #{page_id} as answered.', 'success')
    return redirect(url_for('admin.pages'))


@admin_bp.route('/pages/<int:page_id>/reply', methods=['POST'])
@login_required
@admin_required
def reply_page(page_id):
    """Send a one-line reply that will pop up in the paging user's
    telnet/SSH/rlogin session on their next menu loop."""
    from ..models import SysopPage
    from ..features.sysop_paging import push_message
    p = SysopPage.query.get_or_404(page_id)
    text = (request.form.get('reply') or '').strip()
    if not text:
        flash('Empty reply.', 'warning')
        return redirect(url_for('admin.pages'))
    push_message(p.user_id, current_user.username, text)
    p.answered = True
    p.answered_at = datetime.utcnow()
    db.session.commit()
    flash(f'Reply queued for {p.user.username if p.user else "user"}.', 'success')
    return redirect(url_for('admin.pages'))


@admin_bp.route('/file-echo-subs', methods=['GET', 'POST'])
@login_required
@admin_required
def file_echo_subs():
    """Manage FileEchoSubscription rows — which peers receive each file area."""
    from ..models import FileEchoSubscription, FileArea
    from ..echomail.routing import parse_address

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            file_area_id = request.form.get('file_area_id', type=int)
            peer = (request.form.get('peer_address') or '').strip()
            if not file_area_id or not parse_address(peer):
                flash('Provide a file area and a valid FTN peer address.', 'danger')
                return redirect(url_for('admin.file_echo_subs'))
            existing = FileEchoSubscription.query.filter_by(
                file_area_id=file_area_id, peer_address=peer).first()
            if existing:
                flash('Subscription already exists.', 'warning')
            else:
                db.session.add(FileEchoSubscription(
                    file_area_id=file_area_id, peer_address=peer,
                    is_active=True))
                db.session.commit()
                flash(f'Subscription added: {peer} → file area #{file_area_id}.',
                      'success')
        elif action == 'toggle':
            sub_id = request.form.get('sub_id', type=int)
            sub = FileEchoSubscription.query.get_or_404(sub_id)
            sub.is_active = not bool(sub.is_active)
            db.session.commit()
            flash('Subscription toggled.', 'success')
        elif action == 'delete':
            sub_id = request.form.get('sub_id', type=int)
            sub = FileEchoSubscription.query.get_or_404(sub_id)
            db.session.delete(sub)
            db.session.commit()
            flash('Subscription deleted.', 'success')
        return redirect(url_for('admin.file_echo_subs'))

    subs = (FileEchoSubscription.query
            .join(FileArea, FileArea.id == FileEchoSubscription.file_area_id)
            .order_by(FileArea.tag, FileEchoSubscription.peer_address)
            .all())
    areas = FileArea.query.filter_by(is_active=True).order_by(FileArea.tag).all()
    return render_template('admin/file_echo_subs.html', subs=subs, areas=areas)


@admin_bp.route('/users/<int:user_id>/notes', methods=['GET', 'POST'])
@login_required
@admin_required
def user_notes(user_id):
    """Sysop-only notes attached to a user account."""
    from ..models import UserNote
    target = User.query.get_or_404(user_id)
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            note = (request.form.get('note') or '').strip()
            if note:
                db.session.add(UserNote(
                    user_id=target.id, author_id=current_user.id, note=note))
                db.session.commit()
                flash('Note added.', 'success')
        elif action == 'delete':
            note_id = request.form.get('note_id', type=int)
            n = UserNote.query.get_or_404(note_id)
            if n.user_id != target.id:
                abort(400)
            db.session.delete(n)
            db.session.commit()
            flash('Note deleted.', 'success')
        return redirect(url_for('admin.user_notes', user_id=user_id))

    notes = (UserNote.query.filter_by(user_id=target.id)
             .order_by(UserNote.created_at.desc()).all())
    return render_template('admin/user_notes.html', target=target, notes=notes)


@admin_bp.route('/irc-presets', methods=['GET', 'POST'])
@login_required
@admin_required
def irc_presets():
    """CRUD for sysop-configured IRC server presets (shown in terminal IRC menu)."""
    from ..models import IrcPreset

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            p = IrcPreset(
                name=(request.form.get('name') or '').strip(),
                server=(request.form.get('server') or '').strip(),
                port=request.form.get('port', type=int) or 6667,
                use_ssl=bool(request.form.get('use_ssl')),
                default_nick=(request.form.get('default_nick') or '').strip() or None,
                channels=(request.form.get('channels') or '').strip() or None,
                is_active=bool(request.form.get('is_active')),
                order=request.form.get('order', type=int) or 0,
            )
            if not p.name or not p.server:
                flash('Name and server are required.', 'danger')
            else:
                db.session.add(p)
                db.session.commit()
                flash(f'Preset "{p.name}" created.', 'success')
        elif action == 'edit':
            p = IrcPreset.query.get_or_404(request.form.get('preset_id', type=int))
            p.name = (request.form.get('name') or p.name).strip()
            p.server = (request.form.get('server') or p.server).strip()
            p.port = request.form.get('port', type=int) or p.port
            p.use_ssl = bool(request.form.get('use_ssl'))
            p.default_nick = (request.form.get('default_nick') or '').strip() or None
            p.channels = (request.form.get('channels') or '').strip() or None
            p.is_active = bool(request.form.get('is_active'))
            p.order = request.form.get('order', type=int) or 0
            db.session.commit()
            flash(f'Preset "{p.name}" updated.', 'success')
        elif action == 'toggle':
            p = IrcPreset.query.get_or_404(request.form.get('preset_id', type=int))
            p.is_active = not p.is_active
            db.session.commit()
            flash('Toggled.', 'success')
        elif action == 'delete':
            p = IrcPreset.query.get_or_404(request.form.get('preset_id', type=int))
            db.session.delete(p)
            db.session.commit()
            flash('Deleted.', 'success')
        return redirect(url_for('admin.irc_presets'))

    presets = IrcPreset.query.order_by(IrcPreset.order, IrcPreset.name).all()
    return render_template('admin/irc_presets.html', presets=presets)


@admin_bp.route('/connection-test', methods=['GET', 'POST'])
@login_required
@admin_required
def connection_test():
    """Quick connectivity probe: test telnet/SSH/web/finger/IRC ports against
    a remote host. Useful for diagnosing 'is bbs.a-net.online up?' from the
    sysop dashboard."""
    import socket as _socket
    DEFAULT_HOST = 'bbs.a-net.online'
    DEFAULT_PROBES = [
        ('Web HTTP',  80,    'GET / HTTP/1.0\r\nHost: {host}\r\n\r\n'),
        ('Web HTTPS', 443,   None),  # SSL handshake only
        ('Telnet',    2233,  None),
        ('Telnet',    23,    None),
        ('SSH',       2234,  None),
        ('SSH',       22,    None),
        ('Rlogin',    513,   None),
        ('Finger',    79,    '\r\n'),
        ('IRC',       6667,  None),
        ('BinkP',     24554, None),
    ]

    host = DEFAULT_HOST
    results = []
    if request.method == 'POST':
        host = (request.form.get('host') or DEFAULT_HOST).strip()
        ports_arg = (request.form.get('ports') or '').strip()
        probes = []
        if ports_arg:
            # Custom port list, comma-sep with optional name: "Telnet:2233,IRC:6667"
            for spec in ports_arg.split(','):
                spec = spec.strip()
                if not spec:
                    continue
                if ':' in spec:
                    nm, _, p = spec.partition(':')
                    try:
                        probes.append((nm.strip() or 'TCP', int(p.strip()), None))
                    except ValueError:
                        continue
                else:
                    try:
                        probes.append(('TCP', int(spec), None))
                    except ValueError:
                        continue
        else:
            probes = DEFAULT_PROBES

        for label, port, banner in probes:
            entry = {'label': label, 'port': port, 'ok': False,
                     'ms': None, 'note': ''}
            try:
                t0 = datetime.utcnow()
                sock = _socket.create_connection((host, port), timeout=5)
                t1 = datetime.utcnow()
                entry['ms'] = int((t1 - t0).total_seconds() * 1000)
                # Optional banner read
                if banner:
                    try:
                        sock.sendall(banner.format(host=host).encode())
                    except OSError:
                        pass
                try:
                    sock.settimeout(2)
                    data = sock.recv(256)
                    if data:
                        entry['note'] = data[:120].decode('utf-8', errors='replace').replace('\r', '\\r').replace('\n', '\\n')
                except OSError:
                    pass
                sock.close()
                entry['ok'] = True
            except (OSError, _socket.gaierror) as exc:
                entry['note'] = str(exc)
            results.append(entry)

    return render_template('admin/connection_test.html',
                           host=host, results=results)


@admin_bp.route('/virus-scan', methods=['GET', 'POST'])
@login_required
@admin_required
def virus_scan_admin():
    """Run a bulk virus scan across all FileArea storage paths.

    Background task to avoid blocking the request — results stream into a
    log table the sysop can refresh."""
    from ..models import FileArea
    from ..features.virus_scan import scan_path
    import os as _os

    results = None
    if request.method == 'POST':
        results = []
        for area in FileArea.query.filter_by(is_active=True).all():
            if not area.storage_path or not _os.path.isdir(area.storage_path):
                continue
            for fname in _os.listdir(area.storage_path):
                if fname.startswith('.'):
                    continue
                full = _os.path.join(area.storage_path, fname)
                if not _os.path.isfile(full):
                    continue
                r = scan_path(full)
                results.append({
                    'area': area.tag,
                    'file': fname,
                    'infected': r.infected,
                    'sig': r.signature,
                    'msg': r.message,
                })
        # Quarantine: rename infected files with a .infected suffix so they
        # can't be downloaded but the sysop can still inspect them.
        quarantined = 0
        for r in results:
            if not r['infected']:
                continue
            try:
                area_row = FileArea.query.filter_by(tag=r['area']).first()
                if not area_row:
                    continue
                src = _os.path.join(area_row.storage_path, r['file'])
                dst = src + '.infected'
                if _os.path.isfile(src):
                    _os.rename(src, dst)
                    quarantined += 1
            except OSError:
                pass
        if quarantined:
            flash(f'Quarantined {quarantined} infected file(s).', 'warning')
        else:
            flash(f'Scanned {len(results)} files — no infections.', 'success')

    return render_template('admin/virus_scan.html', results=results)


@admin_bp.route('/dialout', methods=['GET', 'POST'])
@login_required
@admin_required
def dialout_admin():
    """CRUD for the dial-out BBS directory."""
    from ..models import DialoutDestination

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            d = DialoutDestination(
                name=(request.form.get('name') or '').strip(),
                hostname=(request.form.get('hostname') or '').strip(),
                port=request.form.get('port', type=int) or 23,
                protocol=(request.form.get('protocol') or 'telnet').strip(),
                description=(request.form.get('description') or '').strip() or None,
                sort_order=request.form.get('sort_order', type=int) or 100,
                is_active=bool(request.form.get('is_active')),
            )
            if not d.name or not d.hostname:
                flash('Name + hostname required.', 'danger')
            else:
                db.session.add(d); db.session.commit()
                flash(f'Added {d.name}.', 'success')
        elif action == 'toggle':
            d = DialoutDestination.query.get_or_404(
                request.form.get('dest_id', type=int))
            d.is_active = not bool(d.is_active); db.session.commit()
        elif action == 'delete':
            d = DialoutDestination.query.get_or_404(
                request.form.get('dest_id', type=int))
            db.session.delete(d); db.session.commit()
            flash('Deleted.', 'success')
        return redirect(url_for('admin.dialout_admin'))

    rows = (DialoutDestination.query
            .order_by(DialoutDestination.sort_order, DialoutDestination.name)
            .all())
    return render_template('admin/dialout.html', rows=rows)


@admin_bp.route('/motd', methods=['GET', 'POST'])
@login_required
@admin_required
def motd_admin():
    """Manage the random MOTD pool."""
    from ..models import MotdEntry
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            text = (request.form.get('text') or '').strip()
            weight = request.form.get('weight', type=int) or 1
            if text:
                db.session.add(MotdEntry(text=text, weight=weight, is_active=True))
                db.session.commit()
                flash('MOTD added.', 'success')
        elif action == 'toggle':
            m = MotdEntry.query.get_or_404(request.form.get('motd_id', type=int))
            m.is_active = not bool(m.is_active); db.session.commit()
        elif action == 'delete':
            m = MotdEntry.query.get_or_404(request.form.get('motd_id', type=int))
            db.session.delete(m); db.session.commit()
        return redirect(url_for('admin.motd_admin'))
    motds = MotdEntry.query.order_by(MotdEntry.created_at.desc()).all()
    return render_template('admin/motd.html', motds=motds)


@admin_bp.route('/callers')
@login_required
@admin_required
def callers_legacy():
    """Legacy alias — redirects to the filterable caller log."""
    return redirect(url_for('admin.caller_log'))


@admin_bp.route('/setup-wizard/check-hub', methods=['POST'])
@login_required
@admin_required
def setup_wizard_check_hub():
    """Probe the configured federation hub before the wizard submits.

    Lets the sysop see "yes, my outbound HTTPS to the hub works" /
    "no, your firewall is blocking it" before committing to
    registration. Pure read-only — does NOT send a register POST.
    """
    from flask import jsonify
    import requests
    hub = (current_app.config.get('REGISTRY_URL') or 'https://bbs.a-net.fyi').rstrip('/')
    out = {'hub': hub, 'ok': False, 'detail': ''}
    try:
        r = requests.get(hub + '/anetbbs.lst', timeout=8,
                         headers={'User-Agent': 'ANetBBS/setup-wizard'})
        out['ok'] = (r.status_code == 200 and 'ANetBBS' in r.text[:512])
        out['detail'] = (f'HTTP {r.status_code}, {len(r.text)} bytes'
                         + ('' if out['ok'] else ' — body did not contain "ANetBBS"'))
    except Exception as exc:  # noqa: BLE001
        out['detail'] = f'{exc.__class__.__name__}: {exc}'
    return jsonify(out)


@admin_bp.route('/setup-wizard', methods=['GET', 'POST'])
@login_required
@admin_required
def setup_wizard():
    """First-run setup wizard — collects BBS name, sysop name, default theme,
    seed MOTDs, default echo subscriptions. Idempotent — safe to re-run.

    Outer try/except renders an error report instead of 500 so the sysop can
    see what went wrong on production."""
    try:
        return _setup_wizard_impl()
    except Exception as exc:
        import traceback as _tb
        try:
            db.session.rollback()
        except Exception:
            pass
        flash(f'Setup wizard error: {exc}', 'danger')
        return render_template('admin/setup_wizard_error.html',
                               error=str(exc),
                               traceback=_tb.format_exc())


def _setup_wizard_impl():
    from ..models import (MotdEntry, EchomailNetwork, EchoArea, BbsMenu,
                          DialoutDestination)
    # Ensure any tables added in newer versions exist before we query them.
    try:
        db.create_all()
    except Exception:
        db.session.rollback()
    if request.method == 'POST':
        # Persist a few core .env / DB settings.
        bbs_name = (request.form.get('bbs_name') or '').strip()
        sysop_name = (request.form.get('sysop_name') or '').strip()
        sysop_email = (request.form.get('sysop_email') or '').strip()
        bbs_location = (request.form.get('bbs_location') or '').strip()
        federation_register = request.form.get('federation_register') == 'on'
        seed_motds = request.form.getlist('motd')
        seed_dial = request.form.get('seed_dial') == 'on'

        # Federation self-registration. The plumbing already exists for
        # daily heartbeats; this just persists the sysop's metadata to
        # .env and triggers an immediate registration tick. Failures
        # are non-fatal — the daily thread retries.
        if federation_register and sysop_email and sysop_name:
            env_updates = {
                'SYSOP_NAME': sysop_name,
                'SYSOP_EMAIL': sysop_email,
                'BBS_LOCATION': bbs_location,
                'REGISTRY_SELF_REGISTER': 'true',
            }
            try:
                _write_env_keys(_env_path(), env_updates)
                # Also push into the live config so the tick below uses
                # the new values without needing a restart.
                for k, v in env_updates.items():
                    current_app.config[k] = (v == 'true') if k == 'REGISTRY_SELF_REGISTER' else v
            except Exception as exc:
                flash(f'Federation .env update failed: {exc}', 'warning')
            try:
                from ..msp.registry_client import _tick as _registry_tick
                _registry_tick(current_app._get_current_object())
                flash(f'Federation registration sent — check {sysop_email} '
                      f'for the verification email from the hub.', 'success')
            except Exception as exc:
                flash(f'Saved sysop details, but the immediate registration '
                      f'tick failed: {exc}. The daily heartbeat will retry.',
                      'info')

        # Each section is independent — one failure shouldn't abort the others.
        if seed_motds:
            try:
                for text in seed_motds:
                    t = (text or '').strip()
                    if t and not MotdEntry.query.filter_by(text=t).first():
                        db.session.add(MotdEntry(text=t, weight=1, is_active=True))
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                flash(f'MOTD seeding skipped: {exc}', 'warning')

        if seed_dial:
            try:
                from ..features.dialout import DEFAULT_DIRECTORY
                for n, h, p, proto in DEFAULT_DIRECTORY:
                    if not DialoutDestination.query.filter_by(hostname=h).first():
                        db.session.add(DialoutDestination(
                            name=n, hostname=h, port=p, protocol=proto,
                            is_active=True))
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                flash(f'Dial-out seeding skipped: {exc}', 'warning')

        if request.form.get('seed_ansi') == 'on':
            try:
                from ..models import AnsiArt as _AA
                from ..web.ansi_editor import render_ansi_text as _render
                for slug, name, grid in _build_ansi_samples():
                    if _AA.query.filter_by(slug=slug).first():
                        continue
                    db.session.add(_AA(
                        name=name, slug=slug,
                        width=grid['width'], height=grid['height'],
                        grid_json=json.dumps(grid),
                        ansi_text=_render(grid),
                        description='Seeded sample',
                        created_by_id=current_user.id))
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                flash(f'ANSI sample seeding skipped: {exc}', 'warning')

        flash('Setup wizard complete — keep configuring from the admin menu.',
              'success')
        return redirect(url_for('admin.dashboard'))

    # GET path: render the wizard with progress indicators.
    def _safe_count(q):
        try:
            return q()
        except Exception:
            db.session.rollback()
            return 0
    progress = {
        'has_admin':   _safe_count(lambda: User.query.filter_by(is_admin=True).count()) > 0,
        'has_motd':    _safe_count(lambda: MotdEntry.query.count()) > 0,
        'has_network': _safe_count(lambda: EchomailNetwork.query.filter_by(is_active=True).count()) > 0,
        'has_areas':   _safe_count(lambda: EchoArea.query.count()) > 0,
        'has_menus':   _safe_count(lambda: BbsMenu.query.count()) > 0,
        'has_dial':    _safe_count(lambda: DialoutDestination.query.count()) > 0,
    }
    return render_template('admin/setup_wizard.html', progress=progress)


def _build_ansi_samples():
    """Return [(slug, name, grid_dict), ...] sample art pieces."""
    # Defaults: light-grey-on-black canvas (palette 15 fg / 1 bg).
    def _empty(W=80, H=10):
        return {'width': W, 'height': H,
                'cells': [{'c': ' ', 'fg': 15, 'bg': 1} for _ in range(W * H)]}

    def _put(grid, row, col, char, fg=15, bg=1):
        if 0 <= row < grid['height'] and 0 <= col < grid['width']:
            grid['cells'][row * grid['width'] + col] = {
                'c': char, 'fg': fg, 'bg': bg}

    def _putstr(grid, row, col, s, fg=15, bg=1):
        for i, ch in enumerate(s):
            _put(grid, row, col + i, ch, fg, bg)

    samples = []

    # Welcome banner
    g1 = _empty(80, 8)
    for c in range(80):
        _put(g1, 0, c, '═', fg=11)
        _put(g1, 7, c, '═', fg=11)
    _put(g1, 0, 0, '╔', fg=11); _put(g1, 0, 79, '╗', fg=11)
    _put(g1, 7, 0, '╚', fg=11); _put(g1, 7, 79, '╝', fg=11)
    for r in range(1, 7):
        _put(g1, r, 0, '║', fg=11); _put(g1, r, 79, '║', fg=11)
    _putstr(g1, 2, 28, 'WELCOME TO ANetBBS', fg=8)
    _putstr(g1, 4, 24, 'A retro hangout, lovingly rebuilt', fg=15)
    _putstr(g1, 5, 30, '(press M for messages)', fg=14)
    samples.append(('sample-welcome', 'Sample · Welcome banner', g1))

    # Login splash
    g2 = _empty(80, 12)
    _putstr(g2, 1, 22, '─── ANetBBS · System Login ───', fg=10)
    _putstr(g2, 4, 8, 'Username: __________', fg=15)
    _putstr(g2, 6, 8, 'Password: __________', fg=15)
    _putstr(g2, 9, 12, 'New user? Press [N] to register.', fg=14)
    _putstr(g2, 10, 12, 'Forgot password? Press [F].', fg=14)
    samples.append(('sample-login', 'Sample · Login splash', g2))

    # Divider strip
    g3 = _empty(80, 1)
    for c in range(80):
        _put(g3, 0, c, '▄', fg=11)
    samples.append(('sample-divider', 'Sample · Divider strip', g3))

    # Goodbye
    g4 = _empty(80, 6)
    _putstr(g4, 1, 28, 'Thanks for calling — see ya!', fg=14)
    _putstr(g4, 3, 30, '(disconnecting in 3...)', fg=8)
    samples.append(('sample-goodbye', 'Sample · Goodbye screen', g4))

    return samples


@admin_bp.route('/newuser-questions', methods=['GET', 'POST'])
@login_required
@admin_required
def newuser_questions():
    """CRUD for newuser questionnaire prompts."""
    from ..models import NewUserQuestion
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            prompt = (request.form.get('prompt') or '').strip()
            if prompt:
                db.session.add(NewUserQuestion(
                    prompt=prompt[:280],
                    is_required=bool(request.form.get('is_required')),
                    sort_order=int(request.form.get('sort_order') or 0),
                    is_active=True))
                db.session.commit()
                flash('Question added.', 'success')
        elif action == 'toggle':
            qid = request.form.get('qid', type=int)
            q = NewUserQuestion.query.get(qid)
            if q:
                q.is_active = not bool(q.is_active)
                db.session.commit()
        elif action == 'delete':
            qid = request.form.get('qid', type=int)
            q = NewUserQuestion.query.get(qid)
            if q:
                db.session.delete(q); db.session.commit()
                flash('Question deleted.', 'success')
        return redirect(url_for('admin.newuser_questions'))
    rows = (NewUserQuestion.query
            .order_by(NewUserQuestion.sort_order,
                      NewUserQuestion.id).all())
    return render_template('admin/newuser_questions.html', rows=rows)


@admin_bp.route('/time-budgets', methods=['GET', 'POST'])
@login_required
@admin_required
def time_budgets():
    """Sysop UI to set per-user time limits / time-bank balances.

    GET shows all users with their current budgets.
    POST upserts a budget for a single user.
    """
    from ..models import UserTimeBudget
    if request.method == 'POST':
        user_id = request.form.get('user_id', type=int)
        u = User.query.get(user_id) if user_id else None
        if not u:
            flash('User not found.', 'danger')
            return redirect(url_for('admin.time_budgets'))
        b = UserTimeBudget.query.filter_by(user_id=u.id).first()
        if not b:
            b = UserTimeBudget(user_id=u.id)
            db.session.add(b)
        b.time_limit_min = int(request.form.get('time_limit_min') or 0)
        b.daily_limit_min = int(request.form.get('daily_limit_min') or 0)
        b.bank_minutes = int(request.form.get('bank_minutes') or 0)
        db.session.commit()
        flash(f'Updated time budget for {u.username}.', 'success')
        return redirect(url_for('admin.time_budgets'))

    rows = (db.session.query(User, UserTimeBudget)
            .outerjoin(UserTimeBudget, UserTimeBudget.user_id == User.id)
            .order_by(User.username).all())
    return render_template('admin/time_budgets.html', rows=rows)


@admin_bp.route('/db-backup')
@login_required
@admin_required
def db_backup():
    """Stream the SQLite DB file as a download. Only for sqlite installs."""
    from flask import send_file as _send_file, current_app as _ca
    uri = _ca.config.get('SQLALCHEMY_DATABASE_URI', '')
    if not uri.startswith('sqlite'):
        flash('DB backup only supports SQLite installs.', 'warning')
        return redirect(url_for('admin.dashboard'))
    path = uri.replace('sqlite:///', '').replace('sqlite:////', '/')
    if not path.startswith('/'):
        path = '/' + path
    if not os.path.isfile(path):
        flash(f'DB file not found: {path}', 'danger')
        return redirect(url_for('admin.dashboard'))
    return _send_file(path,
                      as_attachment=True,
                      download_name=f'anetbbs-backup-{datetime.utcnow().strftime("%Y%m%d-%H%M")}.db',
                      mimetype='application/octet-stream')


@admin_bp.route('/theme-builder', methods=['GET', 'POST'])
@login_required
@admin_required
def theme_builder():
    """In-browser theme designer — color pickers write to a Theme row."""
    from ..models import Theme as _Theme
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip().lower().replace(' ', '-')
        display_name = (request.form.get('display_name') or '').strip()
        css_vars = {
            # Write the full set base.html knows about. Theme Builder gives
            # us 6 inputs; we derive a couple of *-dark / *-muted variants
            # by darkening the primary/text values.
            '--theme-primary':       request.form.get('primary',     '#00ff00'),
            '--theme-primary-dark':  request.form.get('primary',     '#00ff00'),
            '--theme-bg':            request.form.get('bg',          '#000000'),
            '--theme-bg-dark':       request.form.get('bg_dark',     '#111111'),
            '--theme-text':          request.form.get('text',        '#cccccc'),
            '--theme-text-muted':    request.form.get('text',        '#cccccc'),
            '--theme-accent':        request.form.get('accent',      '#ffff00'),
            '--theme-link':          request.form.get('link',        '#00ffff'),
            '--theme-card-bg':       request.form.get('bg_dark',     '#111111'),
            '--theme-input-bg':      request.form.get('bg_dark',     '#111111'),
            '--theme-input-focus':   request.form.get('bg',          '#000000'),
            '--theme-border':        request.form.get('primary',     '#00ff00'),
        }
        if not name or not display_name:
            flash('Name + display name required.', 'danger')
            return redirect(url_for('admin.theme_builder'))
        existing = _Theme.query.filter_by(name=name).first()
        if existing:
            existing.display_name = display_name
            existing.css_variables = json.dumps(css_vars)
            existing.is_active = True
            flash(f'Theme "{name}" updated.', 'success')
        else:
            db.session.add(_Theme(name=name, display_name=display_name,
                                  css_variables=json.dumps(css_vars),
                                  is_active=True))
            flash(f'Theme "{name}" created.', 'success')
        db.session.commit()
        return redirect(url_for('admin.theme_builder'))
    themes = _Theme.query.order_by(_Theme.display_name).all()
    return render_template('admin/theme_builder.html', themes=themes)


@admin_bp.route('/webhooks', methods=['GET', 'POST'])
@login_required
@admin_required
def webhooks_admin():
    from ..models import Webhook
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            w = Webhook(
                name=(request.form.get('name') or '').strip(),
                url=(request.form.get('url') or '').strip(),
                event=(request.form.get('event') or 'shout').strip(),
                method=(request.form.get('method') or 'POST').strip(),
                template=(request.form.get('template') or '').strip() or None,
                secret=(request.form.get('secret') or '').strip() or None,
                is_active=bool(request.form.get('is_active')))
            if not w.name or not w.url:
                flash('Name + URL required.', 'danger')
            else:
                db.session.add(w); db.session.commit()
                flash(f'Webhook "{w.name}" created.', 'success')
        elif action == 'toggle':
            w = Webhook.query.get_or_404(request.form.get('w_id', type=int))
            w.is_active = not bool(w.is_active); db.session.commit()
        elif action == 'delete':
            w = Webhook.query.get_or_404(request.form.get('w_id', type=int))
            db.session.delete(w); db.session.commit()
        return redirect(url_for('admin.webhooks_admin'))
    rows = Webhook.query.order_by(Webhook.event, Webhook.name).all()
    return render_template('admin/webhooks.html', rows=rows)


@admin_bp.route('/console', methods=['GET', 'POST'])
@login_required
@admin_required
def console():
    """Browser-based sysop console — pre-canned safe commands."""
    output = ''
    cmd = ''
    if request.method == 'POST':
        cmd = (request.form.get('cmd') or '').strip()
        output = _run_sysop_command(cmd)
    return render_template('admin/console.html', output=output, cmd=cmd)


def _run_sysop_command(cmd):
    """Allow-list of safe ops."""
    import subprocess as _sp
    cmd_low = cmd.strip().lower()
    if cmd_low in ('whoison', 'who'):
        from ..models import UserSession as _US, User as _U
        from datetime import timedelta as _td
        cutoff = datetime.utcnow() - _td(minutes=5)
        rows = (_US.query.filter(_US.last_seen >= cutoff)
                .join(_U, _U.id == _US.user_id).all())
        if not rows:
            return 'No users online.'
        return '\n'.join(
            f"{r.user.username:<20} {r.page or '?':<25} {r.ip_address or ''}"
            for r in rows)
    if cmd_low in ('uptime',):
        try:
            r = _sp.run(['uptime'], capture_output=True, text=True, timeout=5)
            return r.stdout
        except Exception as exc:
            return f'error: {exc}'
    if cmd_low.startswith('tail '):
        path = cmd[5:].strip()
        if path not in ('/var/log/syslog', '/var/log/messages',
                        '/var/log/anetbbs.log',
                        '/tmp/anetbbs_dos_dosbox.log'):
            return 'tail: only allowed paths: /var/log/syslog, /var/log/messages, /var/log/anetbbs.log, /tmp/anetbbs_dos_dosbox.log'
        try:
            with open(path) as fh:
                return ''.join(fh.readlines()[-50:])
        except Exception as exc:
            return f'error: {exc}'
    if cmd_low.startswith('journalctl '):
        unit = cmd[len('journalctl '):].strip()
        if not unit.startswith('anetbbs'):
            return 'journalctl: only anetbbs* units allowed'
        try:
            r = _sp.run(['journalctl', '-u', unit, '-n', '50', '--no-pager'],
                        capture_output=True, text=True, timeout=10)
            return r.stdout or r.stderr
        except Exception as exc:
            return f'error: {exc}'
    if cmd_low == 'help' or cmd_low == '?' or not cmd_low:
        return ('Commands:\n'
                '  whoison           — currently-online users\n'
                '  uptime            — host uptime\n'
                '  tail <path>       — last 50 lines of an allow-listed log\n'
                '  journalctl <unit> — last 50 lines of an anetbbs systemd unit\n'
                '  help              — this list')
    return f'Unknown command: {cmd}\nType `help` for the list.'


@admin_bp.route('/word-filter', methods=['GET', 'POST'])
@login_required
@admin_required
def word_filter():
    """Manage profanity / word-filter list."""
    from ..models import WordFilter
    from ..features.word_filter import invalidate
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            pat = (request.form.get('pattern') or '').strip()
            rep = (request.form.get('replacement') or '****').strip() or '****'
            if pat:
                if not WordFilter.query.filter_by(pattern=pat).first():
                    db.session.add(WordFilter(pattern=pat, replacement=rep,
                                              is_active=True))
                    db.session.commit()
                    invalidate()
                    flash(f'Added filter "{pat}".', 'success')
        elif action == 'toggle':
            w = WordFilter.query.get_or_404(request.form.get('w_id', type=int))
            w.is_active = not bool(w.is_active); db.session.commit()
            invalidate()
        elif action == 'delete':
            w = WordFilter.query.get_or_404(request.form.get('w_id', type=int))
            db.session.delete(w); db.session.commit()
            invalidate()
        return redirect(url_for('admin.word_filter'))
    rows = WordFilter.query.order_by(WordFilter.pattern).all()
    return render_template('admin/word_filter.html', rows=rows)


@admin_bp.route('/password-resets')
@login_required
@admin_required
def password_resets():
    """List pending (unused, unexpired) password-reset tokens so the sysop
    can copy the URL and pass it to the user out-of-band (PM, chat, etc.)."""
    now = datetime.utcnow()
    pending = (PasswordResetToken.query
               .filter(PasswordResetToken.used_at.is_(None),
                       PasswordResetToken.expires_at > now)
               .order_by(PasswordResetToken.created_at.desc())
               .all())
    # Build full reset URLs here so the template doesn't need url_for tricks.
    from flask import request as _req
    base = _req.host_url.rstrip('/')
    tokens_with_url = [
        {
            'token': rt,
            'reset_url': f"{base}/auth/reset/{rt.token}",
        }
        for rt in pending
    ]
    return render_template('admin/password_resets.html',
                           tokens=tokens_with_url, now=now)


@admin_bp.route('/password-resets/<int:token_id>/revoke', methods=['POST'])
@login_required
@admin_required
def revoke_reset_token(token_id):
    """Invalidate a pending token (mark it used so it can't be clicked)."""
    rt = PasswordResetToken.query.get_or_404(token_id)
    rt.used_at = datetime.utcnow()
    db.session.commit()
    flash(f'Reset token for {rt.user.username} revoked.', 'success')
    return redirect(url_for('admin.password_resets'))
