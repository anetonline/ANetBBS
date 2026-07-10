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
    user_level = _get(user, 'access_level', 10) or 10
    return user_level >= (min_level or 0)


def _get(obj, key, default):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)
