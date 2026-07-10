"""
Web admin pages to CRUD the data-driven BBS menus.
Routes mounted under /admin/bbs-menus/.
"""
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user

from ..models import db, BbsMenu, BbsMenuItem, BbsAnsiScreen
from .access_control import require_admin as _admin_required

menu_admin_bp = Blueprint('menu_admin', __name__, url_prefix='/admin/bbs-menus')


# Action types known to the menu engine — keep in sync with menu_engine._ACTIONS
ACTION_TYPES = [
    ('goto', 'Go to another menu (action_args = menu name)'),
    ('door', 'Launch a door game (action_args = Game id)'),
    ('exec', 'Run external program — args is JSON or simple cmdline'),
    ('ansi', 'Show an ANSI screen (action_args = slot name)'),
    ('boards', 'Open message boards'),
    ('pm', 'PM inbox'),
    ('pm_send', 'Compose new PM'),
    ('imsg', 'InterBBS IM inbox (MSP)'),
    ('imsg_send', 'Send InterBBS IM (MSP)'),
    ('bulletins', 'Bulletins'),
    ('echo', 'Echomail areas'),
    ('echo_post', 'Compose echomail'),
    ('files', 'File library'),
    ('games', 'Game center'),
    ('rss', 'RSS news reader'),
    ('guru', 'Ask Anet (help guru search)'),
    ('chat', 'Chat menu'),
    ('multinode', 'Multinode chat (between connected terminal nodes)'),
    ('oneliners', 'Show one-liners + last 10 callers'),
    ('lastcallers', 'Last callers list (full, paginated)'),
    ('who', "Who's online"),
    ('profile', 'View own profile'),
    ('edit_prof', 'Edit profile'),
    ('passwd', 'Change password'),
    ('sysop', 'Sysop tools (admin only)'),
    ('page', 'Page sysop'),
    ('dialout', 'Dial-out menu'),
    ('logoff', 'End session'),
]


# Standard slot names rendered automatically by the session lifecycle.
BUILTIN_SCREEN_SLOTS = [
    ('welcome', 'Pre-login welcome banner (telnet only — auto-login SSH/rlogin skips it)'),
    ('goodbye', 'Logoff goodbye screen (all protocols)'),
    ('newuser', 'Shown after successful new-user registration'),
]


@menu_admin_bp.route('/')
@login_required
@_admin_required
def list_menus():
    menus = BbsMenu.query.order_by(BbsMenu.name).all()
    name_to_menu = {m.name: m for m in menus}
    # children[parent_name] -> list of dicts describing menus reached via goto.
    children = {m.name: [] for m in menus}
    pointed_to = set()
    for m in menus:
        seen = set()
        for it in m.items:
            if (it.action_type == 'goto' and it.action_args in name_to_menu
                    and it.action_args != m.name
                    and it.action_args not in seen):
                seen.add(it.action_args)
                children[m.name].append({
                    'name': it.action_args,
                    'title': name_to_menu[it.action_args].title,
                    'via_hotkey': it.hotkey,
                    'via_label': it.label,
                })
                pointed_to.add(it.action_args)
    # Roots = menus no other menu points at — entry points.
    roots = [m for m in menus if m.name not in pointed_to]
    return render_template('menu_admin/list.html',
                           menus=menus, roots=roots, children=children,
                           name_to_menu=name_to_menu)


@menu_admin_bp.route('/new', methods=['GET', 'POST'])
@login_required
@_admin_required
def new_menu():
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        title = (request.form.get('title') or '').strip()
        if not name or not title:
            flash('Name and title are required.', 'danger')
            return redirect(url_for('menu_admin.new_menu'))
        if BbsMenu.query.filter_by(name=name).first():
            flash(f"Menu '{name}' already exists.", 'danger')
            return redirect(url_for('menu_admin.new_menu'))
        m = BbsMenu(
            name=name[:50], title=title[:100],
            ansi_screen=request.form.get('ansi_screen') or '',
            prompt=request.form.get('prompt') or 'Choice: ',
            is_default=bool(request.form.get('is_default')),
            min_access=int(request.form.get('min_access') or 0),
        )
        db.session.add(m); db.session.commit()
        return redirect(url_for('menu_admin.edit_menu', menu_id=m.id))
    return render_template('menu_admin/edit.html', menu=None, items=[], action_types=ACTION_TYPES)


@menu_admin_bp.route('/<int:menu_id>/edit', methods=['GET', 'POST'])
@login_required
@_admin_required
def edit_menu(menu_id):
    m = BbsMenu.query.get_or_404(menu_id)
    if request.method == 'POST':
        m.title = (request.form.get('title') or m.title)[:100]
        m.ansi_screen = request.form.get('ansi_screen') or ''
        m.prompt = (request.form.get('prompt') or 'Choice: ')[:100]
        m.is_default = bool(request.form.get('is_default'))
        m.min_access = int(request.form.get('min_access') or 0)
        db.session.commit()
        flash('Menu saved.', 'success')
        return redirect(url_for('menu_admin.edit_menu', menu_id=m.id))
    items = m.items.order_by(BbsMenuItem.sort_order, BbsMenuItem.id).all()
    return render_template('menu_admin/edit.html', menu=m, items=items, action_types=ACTION_TYPES)


@menu_admin_bp.route('/<int:menu_id>/delete', methods=['POST'])
@login_required
@_admin_required
def delete_menu(menu_id):
    m = BbsMenu.query.get_or_404(menu_id)
    db.session.delete(m); db.session.commit()
    flash(f"Deleted menu '{m.name}'.", 'success')
    return redirect(url_for('menu_admin.list_menus'))


@menu_admin_bp.route('/<int:menu_id>/items/new', methods=['POST'])
@login_required
@_admin_required
def add_item(menu_id):
    m = BbsMenu.query.get_or_404(menu_id)
    item = BbsMenuItem(
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
    return redirect(url_for('menu_admin.edit_menu', menu_id=m.id))


@menu_admin_bp.route('/items/<int:item_id>/delete', methods=['POST'])
@login_required
@_admin_required
def delete_item(item_id):
    it = BbsMenuItem.query.get_or_404(item_id)
    mid = it.menu_id
    db.session.delete(it); db.session.commit()
    return redirect(url_for('menu_admin.edit_menu', menu_id=mid))


@menu_admin_bp.route('/items/<int:item_id>/edit', methods=['POST'])
@login_required
@_admin_required
def edit_item(item_id):
    it = BbsMenuItem.query.get_or_404(item_id)
    it.hotkey = (request.form.get('hotkey') or it.hotkey)[:4].upper()
    it.label = (request.form.get('label') or it.label)[:80]
    it.action_type = request.form.get('action_type') or it.action_type
    it.action_args = (request.form.get('action_args') or '')[:255] or None
    it.min_access = int(request.form.get('min_access') or 0)
    it.sort_order = int(request.form.get('sort_order') or 0)
    it.is_visible = bool(request.form.get('is_visible'))
    db.session.commit()
    return redirect(url_for('menu_admin.edit_menu', menu_id=it.menu_id))


@menu_admin_bp.route('/seed-defaults', methods=['POST'])
@login_required
@_admin_required
def seed_defaults():
    """Populate the default 'main' menu if no menus exist."""
    from ..features.menu_engine import seed_default_menus
    n = seed_default_menus()
    flash(f'Seeded {n} default menu items.', 'success')
    return redirect(url_for('menu_admin.list_menus'))


# ---------------------------------------------------------------------------
# ANSI screens (welcome / goodbye / newuser / custom slots)
# ---------------------------------------------------------------------------

@menu_admin_bp.route('/screens')
@login_required
@_admin_required
def list_screens():
    rows = BbsAnsiScreen.query.order_by(BbsAnsiScreen.slot).all()
    existing_slots = {r.slot for r in rows}
    missing_builtins = [(s, d) for s, d in BUILTIN_SCREEN_SLOTS
                        if s not in existing_slots]
    return render_template('menu_admin/screens_list.html',
                           rows=rows,
                           builtins=BUILTIN_SCREEN_SLOTS,
                           missing_builtins=missing_builtins)


@menu_admin_bp.route('/screens/new', methods=['GET', 'POST'])
@menu_admin_bp.route('/screens/<int:screen_id>/edit', methods=['GET', 'POST'])
@login_required
@_admin_required
def edit_screen(screen_id=None):
    row = BbsAnsiScreen.query.get(screen_id) if screen_id else None
    if request.method == 'POST':
        slot = (request.form.get('slot') or '').strip()
        if not slot:
            flash('Slot name required.', 'danger')
            return redirect(url_for('menu_admin.list_screens'))
        if row is None:
            row = BbsAnsiScreen.query.filter_by(slot=slot).first()
            if row is None:
                row = BbsAnsiScreen(slot=slot, body='')
                db.session.add(row)
        elif slot != row.slot:
            row.slot = slot
        row.title = (request.form.get('title') or '')[:120] or None
        row.body = request.form.get('body') or ''
        row.pause_after = bool(request.form.get('pause_after'))
        row.is_active = bool(request.form.get('is_active'))
        db.session.commit()
        flash(f'Screen "{row.slot}" saved.', 'success')
        return redirect(url_for('menu_admin.list_screens'))

    # Allow GET with ?slot=foo to pre-fill (for the "Create" link from the
    # missing-builtin list).
    prefill_slot = request.args.get('slot') or ''
    return render_template('menu_admin/screens_edit.html',
                           row=row, prefill_slot=prefill_slot,
                           builtins=BUILTIN_SCREEN_SLOTS)


@menu_admin_bp.route('/screens/<int:screen_id>/delete', methods=['POST'])
@login_required
@_admin_required
def delete_screen(screen_id):
    row = BbsAnsiScreen.query.get_or_404(screen_id)
    db.session.delete(row); db.session.commit()
    flash(f'Deleted screen "{row.slot}".', 'success')
    return redirect(url_for('menu_admin.list_screens'))


@menu_admin_bp.route('/seed-samples', methods=['POST'])
@login_required
@_admin_required
def seed_samples():
    """Insert example menu items demonstrating exec / ansi actions
    so a new sysop can see them in action without writing code.

    Currently adds:
      * A 'weather' exec item to the main menu (uses wttr.in via curl)
      * A 'show welcome' ansi item that re-shows the welcome screen
    Idempotent — won't add duplicates.
    """
    main = BbsMenu.query.filter_by(name='main').first()
    if not main:
        flash('No "main" menu found. Seed defaults first.', 'danger')
        return redirect(url_for('menu_admin.list_menus'))

    added = 0
    # Weather exec item
    if not BbsMenuItem.query.filter_by(menu_id=main.id, hotkey='2').first():
        db.session.add(BbsMenuItem(
            menu_id=main.id, hotkey='2',
            label='Weather (sample exec door)',
            action_type='exec',
            action_args=(
                '{"name":"Weather","cmd":"curl -s wttr.in/?A0Tn0"}'),
            sort_order=87, is_visible=True))
        added += 1
    # ANSI demo item — re-show the welcome banner
    if not BbsMenuItem.query.filter_by(menu_id=main.id, hotkey='1').first():
        db.session.add(BbsMenuItem(
            menu_id=main.id, hotkey='1',
            label='Show welcome banner (sample ansi)',
            action_type='ansi',
            action_args='welcome',
            sort_order=88, is_visible=True))
        added += 1

    db.session.commit()
    flash(f'Added {added} sample item(s) to the main menu.', 'success')
    return redirect(url_for('menu_admin.edit_menu', menu_id=main.id))


@menu_admin_bp.route('/<int:menu_id>/spawn-submenu', methods=['POST'])
@login_required
@_admin_required
def spawn_submenu(menu_id):
    """Create a new menu and add a 'goto' item linking to it from the
    parent menu — one-click submenu creation."""
    parent = BbsMenu.query.get_or_404(menu_id)
    name = (request.form.get('name') or '').strip().lower()
    title = (request.form.get('title') or '').strip()
    hotkey = (request.form.get('hotkey') or '').strip().upper()[:4]
    label = (request.form.get('label') or title or name).strip()
    if not name or not title or not hotkey:
        flash('Name, title and hotkey are required.', 'danger')
        return redirect(url_for('menu_admin.edit_menu', menu_id=parent.id))
    if BbsMenu.query.filter_by(name=name).first():
        flash(f"Menu '{name}' already exists.", 'danger')
        return redirect(url_for('menu_admin.edit_menu', menu_id=parent.id))
    sub = BbsMenu(name=name[:50], title=title[:100],
                  prompt='Choice: ', is_default=False, min_access=0)
    db.session.add(sub); db.session.flush()
    # Auto-add a 'Q' / back-to-parent item on the submenu so users aren't trapped.
    db.session.add(BbsMenuItem(
        menu_id=sub.id, hotkey='Q', label=f'Back to {parent.title}',
        action_type='goto', action_args=parent.name, sort_order=999))
    # Link from parent
    db.session.add(BbsMenuItem(
        menu_id=parent.id, hotkey=hotkey, label=label[:80],
        action_type='goto', action_args=name, sort_order=500))
    db.session.commit()
    flash(f'Created submenu "{name}" — edit its items below.', 'success')
    return redirect(url_for('menu_admin.edit_menu', menu_id=sub.id))
