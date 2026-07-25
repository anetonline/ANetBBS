# anetbbs/web/games_admin.py
"""
Admin Games blueprint — manage the ANetBBS Game Center.
"""
import logging
from datetime import datetime

from flask import (Blueprint, render_template, redirect, url_for,
                   flash, current_app)
from flask_login import login_required
from flask_wtf import FlaskForm
from wtforms import (StringField, TextAreaField, SelectField, IntegerField,
                     BooleanField, SubmitField)
from wtforms.validators import DataRequired, Length, Optional, NumberRange

from ..models import db, Game, GameSession, GameCategory

logger = logging.getLogger(__name__)

games_admin_bp = Blueprint('games_admin', __name__, url_prefix='/admin/games')

from .access_control import require_admin as _admin_required


# ---------------------------------------------------------------------------
# Forms
# ---------------------------------------------------------------------------

class GameCategoryForm(FlaskForm):
    """Add/Edit game category form."""
    name = StringField('Category Name', validators=[DataRequired(), Length(max=80)])
    slug = StringField('Slug (URL-safe)', validators=[DataRequired(), Length(max=80)])
    sort_order = IntegerField('Sort Order', validators=[Optional()], default=0)
    submit = SubmitField('Save Category')


def _category_choices():
    """Load category choices from DB, falling back to a sensible default."""
    try:
        cats = GameCategory.query.order_by(GameCategory.sort_order, GameCategory.name).all()
        if cats:
            return [(c.slug, c.name) for c in cats]
    except Exception:
        pass
    return [('other', 'Other')]


class GameForm(FlaskForm):
    """Add/Edit game form."""
    name = StringField('Name', validators=[DataRequired(), Length(max=100)])
    slug = StringField('Slug (URL-safe)', validators=[DataRequired(), Length(max=100)])
    description = TextAreaField('Description', validators=[Optional()])
    category = SelectField('Category', choices=[], default='other', validate_choice=False)
    min_access_level = IntegerField(
        'Min Access Level',
        validators=[Optional(), NumberRange(min=0, max=255)],
        default=0,
    )
    game_type = SelectField('Game Type', choices=[
        ('builtin_web', 'Built-in Web Game'),
        ('door_dos_browser', 'In-Browser DOS Game (js-dos)'),
        ('door_dos', 'DOS Door Game (DOSBox)'),
        ('door_dosemu', 'DOS Door Game (dosemu2)'),
        ('door_mystic', 'Mystic BBS Python Game'),
        ('door_mystic_mps', 'Mystic BBS Pascal Script (.mps)'),
        ('door_native', 'Native/Script Door Game'),
        ('door_synchronet', 'Synchronet JS Game'),
        ('door_rlogin', 'A-Net Game Server (rlogin)'),
        ('door_telnet', 'Telnet Door Server (TWGS, etc.)'),
    ], validators=[DataRequired()])
    icon = StringField('Bootstrap Icon Class', validators=[Optional(), Length(max=50)])
    max_nodes = IntegerField('Max Simultaneous Players',
                             validators=[NumberRange(min=1, max=100)], default=1)
    sort_order = IntegerField('Sort Order', validators=[Optional()], default=0)
    is_active = BooleanField('Active', default=True)
    is_multiplayer = BooleanField('Multiplayer', default=False)
    web_enabled = BooleanField('Web enabled', default=True)
    terminal_enabled = BooleanField('Terminal enabled', default=True)
    share_scores_interbbs = BooleanField(
        'Share high scores via InterBBS score sharing', default=True)

    # Door game fields
    executable_path = StringField('Executable Path', validators=[Optional(), Length(max=500)])
    working_directory = StringField('Working Directory', validators=[Optional(), Length(max=500)])
    command_line_args = StringField('Command Line Args', validators=[Optional(), Length(max=500)])
    rlogin_bbs_tag = StringField('BBS Tag', validators=[Optional(), Length(max=20)])
    drop_file_type = SelectField('Drop File Type', choices=[
        ('none', 'None'),
        ('door.sys', 'DOOR.SYS'),
        ('dorinfo', 'DORINFO1.DEF'),
        ('door32.sys', 'DOOR32.SYS'),
    ], default='none', validators=[Optional()], validate_choice=False)
    drop_file_path = StringField('Drop File Path', validators=[Optional(), Length(max=500)])
    use_dosbox = BooleanField('Use DOSBox/dosemu2', default=False)
    needs_fossil_driver = BooleanField('Requires FOSSIL Driver (BNU.COM / X00.COM)', default=False)

    # Mystic Python
    mystic_script_path = StringField('Mystic Script Path (.mpy)', validators=[Optional(), Length(max=500)])

    # Synchronet JS
    synchronet_script_path = StringField('Synchronet Script Path (.js)', validators=[Optional(), Length(max=500)])
    synchronet_exec_dir = StringField('Synchronet Exec Dir', validators=[Optional(), Length(max=500)])

    # Web game
    web_game_module = StringField('Web Game Module Name', validators=[Optional(), Length(max=100)])
    web_game_url = StringField('Web Game URL', validators=[Optional(), Length(max=500)])

    submit = SubmitField('Save Game')


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@games_admin_bp.route('/')
@login_required
@_admin_required
def dashboard():
    total_games = Game.query.count()
    active_sessions = GameSession.query.filter_by(status='active').count()
    total_plays = db.session.query(db.func.sum(Game.play_count)).scalar() or 0
    top_games = (Game.query
                 .filter_by(is_active=True)
                 .order_by(Game.play_count.desc())
                 .limit(5).all())
    recent_sessions = (GameSession.query
                       .order_by(GameSession.started_at.desc())
                       .limit(10).all())
    return render_template(
        'games/admin/dashboard.html',
        total_games=total_games,
        active_sessions=active_sessions,
        total_plays=total_plays,
        top_games=top_games,
        recent_sessions=recent_sessions,
    )


@games_admin_bp.route('/list')
@login_required
@_admin_required
def list_games():
    games = Game.query.order_by(Game.sort_order, Game.name).all()
    return render_template('games/admin/list.html', games=games)


@games_admin_bp.route('/add', methods=['GET', 'POST'])
@login_required
@_admin_required
def add_game():
    form = GameForm()
    form.category.choices = _category_choices()
    if form.validate_on_submit():
        game = Game()
        _populate_game(game, form)
        db.session.add(game)
        db.session.commit()
        flash(f'Game "{game.name}" added successfully.', 'success')
        return redirect(url_for('games_admin.list_games'))
    return render_template('games/admin/form.html', form=form, game=None)


@games_admin_bp.route('/<int:game_id>/edit', methods=['GET', 'POST'])
@login_required
@_admin_required
def edit_game(game_id):
    game = Game.query.get_or_404(game_id)
    form = GameForm(obj=game)
    form.category.choices = _category_choices()
    if form.validate_on_submit():
        _populate_game(game, form)
        db.session.commit()
        flash(f'Game "{game.name}" updated.', 'success')
        return redirect(url_for('games_admin.list_games'))
    return render_template('games/admin/form.html', form=form, game=game)


@games_admin_bp.route('/<int:game_id>/delete', methods=['POST'])
@login_required
@_admin_required
def delete_game(game_id):
    game = Game.query.get_or_404(game_id)
    name = game.name
    # GameSession / GameScore have FK -> games.id without cascade. Manually
    # remove dependent rows first so SQLAlchemy doesn't raise IntegrityError
    # (which surfaces as a 500 in admin). Both relationships are dynamic
    # so we use .delete() on the queries.
    try:
        game.sessions.delete()
        game.scores.delete()
    except Exception:
        # If any dependent table is missing or the relationship doesn't exist
        # yet, fall through and let the main delete try.
        db.session.rollback()
    db.session.delete(game)
    db.session.commit()
    flash(f'Game "{name}" deleted.', 'success')
    return redirect(url_for('games_admin.list_games'))


@games_admin_bp.route('/config')
@login_required
@_admin_required
def config():
    cfg = {
        'DOSBOX_PATH': current_app.config.get('DOSBOX_PATH', ''),
        'DOSEMU_PATH': current_app.config.get('DOSEMU_PATH', ''),
        'NODEJS_PATH': current_app.config.get('NODEJS_PATH', ''),
        'MYSTIC_PYTHON_PATH': current_app.config.get('MYSTIC_PYTHON_PATH', ''),
        'GAMES_MAX_NODES': current_app.config.get('GAMES_MAX_NODES', 10),
        'GAMES_SESSION_TIMEOUT': current_app.config.get('GAMES_SESSION_TIMEOUT', 3600),
        'GAMES_DATA_DIR': current_app.config.get('GAMES_DATA_DIR', ''),
    }
    return render_template('games/admin/config.html', cfg=cfg)


@games_admin_bp.route('/sessions')
@login_required
@_admin_required
def sessions():
    active = (GameSession.query
              .filter_by(status='active')
              .order_by(GameSession.started_at.desc())
              .all())
    return render_template('games/admin/sessions.html', sessions=active)


@games_admin_bp.route('/sessions/<int:session_id>/disconnect', methods=['POST'])
@login_required
@_admin_required
def disconnect_session(session_id):
    from ..games.door_runner import terminate_session
    terminate_session(session_id)
    gs = GameSession.query.get(session_id)
    if gs:
        gs.status = 'completed'
        gs.ended_at = datetime.utcnow()
        db.session.commit()
    flash('Session terminated.', 'success')
    return redirect(url_for('games_admin.sessions'))


@games_admin_bp.route('/sessions/clear-stale', methods=['POST'])
@login_required
@_admin_required
def clear_stale_sessions():
    """Mark all active sessions as stale (bulk cleanup after restart / runaway sessions)."""
    count = GameSession.query.filter_by(status='active').update(
        {'status': 'stale', 'ended_at': datetime.utcnow()})
    db.session.commit()
    flash(f'Marked {count} session(s) as stale.', 'success')
    return redirect(url_for('games_admin.sessions'))


# ---------------------------------------------------------------------------
# TW2 — universe reset
# ---------------------------------------------------------------------------
# Trade Wars 2002 keeps every sector, player, port, planet, team, and the
# Cabal record in one JSON file at
# {INSTALL_DIR}/data/sbbs_doors/tw2/db/tw2.json (path resolved at runtime
# via ANETBBS_TW2_DB_DIR — see games/door_runner.py). On first launch
# tw2.js notices the file is missing and re-runs the universe build
# cascade. So a "reset" is just deleting that JSON plus the game.ini.
# Sysop use-cases: a botched test, exploring init bugs, wiping after
# unsupervised playtesting before a public launch.

import os as _os


def _tw2_db_dir():
    """Resolve the tw2 db dir the same way door_runner.py does."""
    install_root = current_app.config.get('INSTALL_DIR') or \
        _os.path.dirname(_os.path.dirname(
            _os.path.dirname(_os.path.abspath(__file__))))
    return _os.path.join(install_root, 'data', 'sbbs_doors', 'tw2', 'db')


def _tw2_legacy_db_dir():
    """Pre-v1.0a2.39 location, inside the source tree. We wipe both so a
    sysop installing the fix doesn't end up running off stale state."""
    install_root = current_app.config.get('INSTALL_DIR') or \
        _os.path.dirname(_os.path.dirname(
            _os.path.dirname(_os.path.abspath(__file__))))
    return _os.path.join(install_root, 'anetbbs', 'games',
                         'sbbs_doors', 'tw2', 'localhost')


def _tw2_game_ini():
    install_root = current_app.config.get('INSTALL_DIR') or \
        _os.path.dirname(_os.path.dirname(
            _os.path.dirname(_os.path.abspath(__file__))))
    return _os.path.join(install_root, 'anetbbs', 'games',
                         'sbbs_doors', 'tw2', 'game.ini')


@games_admin_bp.route('/tw2/reset-universe', methods=['POST'])
@_admin_required
def tw2_reset_universe():
    wiped = []
    for d in (_tw2_db_dir(), _tw2_legacy_db_dir()):
        if not _os.path.isdir(d):
            continue
        try:
            for f in _os.listdir(d):
                if f.endswith(('.json', '.json.tmp')):
                    _os.remove(_os.path.join(d, f))
                    wiped.append(_os.path.join(d, f))
        except OSError as exc:
            logger.warning('tw2 reset: cannot scan %s: %s', d, exc)
    ini = _tw2_game_ini()
    if _os.path.isfile(ini):
        try:
            _os.remove(ini)
            wiped.append(ini)
        except OSError as exc:
            logger.warning('tw2 reset: cannot remove %s: %s', ini, exc)
    if wiped:
        flash(f'Trade Wars universe reset — {len(wiped)} file(s) removed. '
              'Next launch will re-generate the universe.', 'success')
    else:
        flash('Nothing to reset — no TW2 state on disk yet.', 'info')
    return redirect(url_for('games_admin.dashboard'))


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _populate_game(game, form):
    """Copy form data onto a Game model instance."""
    game.name = form.name.data
    game.slug = form.slug.data
    game.description = form.description.data
    game.category = form.category.data
    game.min_access_level = form.min_access_level.data or 0
    game.game_type = form.game_type.data
    game.icon = form.icon.data
    game.max_nodes = form.max_nodes.data or 1
    game.sort_order = form.sort_order.data or 0
    game.is_active = form.is_active.data
    game.is_multiplayer = form.is_multiplayer.data
    game.web_enabled = form.web_enabled.data
    game.terminal_enabled = form.terminal_enabled.data
    game.share_scores_interbbs = form.share_scores_interbbs.data
    game.executable_path = form.executable_path.data
    game.working_directory = form.working_directory.data
    game.command_line_args = form.command_line_args.data
    game.rlogin_bbs_tag = form.rlogin_bbs_tag.data
    game.drop_file_type = form.drop_file_type.data
    game.drop_file_path = form.drop_file_path.data
    game.use_dosbox = form.use_dosbox.data
    game.needs_fossil_driver = form.needs_fossil_driver.data
    game.mystic_script_path = form.mystic_script_path.data
    game.synchronet_script_path = form.synchronet_script_path.data
    game.synchronet_exec_dir = form.synchronet_exec_dir.data
    game.web_game_module = form.web_game_module.data
    game.web_game_url = form.web_game_url.data


# ---------------------------------------------------------------------------
# Category management routes
# ---------------------------------------------------------------------------

@games_admin_bp.route('/categories')
@login_required
@_admin_required
def list_categories():
    cats = GameCategory.query.order_by(GameCategory.sort_order, GameCategory.name).all()
    return render_template('games/admin/categories.html', categories=cats)


@games_admin_bp.route('/categories/add', methods=['GET', 'POST'])
@login_required
@_admin_required
def add_category():
    form = GameCategoryForm()
    if form.validate_on_submit():
        cat = GameCategory(
            name=form.name.data,
            slug=form.slug.data,
            sort_order=form.sort_order.data or 0,
        )
        db.session.add(cat)
        db.session.commit()
        flash(f'Category "{cat.name}" added.', 'success')
        return redirect(url_for('games_admin.list_categories'))
    return render_template('games/admin/category_form.html', form=form, category=None)


@games_admin_bp.route('/categories/<int:cat_id>/edit', methods=['GET', 'POST'])
@login_required
@_admin_required
def edit_category(cat_id):
    cat = GameCategory.query.get_or_404(cat_id)
    form = GameCategoryForm(obj=cat)
    if form.validate_on_submit():
        cat.name = form.name.data
        cat.slug = form.slug.data
        cat.sort_order = form.sort_order.data or 0
        db.session.commit()
        flash(f'Category "{cat.name}" updated.', 'success')
        return redirect(url_for('games_admin.list_categories'))
    return render_template('games/admin/category_form.html', form=form, category=cat)


@games_admin_bp.route('/categories/<int:cat_id>/delete', methods=['POST'])
@login_required
@_admin_required
def delete_category(cat_id):
    cat = GameCategory.query.get_or_404(cat_id)
    name = cat.name
    db.session.delete(cat)
    db.session.commit()
    flash(f'Category "{name}" deleted.', 'success')
    return redirect(url_for('games_admin.list_categories'))
