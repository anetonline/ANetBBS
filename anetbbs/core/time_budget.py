# anetbbs/core/time_budget.py
"""Shared per-user time-budget computation.

Previously this logic existed in exactly one place --
core/session.py's own _enforce_time_budget(), which decides whether to
hard-kick a terminal session once its time runs out. Door-game
launches (games/door_runner.py) never consulted it at all: every
dropfile written to every door reported a flat, hardcoded 60 minutes
remaining regardless of the user's real UserTimeBudget (or complete
absence of one) -- a real gap flagged directly by a sysop testing as
an admin account (which has no time limit at all) and still seeing
every door report exactly one hour left, every time.

compute_remaining_minutes() is the one shared computation both
call sites now use, so a door's reported time-left always matches
what the terminal session is actually held to.
"""

# Reported when there's genuinely no enforced ceiling -- no
# UserTimeBudget row, both limits set to 0 (unlimited), or an admin
# account (core/session.py's own enforcement already exempts admins
# entirely). 1440 (24 hours) rather than some larger synthetic value:
# several classic dropfile formats store this in a field a DOS-era
# door may read as a signed 16-bit integer, and a value that overflows
# that range can wrap to something nonsensical (or negative) instead
# of just being generously large.
UNLIMITED_MINUTES = 1440


def compute_remaining_minutes(user_id, is_admin):
    """Real remaining minutes for user_id right now, mirroring
    core/session.py's _enforce_time_budget()'s own computation exactly
    (session ceiling, further capped by daily-limit-minus-used-today
    plus any banked minutes). Returns UNLIMITED_MINUTES for the same
    three "don't enforce anything" cases that function already treats
    as unlimited: an admin account, no UserTimeBudget row at all, or a
    budget row with both limits set to 0. Never raises -- any lookup
    failure (missing table, no app context, etc.) falls back to
    UNLIMITED_MINUTES rather than reporting 0 (which would make every
    door think the user is instantly out of time)."""
    if is_admin:
        return UNLIMITED_MINUTES
    try:
        from ..models import UserTimeBudget
        from ..features.bbs_ui import _app
        with _app().app_context():
            budget = UserTimeBudget.query.filter_by(user_id=user_id).first()
            if not budget:
                return UNLIMITED_MINUTES
            session_min = budget.time_limit_min or 0
            daily_min = budget.daily_limit_min or 0
            used_today = budget.used_today_min or 0
            bank = budget.bank_minutes or 0
    except Exception:
        return UNLIMITED_MINUTES
    if session_min <= 0 and daily_min <= 0:
        return UNLIMITED_MINUTES
    remaining = session_min if session_min > 0 else UNLIMITED_MINUTES
    if daily_min > 0:
        daily_left = max(0, daily_min - used_today) + bank
        remaining = min(remaining, daily_left)
    return max(remaining, 0)
