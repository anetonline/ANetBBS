# anetbbs/features/access_control.py
"""
Shared read-access gate: "can this user see/read an item at its
min_access_level / is_sysop_only gate."

Before this existed, every feature (echomail areas, RSS feeds, games,
file areas, boards...) re-implemented this same numeric-level + sysop-only
comparison inline, each with its own "or 10" / "or 0" default fallback and
its own (inconsistent) decision about whether an admin bypasses the level
check. This module is the single place that comparison lives now.

Deliberately narrow scope -- this function answers exactly one question.
It does NOT know about `min_write_level` (a different, still-separate
concept: can this user post/upload, not just read) or `UserAccessFlags`
(per-user feature suspension booleans like no_echomail/no_mrc). Folding
those in as extra kwargs would turn a safe mechanical dedup into a
behavior-unifying rewrite -- every call site would need to remember which
kwargs reproduce its exact previous behavior. Keep them separate.

No Flask/DB import here on purpose -- this needs to work against a plain
ORM User row (web) today, and a `session.user` dict (terminal) in a later
pass, without a rewrite.
"""


def evaluate_access(user, min_level=0, *, is_sysop_only=False, bypass_admin=True):
    """Can `user` see/read an item gated at `min_level` / `is_sysop_only`?

    user: an object/dict with `access_level` and `is_admin` attributes (or
          keys, if a plain dict) -- None is treated as an anonymous user
          (access_level 0, not an admin).
    min_level: the item's minimum required access_level (None treated as 0).
    is_sysop_only: True if the item is gated to admins regardless of level.
    bypass_admin: if True (most call sites), an admin always passes,
          regardless of level/sysop_only. If False, admins are evaluated
          exactly like anyone else -- some existing call sites (e.g. RSS
          feed / game listings) never special-cased admins, and migrating
          them must not silently start doing so.
    """
    is_admin = _get(user, 'is_admin', False)
    if bypass_admin and is_admin:
        return True
    if is_sysop_only and not is_admin:
        return False
    # Real bug found in a full auth/access-control audit: this function's
    # OWN docstring promises "None is treated as an anonymous user
    # (access_level 0...)", but the fallback here was `_get(user,
    # 'access_level', 10) or 10` -- and the REAL anonymous object every
    # caller actually passes is Flask-Login's stock AnonymousUserMixin
    # (no custom subclass registered), which is NOT None and has no
    # `access_level` attribute at all, so the lookup fell through to the
    # default 10 for every logged-out visitor. Since every gated model's
    # min_access_level defaults to 10 ("0=public, 10=registered"), an
    # anonymous visitor silently passed the "registered users only" gate
    # on every board/area/feed using the default -- directly defeating
    # the fix boards.py's own _check_board_access() docstring describes
    # closing ("a sysop restricting a board... still left it fully
    # readable... to anonymous visitors"): that fix never actually
    # worked for the common case. Detect anonymous via `is_authenticated`
    # (Flask-Login's own duck-typed signal: False on AnonymousUserMixin,
    # True on UserMixin) rather than an attribute-presence check, so a
    # genuinely-authenticated caller missing `access_level` for some
    # other reason still gets the old "assume registered" default
    # instead of being wrongly treated as anonymous. A plain dict with no
    # `is_authenticated` key (the terminal/session.user case this module
    # is designed to also support) defaults to "not anonymous", matching
    # existing behavior for that caller shape.
    is_anonymous = user is None or not _get(user, 'is_authenticated', True)
    if is_anonymous:
        return 0 >= (min_level or 0)
    user_level = _get(user, 'access_level', None)
    if user_level is None:
        user_level = 10
    return user_level >= (min_level or 0)


def _get(obj, key, default):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def resolve_post_name(user, require_real_name):
    """Resolve the From:/author name to use when `user` posts to an
    echomail area or netmail destination.

    Some FTN networks/areas have a real-world policy requiring the
    poster's actual name rather than a handle/alias. When
    require_real_name is True and the user hasn't set one, this is a
    hard block (not a silent fallback to handle) -- matches the "loud
    and clear rejection" shape evaluate_access() already uses above,
    rather than quietly violating the area/network's own policy.

    Returns (name, error): on success, (resolved_name, None). On
    failure, (None, user_facing_error_message) -- callers must check
    for a non-None error and reject the post, not just fall through.

    user: an object/dict with `real_name`, `echomail_name_pref`,
          `display_name`, `username` attributes (or keys) -- same
          dict-or-ORM-object duck typing as evaluate_access() above,
          via the same _get() helper.
    """
    real_name = (_get(user, 'real_name', '') or '').strip()
    if require_real_name:
        if not real_name:
            return None, ('This area/network requires your real name for '
                          'posting -- set it in your Profile first.')
        return real_name, None
    pref = _get(user, 'echomail_name_pref', 'handle') or 'handle'
    if pref == 'real_name' and real_name:
        return real_name, None
    handle = (_get(user, 'display_name', '') or _get(user, 'username', '')
             or 'Anonymous')
    return handle, None
