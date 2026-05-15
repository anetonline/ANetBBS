# anetbbs/web/groups.py
"""User-formed groups / clans."""
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user

from ..models import db, User, UserGroup, UserGroupMember


groups_bp = Blueprint('groups', __name__, url_prefix='/groups')


@groups_bp.route('/')
def index():
    rows = UserGroup.query.order_by(UserGroup.created_at.desc()).all()
    return render_template('groups/index.html', groups=rows)


@groups_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new():
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        tag = (request.form.get('tag') or '').strip().upper()[:8]
        desc = (request.form.get('description') or '').strip() or None
        is_open = bool(request.form.get('is_open'))
        if not name:
            flash('Name required.', 'danger')
            return redirect(url_for('groups.new'))
        if UserGroup.query.filter_by(name=name).first():
            flash('Group name already taken.', 'danger')
            return redirect(url_for('groups.new'))
        g = UserGroup(name=name, tag=tag or None, description=desc,
                      leader_id=current_user.id, is_open=is_open)
        db.session.add(g); db.session.flush()
        db.session.add(UserGroupMember(group_id=g.id,
                                       user_id=current_user.id))
        db.session.commit()
        flash(f'Group "{name}" created.', 'success')
        return redirect(url_for('groups.view', group_id=g.id))
    return render_template('groups/new.html')


@groups_bp.route('/<int:group_id>')
def view(group_id):
    g = UserGroup.query.get_or_404(group_id)
    members = (UserGroupMember.query.filter_by(group_id=g.id)
               .order_by(UserGroupMember.joined_at).all())
    is_member = False
    if current_user.is_authenticated:
        is_member = (UserGroupMember.query.filter_by(group_id=g.id,
                                                     user_id=current_user.id)
                     .first() is not None)
    return render_template('groups/view.html', g=g, members=members,
                           is_member=is_member)


@groups_bp.route('/<int:group_id>/join', methods=['POST'])
@login_required
def join(group_id):
    g = UserGroup.query.get_or_404(group_id)
    if not g.is_open and g.leader_id != current_user.id:
        flash('This group is invite-only — ask the leader.', 'warning')
        return redirect(url_for('groups.view', group_id=g.id))
    existing = UserGroupMember.query.filter_by(group_id=g.id,
                                               user_id=current_user.id).first()
    if existing:
        flash('Already a member.', 'info')
    else:
        db.session.add(UserGroupMember(group_id=g.id,
                                       user_id=current_user.id))
        db.session.commit()
        flash(f'Joined {g.name}.', 'success')
    return redirect(url_for('groups.view', group_id=g.id))


@groups_bp.route('/<int:group_id>/leave', methods=['POST'])
@login_required
def leave(group_id):
    g = UserGroup.query.get_or_404(group_id)
    row = UserGroupMember.query.filter_by(group_id=g.id,
                                          user_id=current_user.id).first()
    if row:
        if g.leader_id == current_user.id:
            flash('Transfer leadership before leaving.', 'warning')
            return redirect(url_for('groups.view', group_id=g.id))
        db.session.delete(row); db.session.commit()
        flash(f'Left {g.name}.', 'success')
    return redirect(url_for('groups.view', group_id=g.id))


@groups_bp.route('/<int:group_id>/invite', methods=['POST'])
@login_required
def invite(group_id):
    """Leader invites a user (immediate add — keeping it simple)."""
    g = UserGroup.query.get_or_404(group_id)
    if g.leader_id != current_user.id and not current_user.is_admin:
        abort(403)
    name = (request.form.get('username') or '').strip()
    target = User.query.filter(User.username.ilike(name)).first()
    if not target:
        flash('User not found.', 'danger')
        return redirect(url_for('groups.view', group_id=g.id))
    if UserGroupMember.query.filter_by(group_id=g.id,
                                       user_id=target.id).first():
        flash('Already a member.', 'info')
    else:
        db.session.add(UserGroupMember(group_id=g.id,
                                       user_id=target.id))
        db.session.commit()
        flash(f'{target.username} added to {g.name}.', 'success')
    return redirect(url_for('groups.view', group_id=g.id))


@groups_bp.route('/<int:group_id>/kick', methods=['POST'])
@login_required
def kick(group_id):
    g = UserGroup.query.get_or_404(group_id)
    if g.leader_id != current_user.id and not current_user.is_admin:
        abort(403)
    member_id = request.form.get('member_id')
    row = UserGroupMember.query.get(member_id)
    if row and row.group_id == g.id and row.user_id != g.leader_id:
        db.session.delete(row); db.session.commit()
        flash('Member removed.', 'success')
    return redirect(url_for('groups.view', group_id=g.id))


@groups_bp.route('/<int:group_id>/delete', methods=['POST'])
@login_required
def delete(group_id):
    g = UserGroup.query.get_or_404(group_id)
    if g.leader_id != current_user.id and not current_user.is_admin:
        abort(403)
    db.session.delete(g); db.session.commit()
    flash('Group deleted.', 'success')
    return redirect(url_for('groups.index'))
