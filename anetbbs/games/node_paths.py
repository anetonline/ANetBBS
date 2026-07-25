"""Per-node temp directory layout and token substitution for door games.

Classic BBSes (Synchronet, Mystic, RA, etc.) give each active node its
own scratch directory where drop files (DOOR.SYS / DOOR32.SYS / DORINFO)
get written before a door launches. Doors expect the drop file at a
predictable path tied to the node number, so two users on different
nodes can play the same door at once without stomping on each other's
drop file.

This module owns:

  - The on-disk layout: ``<DATA_DIR>/temp/node<N>/`` (one per active node,
    1..BBS_NODES). Created automatically at app startup so a fresh
    install is ready to run doors immediately.

  - The token vocabulary sysops can use in `Game.executable_path`,
    `Game.working_directory`, `Game.command_line_args`, and
    `Game.drop_file_path`. We accept Synchronet-style ``%f``, ``%n``,
    etc. AND Mystic-style ``%P``, ``%N``, etc. — pick whichever your
    door expects.
"""
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def temp_root() -> str:
    """Return the absolute path to ``<DATA_DIR>/temp``. Falls back to
    ``/tmp/anetbbs-temp`` if config isn't loaded yet (which only happens
    in unit tests)."""
    try:
        from ..config import get_config
        cfg = get_config()
        data_dir = getattr(cfg, 'DATA_DIR', None)
        if data_dir:
            return os.path.join(str(data_dir), 'temp')
    except Exception:
        pass
    return '/tmp/anetbbs-temp'  # nosec B108 -- scratch dir fallback only, DATA_DIR-based path used above when available


def node_dir(node_number: int) -> str:
    """Return the absolute path to a node's scratch directory.
    Always returns a path with a trailing separator (Mystic ``%P``
    expects that — ``%Pdoor32.sys`` becomes ``/.../node1/door32.sys``)."""
    return os.path.join(temp_root(), f'node{int(node_number)}') + os.sep


def ensure_node_dirs(node_count: Optional[int] = None) -> None:
    """Create temp/node1..temp/nodeN at startup. Idempotent."""
    if node_count is None:
        try:
            from ..config import get_config
            node_count = int(getattr(get_config(), 'BBS_NODES', 8))
        except Exception:
            node_count = 8
    root = temp_root()
    try:
        os.makedirs(root, exist_ok=True)
        for n in range(1, max(1, int(node_count)) + 1):
            os.makedirs(os.path.join(root, f'node{n}'), exist_ok=True)
    except OSError as exc:
        logger.warning('Could not create per-node temp dirs under %s: %s',
                       root, exc)


# ---------------------------------------------------------------------------
# Token substitution
# ---------------------------------------------------------------------------
# Classic BBS drop-file / command-line token vocabulary.
#
# We accept BOTH Synchronet's lowercase % codes (well-documented at
# wiki.synchro.net/ref:xtrn) AND Mystic's uppercase % codes (from
# mysticbbs.com docs). Sysops can mix them freely — `%f` and `%Pdoor32.sys`
# both yield the right path. The substitution is single-pass: tokens are
# replaced left-to-right, so `%P%n` correctly gives the node dir followed
# by the node number.
#
# Tokens are case-sensitive: `%f` (Synchronet drop file) ≠ `%F` (unused).
# That matches Synchronet's actual behavior — `%c` and `%C` are different
# variables.

# Synchronet xtrn % codes — reference: src/sbbs3/xtrndefs.h, wiki:ref:xtrn
# Documented in xtrn.cnf: "REPLACEMENT KEYWORDS"
_SYNC_TOKENS = {
    '%a',  # account number / user number
    '%c',  # connection description
    '%d',  # baud rate
    '%e',  # node number (synonym of %n in some doors)
    '%f',  # drop file full path
    '%g',  # SSH session active flag
    '%h',  # socket handle
    '%i',  # IP address
    '%j',  # data dir (DATA_DIR)
    '%k',  # ctrl dir
    '%l',  # lines per screen
    '%m',  # minutes left
    '%n',  # node number
    '%o',  # sysop name
    '%p',  # user phone
    '%r',  # user real name
    '%s',  # 'system' name (BBS name)
    '%t',  # time left in seconds
    '%u',  # username / alias
    '%w',  # working directory
    '%y',  # current date
    '%z',  # time of day
    '%!',  # exec dir
    '%%',  # literal %
}

# Mystic % codes — reference: docs/mystic/MENU.MAC, MUTIL.PDF
# Mystic uses uppercase letters where Synchronet uses lowercase. The
# overlap means `%U` is Mystic's username, `%u` is Synchronet's. Both
# resolve to the same value; we accept both.
_MYSTIC_TOKENS = {
    '%P',  # path to node temp dir (with trailing /)
    '%N',  # node number
    '%M',  # minutes left
    '%U',  # username
    '%T',  # temp dir base (= temp_root())
    '%H',  # user handle (alias)
    '%R',  # user real name
    '%E',  # user email
    '%S',  # security level
    '%L',  # location
}


def build_token_context(*, user, node_number: int, drop_file_path: str = '',
                        minutes_left: int = 60, bbs_name: str = 'ANetBBS',
                        sysop_name: str = 'Sysop',
                        ip_address: str = '0.0.0.0',
                        data_dir: str = '',
                        ctrl_dir: str = '',
                        exec_dir: str = '',
                        working_dir: str = '') -> dict:
    """Build the substitution map used by :func:`expand_tokens`.

    `user` may be a SQLAlchemy User row OR a plain dict (the BBS
    terminal stores user as a dict on the session object). We use the
    same `_u()` accessor pattern as `dropfile.py`.
    """
    def _u(field, default=''):
        # Real injection gap found in a full access-control audit:
        # these values (username/display_name/email/location) are
        # user-controlled and get spliced verbatim into generated
        # DOSBox .conf / dosemu2 .bat autoexec content downstream
        # (door_runner.py's _build_dos_command/_build_dosemu_command) --
        # an embedded CR/LF let a crafted profile field inject extra
        # DOS/DOSBox commands into that boot sequence. Stripping CR/LF
        # here, at the one place all of them funnel through, closes it
        # for every %-token that carries user data, not just username.
        if user is None:
            return default
        if isinstance(user, dict):
            val = user.get(field, default)
        else:
            val = getattr(user, field, default)
        if isinstance(val, str):
            val = val.replace('\r', '').replace('\n', '')
        return val

    nd = node_dir(node_number)
    sec = 255 if _u('is_admin') else 50
    n = str(int(node_number))
    return {
        # Synchronet tokens
        '%a': str(_u('id', 0)),
        '%c': 'TELNET',
        '%d': '0',                      # baud (0 = local)
        '%e': n,
        '%f': drop_file_path or '',
        '%g': '0',
        '%h': '0',
        '%i': ip_address or '0.0.0.0',
        '%j': data_dir,
        '%k': ctrl_dir,
        '%l': '25',
        '%m': str(int(minutes_left)),
        '%n': n,
        '%o': sysop_name,
        '%p': '',
        '%r': str(_u('display_name') or _u('username') or ''),
        '%s': bbs_name,
        '%t': str(int(minutes_left) * 60),
        '%u': str(_u('username') or 'guest'),
        '%w': working_dir,
        '%y': '',                       # filled by caller if needed
        '%z': '',
        '%!': exec_dir,
        '%%': '%',                      # literal % (last so it doesn't
                                        # interfere with the loop)
        # Mystic tokens
        '%P': nd,
        '%N': n,
        '%M': str(int(minutes_left)),
        '%U': str(_u('username') or 'guest'),
        '%T': temp_root() + os.sep,
        '%H': str(_u('username') or 'guest'),
        '%R': str(_u('display_name') or _u('username') or ''),
        '%E': str(_u('email') or ''),
        '%S': str(sec),
        '%L': str(_u('location') or 'Unknown'),
    }


def expand_tokens(s: str, ctx: dict) -> str:
    """Replace every Synchronet / Mystic % token in *s* using *ctx*.

    Matches longest tokens first (Mystic uppercase + special `%%` /
    `%!`) before single-char lowercase ones, so e.g. `%PP` becomes the
    node dir followed by literal `P`. Unknown tokens are left intact —
    so a literal `%X` in a path won't get mangled.
    """
    if not s or '%' not in s:
        return s
    # Iterate in a defined order: 2-char tokens first (none in our vocab
    # are longer than 2), so the loop below correctly distinguishes
    # `%%` from `%`. `dict` preserves insertion order; ours puts `%%`
    # last, so handle multi-char ones explicitly first.
    out = s
    # Replace `%%` first to a sentinel, otherwise `%n`-style would
    # eat the second `%`.
    sentinel = '\x00\x01PCT\x01\x00'
    out = out.replace('%%', sentinel)
    # Now apply every token. Order matters for tokens that share a prefix;
    # we don't have any in this vocab (`%P` is unique vs `%p`), but we
    # sort longest-first to be safe if more get added.
    for tok in sorted(ctx.keys(), key=len, reverse=True):
        if tok == '%%':
            continue
        if tok in out:
            out = out.replace(tok, str(ctx[tok]))
    out = out.replace(sentinel, '%')
    return out


# Re-export for sysop-facing docs / admin form help text.
ALL_TOKENS = sorted(_SYNC_TOKENS | _MYSTIC_TOKENS)
