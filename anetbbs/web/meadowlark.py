# anetbbs/web/meadowlark.py
"""
Meadowlark Valley — town/farm-builder web game, server-side save API +
co-op relay.

REST routes:
    GET    /games/meadowlark/saves          — summaries of all 3 slots for the picker UI
    GET    /games/meadowlark/state/<slot>   — full saved state_json for one slot (404 if empty)
    POST   /games/meadowlark/state/<slot>   — upsert a slot's state_json
    DELETE /games/meadowlark/state/<slot>   — clear a slot

The migrated client (anetbbs/static/js/meadowlark/state.js) still builds
the exact same JSON blob serializeState()/deserializeState() always
produced/consumed in the standalone version -- only the storage backend
changed, from localStorage to these routes, so the save FORMAT itself
needed no changes. No import/export text-blob routes exist here on
purpose: those were file-based/localStorage-era conveniences that don't
make sense once a save is already tied to the user's account.

Co-op (SocketIO namespace /mlv-coop):
    host_room       {}                       -> room_hosted {code}
    join_room_req   {code}                   -> room_joined {code, host_username} | room_error {message}
    leave_room_req  {code}                   -> (no reply; room peers get member_left/host_left)
    state_sync      {code, state_json}       -> relayed to room (host-only; see _rooms)
    action          {code, type, payload}    -> relayed to room (any member)

One player (whoever called host_room) is the single source of simulation
truth -- everyone else's client just renders whatever state_sync last
delivered and relays their own build/bulldoze/tax clicks as `action`
events for the host to apply. The server itself is a pure relay: it
knows nothing about Meadowlark's game rules, only which sids are in which
room, so it can't validate an action's game-legality -- that's fine for
a "build with friends you trust" v1 co-op, same trust model as any other
same-room multiplayer toy.

Rooms are an in-memory dict (_rooms), not persisted to the DB -- they're
meant to be "hop in and build together right now" sessions, not durable
state, and are lost on a server restart or (in a multi-worker gunicorn
deployment) only visible to whichever worker process handles a given
socket -- fine for ANetBBS's typical single-worker eventlet deployment,
worth revisiting if that ever changes.
"""
import secrets
import string
from datetime import datetime

from flask import Blueprint, jsonify, request, abort
from flask_login import login_required, current_user
from flask_socketio import join_room, leave_room, emit

from ..models import db, MeadowlarkSave
from ..web_app import socketio

meadowlark_bp = Blueprint('meadowlark', __name__, url_prefix='/games/meadowlark')

SAVE_SLOTS = 3


def _valid_slot(slot):
    return isinstance(slot, int) and 1 <= slot <= SAVE_SLOTS


@meadowlark_bp.route('/saves')
@login_required
def list_saves():
    rows = MeadowlarkSave.query.filter_by(user_id=current_user.id).all()
    by_slot = {r.slot: r for r in rows}
    saves = []
    for slot in range(1, SAVE_SLOTS + 1):
        r = by_slot.get(slot)
        saves.append({
            'slot': slot,
            'empty': r is None,
            'day': r.day if r else None,
            'population': r.population if r else None,
            'season': r.season if r else None,
            'updated_at': r.updated_at.isoformat() if r and r.updated_at else None,
        })
    return jsonify({'saves': saves})


@meadowlark_bp.route('/state/<int:slot>')
@login_required
def get_state(slot):
    if not _valid_slot(slot):
        abort(400)
    row = MeadowlarkSave.query.filter_by(user_id=current_user.id, slot=slot).first()
    if row is None:
        abort(404)
    return jsonify({'state_json': row.state_json})


@meadowlark_bp.route('/state/<int:slot>', methods=['POST'])
@login_required
def save_state(slot):
    if not _valid_slot(slot):
        abort(400)
    data = request.get_json(silent=True) or {}
    state_json = data.get('state_json')
    if not state_json or not isinstance(state_json, str):
        return jsonify({'error': 'state_json required'}), 400

    row = MeadowlarkSave.query.filter_by(user_id=current_user.id, slot=slot).first()
    if row is None:
        row = MeadowlarkSave(user_id=current_user.id, slot=slot)
        db.session.add(row)
    row.state_json = state_json
    row.day = int(data.get('day') or 1)
    row.population = int(data.get('population') or 0)
    row.season = int(data.get('season') or 0)
    row.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True})


@meadowlark_bp.route('/state/<int:slot>', methods=['DELETE'])
@login_required
def delete_state(slot):
    if not _valid_slot(slot):
        abort(400)
    row = MeadowlarkSave.query.filter_by(user_id=current_user.id, slot=slot).first()
    if row is not None:
        db.session.delete(row)
        db.session.commit()
    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# SocketIO — namespace /mlv-coop
# ---------------------------------------------------------------------------

_ROOM_CODE_ALPHABET = string.ascii_uppercase.replace('O', '').replace('I', '') + '23456789'
_ROOM_CODE_LEN = 5

# code -> {'host_sid': sid, 'members': {sid: username}}
_rooms = {}
# sid -> code, so disconnect (which gets no other context) can find the room
_sid_room = {}


def _new_room_code():
    while True:
        code = ''.join(secrets.choice(_ROOM_CODE_ALPHABET) for _ in range(_ROOM_CODE_LEN))
        if code not in _rooms:
            return code


def _leave_current_room(sid):
    """Shared cleanup for an explicit leave_room_req and an unexpected
    disconnect -- same effect either way, just a different trigger."""
    code = _sid_room.pop(sid, None)
    if not code or code not in _rooms:
        return
    room = _rooms[code]
    username = room['members'].pop(sid, None)
    leave_room(code, sid=sid, namespace='/mlv-coop')
    if sid == room['host_sid']:
        # Host leaving ends the room for everyone -- there's no promotion
        # to a new host in this v1, since only the host's browser has the
        # authoritative simulation running.
        emit('host_left', {}, room=code, namespace='/mlv-coop')
        del _rooms[code]
    else:
        # Host is always still a member of its own room's `members` dict
        # (added in host_room()), so this branch only runs for a
        # non-host leaving -- room['members'] is guaranteed non-empty
        # (the host, at minimum) whenever we reach here.
        emit('member_left', {'username': username}, room=code, namespace='/mlv-coop')


@socketio.on('connect', namespace='/mlv-coop')
def coop_connect():
    if not current_user.is_authenticated:
        return False


@socketio.on('disconnect', namespace='/mlv-coop')
def coop_disconnect():
    _leave_current_room(request.sid)


@socketio.on('host_room', namespace='/mlv-coop')
def coop_host_room(_data=None):
    if not current_user.is_authenticated:
        return
    sid = request.sid
    _leave_current_room(sid)  # a client can only be in one room at a time
    code = _new_room_code()
    _rooms[code] = {'host_sid': sid, 'members': {sid: current_user.username}}
    _sid_room[sid] = code
    join_room(code)
    emit('room_hosted', {'code': code})


@socketio.on('join_room_req', namespace='/mlv-coop')
def coop_join_room(data):
    if not current_user.is_authenticated:
        return
    code = (data or {}).get('code', '').strip().upper()
    room = _rooms.get(code)
    if not room:
        emit('room_error', {'message': f'No co-op room with code {code}.'})
        return
    sid = request.sid
    _leave_current_room(sid)
    room['members'][sid] = current_user.username
    _sid_room[sid] = code
    join_room(code)
    emit('room_joined', {'code': code, 'host_username': room['members'][room['host_sid']]})
    emit('member_joined', {'username': current_user.username}, room=code, include_self=False)


@socketio.on('leave_room_req', namespace='/mlv-coop')
def coop_leave_room(_data=None):
    _leave_current_room(request.sid)


@socketio.on('state_sync', namespace='/mlv-coop')
def coop_state_sync(data):
    """Full-state broadcast -- host only. Any non-host sender is ignored
    outright rather than trusted, since a stray/hostile state_sync from a
    guest would silently overwrite everyone else's town."""
    data = data or {}
    code = data.get('code', '')
    room = _rooms.get(code)
    if not room or room['host_sid'] != request.sid:
        return
    emit('state_sync', {'state_json': data.get('state_json')}, room=code, include_self=False)


@socketio.on('action', namespace='/mlv-coop')
def coop_action(data):
    """Relay a build/bulldoze/tax-change action to the rest of the room.
    Any current member may send one (the host applies it locally the same
    way it applies its own actions); the server doesn't interpret it."""
    data = data or {}
    code = data.get('code', '')
    room = _rooms.get(code)
    if not room or request.sid not in room['members']:
        return
    emit('action', {'type': data.get('type'), 'payload': data.get('payload'),
                     'username': room['members'][request.sid]},
         room=code, include_self=False)
