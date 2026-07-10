"""Creates/syncs the Ask Anet guru door's FTS5 search index over wiki_pages.

External-content FTS5 table: the index stores only the inverted term
data, not a copy of title/body -- wiki_pages stays the single source
of truth. Kept in sync via triggers rather than a rebuild-on-write or
cron job because wiki edits are rare and admin-only; triggers are
simplest-correct here, and at a few dozen pages there's no perf reason
to do anything fancier.

SQLite-only (FTS5 is a SQLite extension). Callers must guard on dialect;
ensure_fts_index() itself no-ops on any other engine.
"""
import sqlite3

_DDL = [
    """CREATE VIRTUAL TABLE IF NOT EXISTS wiki_pages_fts USING fts5(
        title, body,
        content='wiki_pages', content_rowid='id',
        tokenize='porter unicode61'
    )""",
    """CREATE TRIGGER IF NOT EXISTS wiki_pages_fts_ai
       AFTER INSERT ON wiki_pages BEGIN
         INSERT INTO wiki_pages_fts(rowid, title, body)
         VALUES (new.id, new.title, new.body);
       END""",
    """CREATE TRIGGER IF NOT EXISTS wiki_pages_fts_ad
       AFTER DELETE ON wiki_pages BEGIN
         INSERT INTO wiki_pages_fts(wiki_pages_fts, rowid, title, body)
         VALUES('delete', old.id, old.title, old.body);
       END""",
    # Scoped to title/body only -- NOT view_count, which changes on every
    # page view and would otherwise cause pointless reindexing.
    """CREATE TRIGGER IF NOT EXISTS wiki_pages_fts_au
       AFTER UPDATE OF title, body ON wiki_pages BEGIN
         INSERT INTO wiki_pages_fts(wiki_pages_fts, rowid, title, body)
         VALUES('delete', old.id, old.title, old.body);
         INSERT INTO wiki_pages_fts(rowid, title, body)
         VALUES (new.id, new.title, new.body);
       END""",
]


def ensure_fts_index(engine, sa_text, logger=None):
    """Idempotent: safe to call on every startup, including concurrently
    from multiple processes (gunicorn web workers, the separate
    telnet/SSH terminal service, binkp, etc. all call this independently
    at their own startup).

    Also backfills the index for pre-existing wiki_pages rows on an
    *upgrading* install (fresh installs populate naturally via the
    AFTER INSERT trigger when anetbbs.wiki.seed runs, later in the
    same startup sequence).

    Runs through the raw sqlite3 driver rather than SQLAlchemy's
    engine.begin(), under an explicit BEGIN IMMEDIATE (real write lock
    taken up front, not SQLAlchemy's deferred-by-default sqlite
    transaction), so two processes starting at the same moment (gunicorn
    web workers, the separate telnet/SSH terminal service, binkp, etc.
    all call this independently) can't both decide to run the one-time
    backfill rebuild concurrently.

    The backfill trigger is "did this table not already exist before
    this call", NOT a row-count comparison between wiki_pages_fts and
    wiki_pages -- confirmed live (and reproduced deterministically,
    zero concurrency needed) that count(*) on an *external content*
    FTS5 table is a passthrough to the content table's own row count
    regardless of whether the actual inverted search index has ever
    been populated. So on an upgrading install (wiki_pages already had
    rows before this code ever ran -- e.g. every real ANetBBS install
    prior to this feature shipping), the two counts always matched from
    the moment the empty table was created, permanently short-circuiting
    the old "only rebuild if counts differ" check and leaving searches
    silently returning nothing forever, even though direct non-MATCH
    reads of wiki_pages_fts (a plain `SELECT body FROM ...`) return
    correct content via that same passthrough -- which is what made it
    look like the index was populated when it wasn't. Checking
    sqlite_master for prior existence is immune to that: it's only ever
    true on the one process, one time, that actually creates the table.
    """
    if engine.dialect.name != 'sqlite':
        if logger:
            logger.info(
                'Ask Anet guru: skipping FTS5 index (dialect %s is not '
                'sqlite) -- search will be unavailable', engine.dialect.name)
        return

    db_path = engine.url.database
    if not db_path or db_path == ':memory:':
        return  # nothing to serialize for an in-memory test DB

    raw = sqlite3.connect(db_path, timeout=30)
    try:
        raw.execute('PRAGMA busy_timeout=20000')
        raw.execute('BEGIN IMMEDIATE')
        existed_before = raw.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='wiki_pages_fts'").fetchone() is not None
        for stmt in _DDL:
            raw.execute(stmt)
        if not existed_before:
            raw.execute(
                "INSERT INTO wiki_pages_fts(wiki_pages_fts) VALUES('rebuild')")
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()
