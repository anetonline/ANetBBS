# anetbbs/web/darkforces.py
"""
ANetDarkForces — first-person raycasting shooter web game, server-side
save API.

REST routes:
    GET    /games/darkforces/saves          — summaries of all 3 slots for the picker UI
    GET    /games/darkforces/state/<slot>   — full saved state_json for one slot (404 if empty)
    POST   /games/darkforces/state/<slot>   — upsert a slot's state_json
    DELETE /games/darkforces/state/<slot>   — clear a slot

The migrated client (anetbbs/static/js/darkforces/state.js) still builds
the exact same JSON blob serializeState()/deserializeState() always
produced/consumed in the standalone version -- only the storage backend
changed, from localStorage to these routes, so the save FORMAT itself
needed no changes. No co-op here (unlike Meadowlark Valley) -- this
game's checkpoint-based single-player save model doesn't have an
equivalent "build together" mode to relay.
"""
from datetime import datetime

from flask import Blueprint, jsonify, request, abort
from flask_login import login_required, current_user

from ..models import db, DarkForcesSave

darkforces_bp = Blueprint('darkforces', __name__, url_prefix='/games/darkforces')

SAVE_SLOTS = 3


def _valid_slot(slot):
    return isinstance(slot, int) and 1 <= slot <= SAVE_SLOTS


@darkforces_bp.route('/saves')
@login_required
def list_saves():
    rows = DarkForcesSave.query.filter_by(user_id=current_user.id).all()
    by_slot = {r.slot: r for r in rows}
    saves = []
    for slot in range(1, SAVE_SLOTS + 1):
        r = by_slot.get(slot)
        saves.append({
            'slot': slot,
            'empty': r is None,
            'level_name': r.level_name if r else None,
            'level_index': r.level_index if r else None,
            'player_level': r.player_level if r else None,
            'updated_at': r.updated_at.isoformat() if r and r.updated_at else None,
        })
    return jsonify({'saves': saves})


@darkforces_bp.route('/state/<int:slot>')
@login_required
def get_state(slot):
    if not _valid_slot(slot):
        abort(400)
    row = DarkForcesSave.query.filter_by(user_id=current_user.id, slot=slot).first()
    if row is None:
        abort(404)
    return jsonify({'state_json': row.state_json})


@darkforces_bp.route('/state/<int:slot>', methods=['POST'])
@login_required
def save_state(slot):
    if not _valid_slot(slot):
        abort(400)
    data = request.get_json(silent=True) or {}
    state_json = data.get('state_json')
    if not state_json or not isinstance(state_json, str):
        return jsonify({'error': 'state_json required'}), 400

    row = DarkForcesSave.query.filter_by(user_id=current_user.id, slot=slot).first()
    if row is None:
        row = DarkForcesSave(user_id=current_user.id, slot=slot)
        db.session.add(row)
    row.state_json = state_json
    row.level_name = str(data.get('level_name') or '')[:64]
    row.level_index = int(data.get('level_index') or 0)
    row.player_level = int(data.get('player_level') or 1)
    row.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True})


@darkforces_bp.route('/state/<int:slot>', methods=['DELETE'])
@login_required
def delete_state(slot):
    if not _valid_slot(slot):
        abort(400)
    row = DarkForcesSave.query.filter_by(user_id=current_user.id, slot=slot).first()
    if row is not None:
        db.session.delete(row)
        db.session.commit()
    return jsonify({'ok': True})
