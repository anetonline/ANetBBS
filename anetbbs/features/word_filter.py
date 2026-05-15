# anetbbs/features/word_filter.py
"""
Naughty-word / profanity filter. Sysop manages WordFilter rows; we run
every shout/post/PM through `apply()` to mask matched terms.
"""
import re

from ..models import db, WordFilter


_cached = {'patterns': None, 'compiled': None}


def _load():
    """Load active filters into a compiled regex for fast matching."""
    rows = WordFilter.query.filter_by(is_active=True).all()
    if not rows:
        _cached['patterns'] = []
        _cached['compiled'] = None
        return
    pieces = [re.escape(r.pattern) for r in rows if r.pattern]
    if not pieces:
        _cached['compiled'] = None
        return
    _cached['patterns'] = [(r.pattern, r.replacement or '****') for r in rows
                           if r.pattern]
    _cached['compiled'] = re.compile(
        r'\b(' + '|'.join(re.escape(p) for p, _ in _cached['patterns']) + r')\b',
        re.IGNORECASE)


def apply(text):
    """Run text through the filter. Cached after first call per request."""
    if not text:
        return text
    if _cached['compiled'] is None:
        try:
            _load()
        except Exception:
            db.session.rollback()
            return text
    if not _cached['compiled']:
        return text
    repl_map = {p.lower(): r for p, r in (_cached['patterns'] or [])}
    return _cached['compiled'].sub(
        lambda m: repl_map.get(m.group(1).lower(), '****'), text)


def invalidate():
    """Force a reload on next apply() — call after admin edits."""
    _cached['compiled'] = None
    _cached['patterns'] = None
