# anetbbs/web/profile.py
"""
User profile blueprint - enhanced with avatars, bio, themes
"""
import os
import uuid
from flask import Blueprint, render_template, redirect, url_for, flash, abort, request, current_app, send_from_directory
from flask_login import login_required, current_user
from wtforms import StringField, PasswordField, SubmitField, TextAreaField, BooleanField, SelectField, DateField
from wtforms.validators import DataRequired, EqualTo, Length, Optional, ValidationError
from flask_wtf import FlaskForm
from flask_wtf.file import FileField as WTFFileField, FileAllowed
from werkzeug.utils import secure_filename
from datetime import datetime

from .validators import PermissiveEmail as Email
from ..models import db, User, Post, Theme, UserSession, UserAka, UserSecurityAnswer, SECURITY_QUESTIONS, UserField, UserFieldValue
from ..echomail.routing import parse_address

profile_bp = Blueprint('profile', __name__, url_prefix='/profile')


def get_avatar_url(user):
    """Get the best available avatar URL for a user"""
    if user.avatar_upload:
        return url_for('profile.serve_avatar', filename=user.avatar_upload)
    if user.avatar_url:
        return user.avatar_url
    return None


def is_user_online(user):
    """Check if user is currently online (last seen within 5 minutes)"""
    session = UserSession.query.filter_by(user_id=user.id).first()
    if session and session.last_seen:
        return (datetime.utcnow() - session.last_seen).total_seconds() < 300
    return False


class UpdateProfileForm(FlaskForm):
    """Form for updating user profile"""
    display_name = StringField('Display Name', validators=[Optional(), Length(max=100)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    bio = TextAreaField('Bio', validators=[Optional(), Length(max=500)])
    location = StringField('Location', validators=[Optional(), Length(max=100)])
    website = StringField('Website', validators=[Optional(), Length(max=255)])
    signature = TextAreaField('Forum Signature', validators=[Optional(), Length(max=200)])
    tagline = StringField('FTN Tagline', validators=[Optional(), Length(max=160)],
                          description="One-liner appended to outbound netmail/echomail.")
    date_of_birth = DateField('Date of Birth', validators=[Optional()])
    show_email = BooleanField('Show Email Publicly')
    avatar_url = StringField('Avatar URL', validators=[Optional(), Length(max=500)])
    avatar_file = WTFFileField('Upload Avatar', validators=[Optional(), FileAllowed(['jpg', 'jpeg', 'png', 'gif', 'webp'], 'Images only!')])
    theme_id = SelectField('Theme', coerce=int, validators=[Optional()])
    submit = SubmitField('Update Profile')

    def __init__(self, original_email, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.original_email = original_email
        # Populate theme choices
        themes = Theme.query.filter_by(is_active=True).all()
        self.theme_id.choices = [(0, 'Default (Classic Green)')] + [(t.id, t.display_name) for t in themes]

    def validate_email(self, field):
        """Check if email is already taken by another user"""
        if field.data != self.original_email:
            if User.query.filter_by(email=field.data).first():
                raise ValidationError('Email already registered.')


class ChangePasswordForm(FlaskForm):
    """Form for changing password"""
    current_password = PasswordField('Current Password', validators=[DataRequired()])
    new_password = PasswordField('New Password', validators=[
        DataRequired(),
        Length(min=6, message='Password must be at least 6 characters')
    ])
    confirm_password = PasswordField('Confirm New Password', validators=[
        DataRequired(),
        EqualTo('new_password', message='Passwords must match')
    ])
    submit = SubmitField('Change Password')


def _save_avatar(file_obj, old_filename=None):
    """Save avatar file. Returns tuple (filename, error_message); error_message is None on success."""
    avatars_dir = current_app.config.get('AVATARS_DIR', os.path.join(current_app.config['DATA_DIR'], 'avatars'))
    os.makedirs(avatars_dir, exist_ok=True)

    # Remove old avatar if exists
    if old_filename:
        old_path = os.path.join(avatars_dir, old_filename)
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass

    # Check file size
    file_obj.seek(0, 2)
    size = file_obj.tell()
    file_obj.seek(0)
    max_size = current_app.config.get('AVATAR_MAX_SIZE', 2 * 1024 * 1024)
    if size > max_size:
        return None, 'Avatar too large. Maximum size is 2MB.'

    # Save with unique name
    original_name = secure_filename(file_obj.filename)
    ext = original_name.rsplit('.', 1)[-1].lower() if '.' in original_name else 'jpg'
    unique_name = f"{uuid.uuid4().hex}.{ext}"

    filepath = os.path.join(avatars_dir, unique_name)
    file_obj.save(filepath)

    return unique_name, None


@profile_bp.route('/avatars/<filename>')
def serve_avatar(filename):
    """Serve avatar files"""
    avatars_dir = current_app.config.get('AVATARS_DIR', os.path.join(current_app.config['DATA_DIR'], 'avatars'))
    return send_from_directory(avatars_dir, filename)


@profile_bp.route('/')
@login_required
def index():
    """View current user's profile"""
    return redirect(url_for('profile.view_user', username=current_user.username))


@profile_bp.route('/<username>')
def view_user(username):
    """View a user's profile"""
    user = User.query.filter_by(username=username).first_or_404()

    # Get user's recent posts
    recent_posts = Post.query.filter_by(author_id=user.id)\
        .order_by(Post.created_at.desc())\
        .limit(10)\
        .all()

    # Get statistics
    stats = {
        'total_posts': Post.query.filter_by(author_id=user.id).count(),
        'total_replies': Post.query.filter_by(author_id=user.id).filter(Post.parent_id.isnot(None)).count(),
        'account_age': (datetime.utcnow().date() - user.created_at.date()).days,
    }

    online = is_user_online(user)
    avatar = get_avatar_url(user)

    # Achievements earned (best-effort — tolerate missing tables)
    achievements = []
    try:
        from ..models import UserAchievement
        rows = (UserAchievement.query.filter_by(user_id=user.id)
                .order_by(UserAchievement.earned_at.desc()).all())
        for ua in rows:
            if ua.achievement:
                achievements.append({
                    'name': ua.achievement.name,
                    'description': ua.achievement.description,
                    'icon': ua.achievement.icon or 'patch-check',
                    'earned_at': ua.earned_at,
                })
    except Exception:
        db.session.rollback()

    # Group memberships (with leader flag)
    groups = []
    try:
        from ..models import UserGroupMember
        rows = UserGroupMember.query.filter_by(user_id=user.id).all()
        for r in rows:
            if r.group:
                groups.append({
                    'group': r.group,
                    'is_leader': (r.group.leader_id == user.id),
                })
    except Exception:
        db.session.rollback()

    custom_fields_list = (UserField.query
                          .filter_by(show_in_profile=True)
                          .order_by(UserField.sort_order, UserField.id).all())
    custom_values = {ufv.field_id: ufv.value
                     for ufv in UserFieldValue.query.filter_by(user_id=user.id).all()}

    return render_template('profile/view.html',
                         user=user,
                         recent_posts=recent_posts,
                         stats=stats,
                         online=online,
                         avatar=avatar,
                         achievements=achievements,
                         groups=groups,
                         custom_fields_list=custom_fields_list,
                         custom_values=custom_values)


@profile_bp.route('/edit', methods=['GET', 'POST'])
@login_required
def edit():
    """Edit current user's profile"""
    form = UpdateProfileForm(current_user.email)

    if form.validate_on_submit():
        current_user.display_name = form.display_name.data or None
        current_user.email = form.email.data
        current_user.bio = form.bio.data or None
        current_user.location = form.location.data or None
        current_user.website = form.website.data or None
        current_user.signature = form.signature.data or None
        current_user.tagline = form.tagline.data or None
        current_user.date_of_birth = form.date_of_birth.data
        current_user.show_email = form.show_email.data

        # Handle theme selection
        theme_id = form.theme_id.data
        current_user.theme_id = theme_id if theme_id and theme_id != 0 else None

        # Handle avatar upload
        if form.avatar_file.data and form.avatar_file.data.filename:
            filename, error = _save_avatar(form.avatar_file.data, current_user.avatar_upload)
            if error:
                flash(error, 'danger')
            else:
                current_user.avatar_upload = filename
                current_user.avatar_url = None  # Clear URL if upload provided
        elif form.avatar_url.data:
            # Clear upload if URL provided
            if current_user.avatar_upload:
                avatars_dir = current_app.config.get('AVATARS_DIR', os.path.join(current_app.config['DATA_DIR'], 'avatars'))
                old_path = os.path.join(avatars_dir, current_user.avatar_upload)
                if os.path.exists(old_path):
                    try:
                        os.remove(old_path)
                    except OSError:
                        pass
                current_user.avatar_upload = None
            current_user.avatar_url = form.avatar_url.data

        # Save custom field values
        for cf in UserField.query.all():
            val = (request.form.get(f'cf_{cf.name}') or '').strip()
            ufv = (UserFieldValue.query
                   .filter_by(user_id=current_user.id, field_id=cf.id).first())
            if val:
                if ufv:
                    ufv.value = val[:2000]
                else:
                    db.session.add(UserFieldValue(
                        user_id=current_user.id, field_id=cf.id, value=val[:2000]))
            elif ufv:
                db.session.delete(ufv)

        db.session.commit()
        flash('Profile updated successfully!', 'success')
        return redirect(url_for('profile.view_user', username=current_user.username))
    elif request.method == 'GET':
        form.display_name.data = current_user.display_name
        form.email.data = current_user.email
        form.bio.data = current_user.bio
        form.location.data = current_user.location
        form.website.data = current_user.website
        form.signature.data = current_user.signature
        form.tagline.data = current_user.tagline
        form.date_of_birth.data = current_user.date_of_birth
        form.show_email.data = current_user.show_email
        form.avatar_url.data = current_user.avatar_url
        form.theme_id.data = current_user.theme_id or 0

    themes = Theme.query.filter_by(is_active=True).all()
    avatar = get_avatar_url(current_user)
    custom_fields_list = UserField.query.order_by(UserField.sort_order, UserField.id).all()
    custom_values = {ufv.field_id: ufv.value
                     for ufv in UserFieldValue.query.filter_by(user_id=current_user.id).all()}
    return render_template('profile/edit.html', form=form, themes=themes, avatar=avatar,
                           custom_fields_list=custom_fields_list, custom_values=custom_values)


@profile_bp.route('/avatar/remove', methods=['POST'])
@login_required
def remove_avatar():
    """Remove current user's avatar"""
    if current_user.avatar_upload:
        avatars_dir = current_app.config.get('AVATARS_DIR', os.path.join(current_app.config['DATA_DIR'], 'avatars'))
        old_path = os.path.join(avatars_dir, current_user.avatar_upload)
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass
        current_user.avatar_upload = None
    current_user.avatar_url = None
    db.session.commit()
    flash('Avatar removed.', 'success')
    return redirect(url_for('profile.edit'))


@profile_bp.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    """Change current user's password"""
    form = ChangePasswordForm()

    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            flash('Current password is incorrect.', 'danger')
            return redirect(url_for('profile.change_password'))

        current_user.set_password(form.new_password.data)
        db.session.commit()

        flash('Password changed successfully!', 'success')
        return redirect(url_for('profile.view_user', username=current_user.username))

    return render_template('profile/change_password.html', form=form)


@profile_bp.route('/themes')
@login_required
def themes():
    """Theme preview page"""
    all_themes = Theme.query.filter_by(is_active=True).all()
    return render_template('profile/themes.html', themes=all_themes)


@profile_bp.route('/akas', methods=['GET', 'POST'])
@login_required
def akas():
    """Manage the FidoNet addresses (AKAs) this user can send mail FROM.

    A sysop running multiple nodes (e.g. 1:234/5 in fidonet AND 21:1/100 in
    fsxnet) needs to pick which AKA to use per-message. This page lets them
    add, remove, and mark a primary."""
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            addr = (request.form.get('address') or '').strip()
            domain = (request.form.get('domain') or 'fidonet').strip().lower()
            if not parse_address(addr):
                flash(f'Bad FTN address: {addr!r} (use zone:net/node[.point])', 'danger')
                return redirect(url_for('profile.akas'))
            existing = UserAka.query.filter_by(
                user_id=current_user.id, address=addr).first()
            if existing:
                flash('That AKA already exists.', 'warning')
                return redirect(url_for('profile.akas'))
            aka = UserAka(user_id=current_user.id, address=addr, domain=domain)
            # First AKA is automatically the primary
            if not UserAka.query.filter_by(user_id=current_user.id).first():
                aka.is_primary = True
            db.session.add(aka)
            db.session.commit()
            flash(f'Added AKA {addr} ({domain}).', 'success')
        elif action == 'delete':
            aka = UserAka.query.get_or_404(int(request.form.get('aka_id')))
            if aka.user_id != current_user.id:
                abort(403)
            db.session.delete(aka)
            db.session.commit()
            flash(f'Deleted {aka.address}.', 'success')
        elif action == 'set_primary':
            aka = UserAka.query.get_or_404(int(request.form.get('aka_id')))
            if aka.user_id != current_user.id:
                abort(403)
            UserAka.query.filter_by(user_id=current_user.id).update({UserAka.is_primary: False})
            aka.is_primary = True
            db.session.commit()
            flash(f'Primary AKA is now {aka.address}.', 'success')
        return redirect(url_for('profile.akas'))

    user_akas = UserAka.query.filter_by(user_id=current_user.id).order_by(
        UserAka.is_primary.desc(), UserAka.address).all()
    return render_template('profile/akas.html', akas=user_akas)


@profile_bp.route('/security-questions', methods=['GET', 'POST'])
@login_required
def security_questions():
    """Set or update security questions for password recovery."""
    existing = {qa.question: qa for qa in current_user.security_answers}
    q_choices = [(q, q) for q in SECURITY_QUESTIONS]

    if request.method == 'POST':
        # Collect up to 3 question/answer pairs from the form
        saved = 0
        for i in range(1, 4):
            q = request.form.get(f'question_{i}', '').strip()
            a = request.form.get(f'answer_{i}', '').strip()
            if not q or not a:
                continue
            if q not in SECURITY_QUESTIONS:
                continue
            rec = existing.get(q)
            if rec:
                rec.answer_hash = UserSecurityAnswer.hash_answer(a)
            else:
                rec = UserSecurityAnswer(
                    user_id=current_user.id,
                    question=q,
                    answer_hash=UserSecurityAnswer.hash_answer(a),
                )
                db.session.add(rec)
            saved += 1

        # Remove any questions that were cleared (question slot left blank)
        submitted_questions = set()
        for i in range(1, 4):
            q = request.form.get(f'question_{i}', '').strip()
            if q and q in SECURITY_QUESTIONS:
                submitted_questions.add(q)
        for qa in list(current_user.security_answers):
            if qa.question not in submitted_questions:
                db.session.delete(qa)

        db.session.commit()
        flash(f'Security questions updated ({saved} saved).', 'success')
        return redirect(url_for('profile.security_questions'))

    current_answers = list(current_user.security_answers)
    return render_template('profile/security_questions.html',
                           q_choices=q_choices,
                           current_answers=current_answers)


@profile_bp.route('/export')
@login_required
def export_data():
    """Self-service data takeout: download a JSON dump of the
    current user's profile, posts, and private messages.
    Each section is independent so a missing column / table won't
    blow up the whole export."""
    import json as _json
    from flask import Response

    def _attr(obj, name, default=None):
        try:
            v = getattr(obj, name, default)
            return v
        except Exception:
            return default

    def _iso(dt):
        try:
            return dt.isoformat() if dt else None
        except Exception:
            return None

    u = current_user
    out = {
        'profile': {
            'id': _attr(u, 'id'),
            'username': _attr(u, 'username'),
            'email': _attr(u, 'email'),
            'display_name': _attr(u, 'display_name'),
            'bio': _attr(u, 'bio'),
            'location': _attr(u, 'location'),
            'signature': _attr(u, 'signature'),
            'tagline': _attr(u, 'tagline'),
            'created_at': _iso(_attr(u, 'created_at')),
            'last_login': _iso(_attr(u, 'last_login')),
        },
        'posts': [],
        'pms_sent': [],
        'pms_received': [],
        'shouts': [],
    }

    try:
        from ..models import Post
        for p in (Post.query.filter_by(author_id=u.id)
                  .order_by(Post.created_at).all()):
            out['posts'].append({
                'id': p.id, 'board_id': p.board_id,
                'parent_id': p.parent_id,
                'subject': p.subject, 'content': p.content,
                'created_at': _iso(p.created_at),
            })
    except Exception:
        db.session.rollback()

    try:
        from ..models import PrivateMessage
        for m in (PrivateMessage.query.filter_by(sender_id=u.id)
                  .order_by(PrivateMessage.created_at).all()):
            out['pms_sent'].append({
                'id': m.id,
                'recipient_id': m.recipient_id,
                'subject': m.subject, 'content': m.content,
                'created_at': _iso(m.created_at),
            })
        for m in (PrivateMessage.query.filter_by(recipient_id=u.id)
                  .order_by(PrivateMessage.created_at).all()):
            out['pms_received'].append({
                'id': m.id,
                'sender_id': m.sender_id,
                'subject': m.subject, 'content': m.content,
                'created_at': _iso(m.created_at),
            })
    except Exception:
        db.session.rollback()

    try:
        from ..models import ShoutboxPost
        for s in (ShoutboxPost.query.filter_by(user_id=u.id)
                  .order_by(ShoutboxPost.created_at).all()):
            out['shouts'].append({
                'id': s.id, 'text': s.text,
                'created_at': _iso(s.created_at),
            })
    except Exception:
        db.session.rollback()

    body = _json.dumps(out, indent=2)
    fname = f'{_attr(u, "username", "user")}-export.json'
    return Response(body, mimetype='application/json',
                    headers={'Content-Disposition':
                             f'attachment; filename="{fname}"'})
