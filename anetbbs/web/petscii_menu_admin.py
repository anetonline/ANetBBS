"""
Web admin pages to CRUD sysop-built custom PETSCII menus.
Routes mounted under /admin/petscii-menus/.

Deliberately separate from menu_admin.py (the ANSI custom-menu system)
rather than sharing BbsMenu/BbsMenuItem -- see models.PetsciiMenu's own
docstring for why. Mirrors menu_admin.py's route/template shape closely
(same admin UX a sysop already knows) but with a much smaller
action_type list (only what petscii_ui.py actually implements) and no
ANSI-screen field at all.
"""
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required

from ..models import db, PetsciiMenu, PetsciiMenuItem
from .access_control import require_admin as _admin_required

petscii_menu_admin_bp = Blueprint('petscii_menu_admin', __name__,
                                  url_prefix='/admin/petscii-menus')


# Action types known to petscii_ui.py's custom-menu interpreter -- keep
# in sync with petscii_ui._CUSTOM_MENU_ACTIONS plus the two specially
# handled types ('goto', 'logoff').
ACTION_TYPES = [
    ('goto', 'Go to another PETSCII menu (action_args = menu name)'),
    ('boards', 'Message boards'),
    ('echo', 'Echomail areas'),
    ('pm', 'Private messages'),
    ('files', 'File-area browsing'),
    ('who', "Who's online"),
    ('profile', 'View own profile'),
    ('games', 'Games (Number Guessing)'),
    ('logoff', 'End session'),
]


@petscii_menu_admin_bp.route('/')
@login_required
@_admin_required
def list_menus():
    menus = PetsciiMenu.query.order_by(PetsciiMenu.name).all()
    return render_template('petscii_menu_admin/list.html', menus=menus)


@petscii_menu_admin_bp.route('/new', methods=['GET', 'POST'])
@login_required
@_admin_required
def new_menu():
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip().lower()
        title = (request.form.get('title') or '').strip()
        if not name or not title:
            flash('Name and title are required.', 'danger')
            return redirect(url_for('petscii_menu_admin.new_menu'))
        if PetsciiMenu.query.filter_by(name=name).first():
            flash(f"PETSCII menu '{name}' already exists.", 'danger')
            return redirect(url_for('petscii_menu_admin.new_menu'))
        m = PetsciiMenu(
            name=name[:50], title=title[:100],
            prompt=request.form.get('prompt') or 'Choice: ',
            is_default=bool(request.form.get('is_default')),
            min_access=int(request.form.get('min_access') or 0),
        )
        db.session.add(m); db.session.commit()
        return redirect(url_for('petscii_menu_admin.edit_menu', menu_id=m.id))
    return render_template('petscii_menu_admin/edit.html', menu=None, items=[],
                           action_types=ACTION_TYPES)


@petscii_menu_admin_bp.route('/<int:menu_id>/edit', methods=['GET', 'POST'])
@login_required
@_admin_required
def edit_menu(menu_id):
    m = PetsciiMenu.query.get_or_404(menu_id)
    if request.method == 'POST':
        m.title = (request.form.get('title') or m.title)[:100]
        m.prompt = (request.form.get('prompt') or 'Choice: ')[:100]
        m.is_default = bool(request.form.get('is_default'))
        m.min_access = int(request.form.get('min_access') or 0)
        db.session.commit()
        flash('PETSCII menu saved.', 'success')
        return redirect(url_for('petscii_menu_admin.edit_menu', menu_id=m.id))
    items = m.items.order_by(PetsciiMenuItem.sort_order, PetsciiMenuItem.id).all()
    return render_template('petscii_menu_admin/edit.html', menu=m, items=items,
                           action_types=ACTION_TYPES)


@petscii_menu_admin_bp.route('/<int:menu_id>/delete', methods=['POST'])
@login_required
@_admin_required
def delete_menu(menu_id):
    m = PetsciiMenu.query.get_or_404(menu_id)
    db.session.delete(m); db.session.commit()
    flash(f"Deleted PETSCII menu '{m.name}'.", 'success')
    return redirect(url_for('petscii_menu_admin.list_menus'))


@petscii_menu_admin_bp.route('/<int:menu_id>/items/new', methods=['POST'])
@login_required
@_admin_required
def add_item(menu_id):
    m = PetsciiMenu.query.get_or_404(menu_id)
    item = PetsciiMenuItem(
        menu_id=m.id,
        hotkey=(request.form.get('hotkey') or '?')[:4].upper(),
        label=(request.form.get('label') or 'Item')[:80],
        action_type=request.form.get('action_type') or 'logoff',
        action_args=(request.form.get('action_args') or '')[:255] or None,
        min_access=int(request.form.get('min_access') or 0),
        sort_order=int(request.form.get('sort_order') or 0),
        is_visible=True,
    )
    db.session.add(item); db.session.commit()
    return redirect(url_for('petscii_menu_admin.edit_menu', menu_id=m.id))


@petscii_menu_admin_bp.route('/items/<int:item_id>/delete', methods=['POST'])
@login_required
@_admin_required
def delete_item(item_id):
    it = PetsciiMenuItem.query.get_or_404(item_id)
    mid = it.menu_id
    db.session.delete(it); db.session.commit()
    return redirect(url_for('petscii_menu_admin.edit_menu', menu_id=mid))


@petscii_menu_admin_bp.route('/items/<int:item_id>/edit', methods=['POST'])
@login_required
@_admin_required
def edit_item(item_id):
    it = PetsciiMenuItem.query.get_or_404(item_id)
    it.hotkey = (request.form.get('hotkey') or it.hotkey)[:4].upper()
    it.label = (request.form.get('label') or it.label)[:80]
    it.action_type = request.form.get('action_type') or it.action_type
    it.action_args = (request.form.get('action_args') or '')[:255] or None
    it.min_access = int(request.form.get('min_access') or 0)
    it.sort_order = int(request.form.get('sort_order') or 0)
    it.is_visible = bool(request.form.get('is_visible'))
    db.session.commit()
    return redirect(url_for('petscii_menu_admin.edit_menu', menu_id=it.menu_id))


@petscii_menu_admin_bp.route('/<int:menu_id>/spawn-submenu', methods=['POST'])
@login_required
@_admin_required
def spawn_submenu(menu_id):
    """Create a new PETSCII menu and add a 'goto' item linking to it from
    the parent -- one-click submenu creation, mirrors menu_admin.py's."""
    parent = PetsciiMenu.query.get_or_404(menu_id)
    name = (request.form.get('name') or '').strip().lower()
    title = (request.form.get('title') or '').strip()
    hotkey = (request.form.get('hotkey') or '').strip().upper()[:4]
    label = (request.form.get('label') or title or name).strip()
    if not name or not title or not hotkey:
        flash('Name, title and hotkey are required.', 'danger')
        return redirect(url_for('petscii_menu_admin.edit_menu', menu_id=parent.id))
    if PetsciiMenu.query.filter_by(name=name).first():
        flash(f"PETSCII menu '{name}' already exists.", 'danger')
        return redirect(url_for('petscii_menu_admin.edit_menu', menu_id=parent.id))
    sub = PetsciiMenu(name=name[:50], title=title[:100],
                      prompt='Choice: ', is_default=False, min_access=0)
    db.session.add(sub); db.session.flush()
    db.session.add(PetsciiMenuItem(
        menu_id=sub.id, hotkey='Q', label=f'Back to {parent.title}',
        action_type='goto', action_args=parent.name, sort_order=999))
    db.session.add(PetsciiMenuItem(
        menu_id=parent.id, hotkey=hotkey, label=label[:80],
        action_type='goto', action_args=name, sort_order=500))
    db.session.commit()
    flash(f'Created PETSCII submenu "{name}" — edit its items below.', 'success')
    return redirect(url_for('petscii_menu_admin.edit_menu', menu_id=sub.id))
