# anetbbs/web/access_control.py
"""
Shared admin-gating for Flask routes.

Before this module existed, every admin blueprint defined its own local
copy of this check -- 21 near-identical implementations across the web/
package, split between a decorator style and a guard-function style, with
one file (admin.py) behaving differently on failure (flash + redirect)
than the other 20 (bare abort(403)). This module is the single
replacement for all of them, standardized on abort(403).

For the non-Flask (pure access-level/flag) side of access control, see
anetbbs/features/access_control.py's evaluate_access() -- that one has no
Flask dependency so it's usable from the terminal UI too; this module is
Flask-specific (uses flask.abort + flask_login.current_user) and only
covers the "is this user an admin at all" gate.
"""
from functools import wraps

from flask import abort
from flask_login import current_user


def require_admin_or_403():
    """Guard-function style: call as the first statement in a view body."""
    if not current_user.is_authenticated or not current_user.is_admin:
        abort(403)


def require_admin(f):
    """Decorator style: @require_admin above a view function."""
    @wraps(f)
    def decorated(*args, **kwargs):
        require_admin_or_403()
        return f(*args, **kwargs)
    return decorated
