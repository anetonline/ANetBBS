"""Eastern-time display conversion.

Every timestamp in the database is stored as naive UTC (via
datetime.utcnow()) -- that does not change. This module is the single
place that converts a stored UTC timestamp to US/Eastern for DISPLAY
to a sysop or user. Uses stdlib zoneinfo (Python 3.9+, no pytz
dependency) so EST/EDT daylight-saving transitions are handled
automatically and correctly, including the %Z abbreviation in
strftime() output.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo('America/New_York')


def to_eastern(dt):
    """Convert a naive-UTC (assumed) or UTC-aware datetime to an
    Eastern-aware datetime. Returns None if dt is None.

    Also accepts an ISO-8601 string (e.g. datetime.utcnow().isoformat()
    + 'Z', a shape used by a few JSON-facing modules like
    msp/registry_client.py that store a heartbeat timestamp as a
    string rather than a real datetime column) -- parsed the same way,
    then converted. Fails open: a string that doesn't parse as ISO-8601
    is returned unchanged rather than raising, since this helper's job
    is display, not validation.
    """
    if dt is None:
        return None
    if isinstance(dt, str):
        # datetime.fromisoformat() only accepts a trailing 'Z' (the
        # shape every JSON-facing producer here actually emits, e.g.
        # upgrades.py's '%Y-%m-%dT%H:%M:%SZ') as of Python 3.11 --
        # normalize it first so this also works on 3.10, which is
        # what's actually deployed on the live server despite
        # install.sh preferring 3.12/3.11 when available.
        text = dt[:-1] + '+00:00' if dt.endswith('Z') else dt
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(EASTERN)


def from_eastern_input(text, fmt='%Y-%m-%d %H:%M'):
    """Parse a string a sysop typed as Eastern-local wall-clock time
    (e.g. into a form field that was pre-filled by fmt_eastern()) and
    return the equivalent naive-UTC datetime for storage.

    Companion to fmt_eastern() -- any round-tripping form field
    (displayed in Eastern, edited, parsed back, stored as UTC) MUST use
    both together, or the stored value silently shifts by the UTC
    offset every time it's edited: displaying in Eastern but then
    parsing the sysop's edited text as if it were still UTC.
    """
    naive_eastern = datetime.strptime(text, fmt)
    aware_eastern = naive_eastern.replace(tzinfo=EASTERN)
    return aware_eastern.astimezone(timezone.utc).replace(tzinfo=None)


def fmt_eastern(dt, fmt='%Y-%m-%d %H:%M', default=''):
    """Convert dt to Eastern and format it for display in one step.
    Returns `default` when dt is None -- matches the
    `X.strftime(...) if X else 'default'` idiom used throughout the
    codebase before this module existed, so call sites that already
    have their own fallback text can pass it straight through."""
    eastern_dt = to_eastern(dt)
    # to_eastern() fails open on a string it can't parse as ISO-8601 --
    # returns the original string as-is rather than raising (see its
    # own docstring). Honor that contract here too: a non-datetime
    # can't be strftime()'d, so fall back to `default` instead of
    # crashing on genuinely malformed input from an external source
    # (e.g. a hand-rolled upstream registry's release JSON).
    if not isinstance(eastern_dt, datetime):
        return default
    return eastern_dt.strftime(fmt)
