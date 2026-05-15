# anetbbs/web/games_admin.py
"""
Admin Games blueprint — manage the ANetBBS Game Center.
"""
import logging
from datetime import datetime

from flask import (Blueprint, render_template, redirect, url_for,
                   flash, abort, current_app)
from flask_login import login_required, current_user
from flask_wtf import FlaskForm
from wtforms import (StringField, TextAreaField, SelectField, IntegerField,
                     BooleanField, SubmitField)
from wtforms.validators import DataRequired, Length, Optional, NumberRange

from ..models import db, Game, GameSession

logger = logging.getLogger(__name__)

games_admin_bp = Blueprint('games_admin', __name__, url_prefix='/admin/games')


def _admin_required(fn):
    """Decorator — admins only. Replaces an older in-body abort() pattern
    that ran AFTER the view body had started executing (a window where a
    non-admin authenticated user could trigger DB reads before the abort)."""
    from functools import wraps
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return fn(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Forms
# ---------------------------------------------------------------------------

class GameForm(FlaskForm):
    """Add/Edit game form."""
    name = StringField('Name', validators=[DataRequired(), Length(max=100)])
    slug = StringField('Slug (URL-safe)', validators=[DataRequired(), Length(max=100)])
    description = TextAreaField('Description', validators=[Optional()])
    category = SelectField('Category', choices=[
        ('action', 'Action'),
        ('classic', 'Classic DOS'),
        ('other', 'Other'),
        ('puzzle', 'Puzzle'),
        ('rpg', 'RPG'),
        ('strategy', 'Strategy'),
    ], default='other', validate_choice=False)
    game_type = SelectField('Game Type', choices=[
        ('builtin_web', 'Built-in Web Game'),
        ('door_dos', 'DOS Door Game (DOSBox)'),
        ('door_mystic', 'Mystic BBS Python Game'),
        ('door_mystic_mps', 'Mystic BBS Pascal Script (.mps)'),
        ('door_native', 'Native/Script Door Game'),
        ('door_synchronet', 'Synchronet JS Game'),
        ('door_rlogin', 'A-Net Game Server (rlogin)'),
    ], validators=[DataRequired()])
    icon = StringField('Bootstrap Icon Class', validators=[Optional(), Length(max=50)])
    max_nodes = IntegerField('Max Simultaneous Players',
                             validators=[NumberRange(min=1, max=100)], default=1)
    sort_order = IntegerField('Sort Order', validators=[Optional()], default=0)
    is_active = BooleanField('Active', default=True)
    is_multiplayer = BooleanField('Multiplayer', default=False)

    # Door game fields
    executable_path = StringField('Executable Path', validators=[Optional(), Length(max=500)])
    working_directory = StringField('Working Directory', validators=[Optional(), Length(max=500)])
    command_line_args = StringField('Command Line Args', validators=[Optional(), Length(max=500)])
    drop_file_type = SelectField('Drop File Type', choices=[
        ('none', 'None'),
        ('door.sys', 'DOOR.SYS'),
        ('dorinfo', 'DORINFO1.DEF'),
        ('door32.sys', 'DOOR32.SYS'),
    ], default='none', validators=[Optional()], validate_choice=False)
    drop_file_path = StringField('Drop File Path', validators=[Optional(), Length(max=500)])
    use_dosbox = BooleanField('Use DOSBox/dosemu2', default=False)

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


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _populate_game(game, form):
    """Copy form data onto a Game model instance."""
    game.name = form.name.data
    game.slug = form.slug.data
    game.description = form.description.data
    game.category = form.category.data
    game.game_type = form.game_type.data
    game.icon = form.icon.data
    game.max_nodes = form.max_nodes.data or 1
    game.sort_order = form.sort_order.data or 0
    game.is_active = form.is_active.data
    game.is_multiplayer = form.is_multiplayer.data
    game.executable_path = form.executable_path.data
    game.working_directory = form.working_directory.data
    game.command_line_args = form.command_line_args.data
    game.drop_file_type = form.drop_file_type.data
    game.drop_file_path = form.drop_file_path.data
    game.use_dosbox = form.use_dosbox.data
    game.mystic_script_path = form.mystic_script_path.data
    game.synchronet_script_path = form.synchronet_script_path.data
    game.synchronet_exec_dir = form.synchronet_exec_dir.data
    game.web_game_module = form.web_game_module.data
    game.web_game_url = form.web_game_url.data
