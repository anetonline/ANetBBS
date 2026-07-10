"""Retrieval for the Ask Anet guru door: SQLite FTS5 MATCH + bm25 ranking,
with alias expansion. Explicitly NOT generative -- see personality.py and
the disclosure text shown at every entry point.
"""
import re

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from ..models import db
from .aliases import ALIASES
from .render_plain import markdown_to_plain

_TOKEN_RE = re.compile(r"[A-Za-z0-9']+")
_STOPWORDS = {
    'a', 'an', 'the', 'is', 'are', 'do', 'does', 'how', 'where', 'can',
    'i', 'to', 'of', 'for', 'in', 'on', 'my', 'you', 'what', 'view',
    'see', 'find',
}


def _tokenize(question):
    return [t.lower() for t in _TOKEN_RE.findall(question)
            if t.lower() not in _STOPWORDS and len(t) > 1]


def _expand_aliases(question, tokens):
    lowered = f' {question.lower()} '
    extra = []
    for phrase, terms in ALIASES.items():
        if phrase in lowered:
            extra.extend(terms)
    return tokens + extra


def _build_match(tokens):
    seen = []
    for t in tokens:
        t = t.replace('"', '')
        if t and t not in seen:
            seen.append(t)
    if not seen:
        return None
    # Quoting each token disables FTS5 operator parsing (so raw
    # punctuation in a typed question can't break the query syntax);
    # trailing * is a prefix match ("notif" -> "notifications").
    return ' OR '.join(f'"{t}"*' for t in seen)


def search(question, limit=8):
    """Return up to `limit` wiki pages ranked by bm25, each as
    {'slug', 'title', 'summary', 'snippet'}. [] if nothing matches, the
    question was empty/all-stopword, or the FTS5 index isn't available
    (e.g. a non-sqlite engine, or a bare test DB that skipped startup
    migration).
    """
    question = (question or '').strip()
    if not question:
        return []
    tokens = _expand_aliases(question, _tokenize(question))
    match = _build_match(tokens)
    if not match:
        return []
    # ASCII-only highlight/ellipsis markers -- the previous version used
    # Unicode guillemets («») and an ellipsis (…), which rendered as
    # mojibake ("?" boxes) on real terminal sessions (CP437, not UTF-8;
    # confirmed live). '>>'/'<<' are also chosen so markdown_to_plain()
    # below can't mistake them for markdown syntax and strip them.
    sql = text("""
        SELECT wp.slug, wp.title, wp.summary,
               snippet(wiki_pages_fts, 1, '>>', '<<', ' ... ', 10) AS snip,
               bm25(wiki_pages_fts, 5.0, 1.0) AS rank
        FROM wiki_pages_fts
        JOIN wiki_pages wp ON wp.id = wiki_pages_fts.rowid
        WHERE wiki_pages_fts MATCH :match AND wp.is_deleted = 0
        ORDER BY rank
        LIMIT :limit
    """)
    try:
        rows = db.session.execute(sql, {'match': match, 'limit': limit}).all()
    except OperationalError:
        db.session.rollback()
        return []
    return [
        {'slug': r.slug, 'title': r.title, 'summary': r.summary or '',
         # snippet() extracts from the raw markdown source (the FTS5
         # index stores markdown as-is, for accurate word matching), so
         # without this, raw "#"/"**"/"[[...]]" syntax leaks straight
         # into the displayed snippet -- confirmed live, e.g. a snippet
         # starting exactly at a page's opening heading rendered as
         # "# TIC Processor" instead of "TIC Processor".
         'snippet': ' '.join(markdown_to_plain(r.snip).split())}
        for r in rows
    ]
