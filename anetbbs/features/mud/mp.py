# anetbbs/features/mud/mp.py
"""Cross-process presence + chat for The Shattered Realm.

Every door session -- builtin_python games included -- is spawned as
its own separate OS subprocess by door_runner.py/builtin_runner.py for
native SSH/telnet AND the web terminal alike (confirmed real,
previously-fixed bug: see anetbbs/features/anetcraft.py's own
multiplayer module docstring). A plain module-level dict would mean
"multiplayer" silently means "solo, with extra steps" -- two players
would each get their own private, empty in-memory roster.

Fixed the same proven way ANetCRAFT's own multiplayer is: shared state
lives on disk under a data/ directory, polled/appended each loop
iteration, scoped down to just presence + chat (no delta log, no mob/
snapshot sync) since mob encounters here are INSTANCED per player, not
a shared-HP mob synced across every process -- see docs/40-mud.md's
"Multiplayer" section for why that's a deliberate scope choice, not a
missing feature.
"""
import json
import time
from pathlib import Path
from urllib.parse import quote as _urlquote

SAVE_DIR = Path(__file__).parent.parent.parent.parent / 'data' / 'doors' / 'mud'
MP_STALE_SECONDS = 10.0


def _mp_root() -> Path:
    d = SAVE_DIR / 'mp'
    (d / 'players').mkdir(parents=True, exist_ok=True)
    return d


def _chat_path() -> Path:
    return _mp_root() / 'chat.jsonl'


def safe_username(username: str) -> str:
    """Percent-encode (not strip) a username for use as a filename
    component -- same injective-encoding fix anetcraft.py's own
    _safe_username() already established (stripping instead of
    encoding let distinct usernames collide onto the same save file;
    this codebase's registration form allows characters, like space
    and '.', that a naive strip would silently collapse)."""
    return _urlquote(username, safe='') or 'player'


def _player_path(username: str) -> Path:
    return _mp_root() / 'players' / f'{safe_username(username)}.json'


def publish_presence(username: str, room_slug: str, joined_at: float):
    """Write/refresh this player's own presence file. Call once on
    entry and again whenever the player's room changes (or at least
    every loop iteration -- cheap, matches anetcraft.py's own
    every-tick-republish convention)."""
    data = {
        'username': username,
        'room_slug': room_slug,
        'joined_at': joined_at,
        'ts': time.time(),
    }
    try:
        _player_path(username).write_text(json.dumps(data))
    except OSError:
        pass


def remove_presence(username: str):
    """Call on exit (try/finally) so a disconnected player doesn't
    linger as "online" for MP_STALE_SECONDS after they've actually
    left."""
    try:
        _player_path(username).unlink(missing_ok=True)
    except OSError:
        pass


def list_players() -> dict:
    """Every currently-fresh connected player's last-published
    presence, keyed by their real (unsanitized) username -- {} if none
    are online. Includes the caller's own entry if they've already
    published one; filter it out by username if "others only" is
    wanted (matches anetcraft.py's own _mp_list_players() semantics)."""
    out = {}
    now = time.time()
    try:
        entries = list((_mp_root() / 'players').iterdir())
    except FileNotFoundError:
        return out
    for f in entries:
        if f.suffix != '.json':
            continue
        try:
            pdata = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if now - pdata.get('ts', 0) > MP_STALE_SECONDS:
            continue
        uname = pdata.get('username')
        if uname:
            out[uname] = pdata
    return out


def players_in_room(room_slug: str, exclude_username: str = '') -> list:
    """Usernames of everyone else currently in `room_slug`."""
    return sorted(
        uname for uname, pdata in list_players().items()
        if pdata.get('room_slug') == room_slug and uname != exclude_username)


def live_player_count() -> int:
    return len(list_players())


CHAT_LOG_MAX_BYTES = 256 * 1024


class ChatTail:
    """Shared MUD chat log (data/doors/mud/mp/chat.jsonl), tailed
    incrementally by every player session (fix F-1: earlier versions
    appended but nothing ever read it).

    Each line is [username, kind, text, target, room_slug]. A new session
    starts at end-of-file (no history replay). A partial trailing line is
    kept for the next poll. The log is truncated once it passes
    CHAT_LOG_MAX_BYTES; readers notice the shrink and restart at 0."""

    def __init__(self):
        try:
            self._pos = _chat_path().stat().st_size
        except OSError:
            self._pos = 0

    def send(self, username: str, kind: str, text: str, target: str = '',
             room_slug: str = ''):
        """kind: 'say'/'emote' (room-scoped), 'yell' (whole MUD), 'tell'
        (only `target`)."""
        try:
            path = _chat_path()
            if path.exists() and path.stat().st_size > CHAT_LOG_MAX_BYTES:
                path.write_text('')
            with open(path, 'a') as f:
                f.write(json.dumps([username, kind, text, target, room_slug]) + '\n')
        except OSError:
            pass

    def poll(self):
        """New (username, kind, text, target, room_slug) entries since the
        last poll on this instance."""
        path = _chat_path()
        try:
            size = path.stat().st_size
        except OSError:
            return []
        if self._pos > size:
            self._pos = 0  # log was rotated/truncated
        if size == self._pos:
            return []
        try:
            with open(path, 'rb') as f:
                f.seek(self._pos)
                chunk = f.read(min(size - self._pos, 1024 * 1024))
        except OSError:
            return []
        end = chunk.rfind(b'\n')
        if end < 0:
            return []
        self._pos += end + 1
        out = []
        for line in chunk[:end].split(b'\n'):
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            if not isinstance(parsed, list) or len(parsed) < 4:
                continue
            username, kind, text, target = parsed[:4]
            room = parsed[4] if len(parsed) > 4 else ''
            out.append((str(username), str(kind), str(text), str(target), str(room)))
        return out


def deliverable(entry, me: str, my_room: str):
    """Text to show `me` for one chat entry, or None if it isn't for them."""
    from ...core.text_safety import strip_untrusted_escapes as _clean
    username, kind, text, target, room = entry
    if username.lower() == (me or '').lower():
        return None
    who, msg = _clean(username), _clean(text)
    if kind == 'yell':
        return f"{who} yells, \"{msg}\""
    if kind == 'tell':
        if target.lower() != (me or '').lower():
            return None
        return f"{who} tells you, \"{msg}\""
    if kind in ('say', 'emote') and room and room == my_room:
        return f"{who} {msg}" if kind == 'emote' else f"{who} says, \"{msg}\""
    return None
