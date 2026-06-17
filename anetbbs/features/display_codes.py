"""
Synchronet @-codes and Mystic |-codes for ANSI screens.

Sysops can drop these tokens into welcome / menu / bulletin ANSI files
and we substitute them with live values at render time.

Refs:
- Synchronet: https://wiki.synchro.net/custom:atcodes
- Mystic:     https://wiki.mysticbbs.com/doku.php?id=displaycodes

Only a useful core subset is wired up — adding more is a one-line
change in the resolver tables. Unknown codes are left in place so the
sysop can spot them and ask for support.
"""
from __future__ import annotations

import re
from datetime import datetime


# --- Resolvers ---------------------------------------------------------------
#
# Each resolver is `lambda ctx -> str`. `ctx` is a dict with keys:
#   user      — User row (may be None for pre-login screens)
#   bbs_name  — string, BBS_NAME config
#   sysop     — string, sysop username (config or fallback)
#   node      — int, node number (1 if unknown)
#   now       — datetime
# Resolvers must always return a string (use empty for missing values).

def _u(ctx, attr, default=''):
    u = ctx.get('user')
    if u is None:
        return default
    # session.user is a dict from user_manager._user_to_dict; support both
    # dict and ORM-object callers.
    if isinstance(u, dict):
        val = u.get(attr, default)
    else:
        val = getattr(u, attr, default)
    return str(val if val is not None and val != '' else default)


SYNCHRONET_AT = {
    # Identity
    'USER':      lambda c: _u(c, 'username'),
    'ALIAS':     lambda c: _u(c, 'username'),
    'NAME':      lambda c: _u(c, 'display_name') or _u(c, 'username'),
    'REAL':      lambda c: _u(c, 'display_name') or _u(c, 'username'),
    'FIRST':     lambda c: (_u(c, 'display_name') or _u(c, 'username')).split()[0:1] and (_u(c, 'display_name') or _u(c, 'username')).split()[0] or '',
    'HANDLE':    lambda c: _u(c, 'username'),
    'EMAIL':     lambda c: _u(c, 'email'),
    'LOCATION':  lambda c: _u(c, 'location'),
    'AGE':       lambda c: '',
    'SEX':       lambda c: '',
    # System
    'CLS':       lambda c: '\x1b[2J\x1b[H',
    'BBS':       lambda c: c.get('bbs_name', ''),
    'SYSOP':     lambda c: c.get('sysop', ''),
    'NODE':      lambda c: str(c.get('node', 1)),
    'VER':       lambda c: c.get('version', 'v1.0a'),
    'VERSION':   lambda c: c.get('version', 'v1.0a'),
    # Time
    'TIME':      lambda c: c['now'].strftime('%H:%M'),
    'DATE':      lambda c: c['now'].strftime('%Y-%m-%d'),
    'DAY':       lambda c: c['now'].strftime('%A'),
    'TIMELEFT':  lambda c: '',  # not modelled
    # Stats
    'CALLS':     lambda c: str(_u(c, 'login_count', 0)),
    'POSTS':     lambda c: '',
    'SECURITY':  lambda c: ('100' if _u(c, 'is_admin') not in ('', 'False', '0', 'None') else '50'),
    'LASTON':    lambda c: '',
}


MYSTIC_PIPE_NAMED = {
    # Mystic doc: https://wiki.mysticbbs.com/doku.php?id=displaycodes
    # |UN user-name, |UA alias, |UR real, |BN bbs, |SN sysop, |DT date,
    # |TM time, |LO last on, |VL version, |ND node, |LF linefeed
    'UN': lambda c: _u(c, 'username'),
    'UA': lambda c: _u(c, 'username'),
    'UR': lambda c: _u(c, 'display_name') or _u(c, 'username'),
    'BN': lambda c: c.get('bbs_name', ''),
    'SN': lambda c: c.get('sysop', ''),
    'DT': lambda c: c['now'].strftime('%Y-%m-%d'),
    'TM': lambda c: c['now'].strftime('%H:%M'),
    'LO': lambda c: '',
    'VL': lambda c: c.get('version', 'v1.0a'),
    'ND': lambda c: str(c.get('node', 1)),
    'LF': lambda c: '\r\n',
}


_AT_RE = re.compile(r'@([A-Z][A-Z0-9_]{0,15})@')
# Parametric @CODE:value@ codes (e.g. @BPS:19200@) — strip these rather than
# displaying literal text; most are Synchronet rendering hints we don't model.
_AT_PARAM_RE = re.compile(r'@[A-Z][A-Z0-9_]{0,15}:[^@]{0,32}@')
_BPS_RE = re.compile(r'@BPS:(\d+)@')
# Mystic named codes: 2-letter uppercase, after a `|` not followed by a digit
# (color codes like |07 are 2-digit and handled elsewhere by _pipe_to_ansi).
_MY_NAMED_RE = re.compile(r'\|([A-Z]{2})\b')


def apply(text: str, *, user=None, bbs_name: str = '', sysop: str = '',
          node: int = 1, version: str = '') -> str:
    """Substitute Synchronet @-codes and Mystic |-named codes in `text`.

    Color pipe codes like ``|07`` are LEFT ALONE — call _pipe_to_ansi after
    this to render them. Unknown codes pass through unchanged.
    """
    if not text or ('@' not in text and '|' not in text):
        return text

    ctx = {
        'user': user,
        'bbs_name': bbs_name or '',
        'sysop': sysop or '',
        'node': int(node or 1),
        'version': version or '',
        'now': datetime.now(),
    }

    def _at_sub(m):
        key = m.group(1)
        fn = SYNCHRONET_AT.get(key)
        if fn is None:
            return m.group(0)              # leave unknown @-codes visible
        try:
            return fn(ctx)
        except Exception:
            return ''

    def _my_sub(m):
        key = m.group(1)
        fn = MYSTIC_PIPE_NAMED.get(key)
        if fn is None:
            return m.group(0)
        try:
            return fn(ctx)
        except Exception:
            return ''

    text = _AT_PARAM_RE.sub('', text)      # strip @BPS:19200@ etc. before token sub
    text = _AT_RE.sub(_at_sub, text)
    text = _MY_NAMED_RE.sub(_my_sub, text)
    return text


def extract_bps(text: str) -> int | None:
    """Return the baud rate from the first @BPS:NNNN@ code in *text*, or None.

    Call this BEFORE apply() — apply() strips the code.
    Clamps to 300–56000 so a typo can't produce an absurd delay.
    """
    if not text or '@' not in text:
        return None
    m = _BPS_RE.search(text)
    if not m:
        return None
    return max(300, min(56000, int(m.group(1))))
