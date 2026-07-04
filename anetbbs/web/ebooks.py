# anetbbs/web/ebooks.py
"""
Ebook reader — search free public-domain books via Gutendex
(gutendex.com, a community JSON API over Project Gutenberg's catalog)
and read them in a book-styled web view.

Routes:
    GET  /games/ebooks/classics            — curated built-in shelf
    GET  /games/ebooks/search?q=...        — Gutendex search proxy
    GET  /games/ebooks/book/<id>           — fetch+cache+chapter-split
    GET  /games/ebooks/book/<id>/download  — serve cached text as .txt
    GET  /games/ebooks/bookmarks           — list current user's bookmarks
    POST /games/ebooks/bookmarks           — add a bookmark
    DELETE /games/ebooks/bookmarks/<id>    — remove a bookmark
    GET  /games/ebooks/history             — recently-read list
    POST /games/ebooks/history             — upsert reading position

Only Project Gutenberg is wired up for now (via Gutendex) — Standard
Ebooks and others are a reasonable follow-up but need real scraping
(no clean JSON search API), so they're out of scope for this pass.

Fetches go straight to whatever URL Gutendex's `formats` field returns
(usually gutenberg.org itself) rather than routing through Gutenberg's
official mirror network. Their robot-access policy asks *bulk/automated*
consumers to use a mirror — this fetches a given book at most once ever
(cached forever afterward in EbookCache), so the request volume here is
closer to "one human clicking a download link" than scraping. Worth
revisiting if this ever needs to serve many BBSes at once.

The actual book-text fetch (not the Gutendex API calls, which are
fine) shells out to the system `curl` instead of using `requests`
directly. Confirmed via testing: a plain `requests.get()` to a
gutenberg.org book URL consistently took 25-30s (and occasionally
timed out), reproduced even from a subprocess with zero eventlet
involvement at all (so it isn't this app's own eventlet monkey-patch),
while `curl` against the identical URL consistently returned in under
5s — most likely Gutenberg (or something in front of it) slow-walking
requests/urllib3's TLS/HTTP fingerprint specifically as bot-like, per
their own robot-access policy. `curl` is already a hard install.sh
dependency for this project, so this doesn't add a new requirement.
"""
import json
import logging
import re
import subprocess
from datetime import datetime

import requests
from flask import Blueprint, jsonify, request, Response, abort
from flask_login import login_required, current_user

from ..models import db, EbookCache, EbookBookmark, EbookReadingHistory

logger = logging.getLogger(__name__)

ebooks_bp = Blueprint('ebooks', __name__, url_prefix='/games/ebooks')

GUTENDEX_API = 'https://gutendex.com/books'
_HTTP_TIMEOUT = 20
_USER_AGENT = 'ANetBBS-EbookReader/1.0 (+https://github.com/anetonline/ANetBBS)'

# A small curated shelf of well-known, unambiguous public-domain
# classics for "instant" browsing without having to search first.
# Metadata is fetched live from Gutendex (falls back to this hardcoded
# title/author if Gutendex is unreachable) so we're not trusting our
# own copy of facts that could drift.
CLASSIC_GUTENBERG_IDS = [
    ('1342', 'Pride and Prejudice', 'Jane Austen'),
    ('84', 'Frankenstein; or, The Modern Prometheus', 'Mary Wollstonecraft Shelley'),
    ('345', 'Dracula', 'Bram Stoker'),
    ('11', "Alice's Adventures in Wonderland", 'Lewis Carroll'),
    ('1661', 'The Adventures of Sherlock Holmes', 'Arthur Conan Doyle'),
    ('2701', 'Moby Dick; or, The Whale', 'Herman Melville'),
    ('98', 'A Tale of Two Cities', 'Charles Dickens'),
    ('1400', 'Great Expectations', 'Charles Dickens'),
    ('2600', 'War and Peace', 'graf Leo Tolstoy'),
]

# Project Gutenberg's standard boilerplate markers, stripped so the
# reader shows just the book. Formats vary a bit across eras of their
# production pipeline, hence the fairly loose regex.
_PG_START_RE = re.compile(
    r'\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK[^\n]*\*\*\*',
    re.IGNORECASE)
_PG_END_RE = re.compile(
    r'\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK[^\n]*\*\*\*',
    re.IGNORECASE)

_CHAPTER_RE = re.compile(
    r'^[ \t]*(chapter|part|book)[ \t]+([0-9]+|[ivxlcdm]+)\b.*$',
    re.IGNORECASE | re.MULTILINE)


def _clean_gutenberg_text(raw):
    """Strip PG's license header/footer boilerplate, keep just the book."""
    text = raw
    start_m = _PG_START_RE.search(text)
    if start_m:
        # Skip to the end of that marker's line.
        nl = text.find('\n', start_m.end())
        text = text[nl + 1:] if nl != -1 else text[start_m.end():]
    end_m = _PG_END_RE.search(text)
    if end_m:
        text = text[:end_m.start()]
    return text.strip('\n')


_MIN_CHAPTER_LENGTH = 500  # chars a real chapter needs before the next one


def _split_chapters(text):
    """Return a list of {title, start_offset} from a cleaned book body.

    Falls back to a single "Full Text" chapter if no chapter-like
    headings are found (common in short works, poetry collections, etc).
    """
    matches = list(_CHAPTER_RE.finditer(text))
    if not matches:
        return [{'title': 'Full Text', 'start_offset': 0}]

    # Almost every Gutenberg book opens with a "Contents" table listing
    # every chapter title in a tight run before the real chapters start
    # -- the same regex that finds real chapter headings also matches
    # each of those ToC lines, since they look identical ("CHAPTER I.
    # Down the Rabbit-Hole"). Real chapters are followed by a full
    # chapter's worth of body text; ToC lines are followed almost
    # immediately by the next ToC line. Keep only matches with enough
    # room before whatever comes next to plausibly be real content.
    filtered = []
    for i, m in enumerate(matches):
        next_start = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        if next_start - m.start() >= _MIN_CHAPTER_LENGTH:
            filtered.append(m)
    if not filtered:
        # Every match looked like a ToC entry (e.g. a very short book) --
        # fall back to the last match found, better than nothing.
        filtered = [matches[-1]]

    chapters = []
    for m in filtered:
        title = ' '.join(m.group(0).split())
        chapters.append({'title': title[:80], 'start_offset': m.start()})
    # If the first chapter heading isn't near the very start, the
    # preceding text (front matter, dedication, ToC, etc.) still needs a
    # home so `content[a:b]` slicing in the client covers everything.
    if chapters[0]['start_offset'] > 0:
        chapters.insert(0, {'title': 'Front Matter', 'start_offset': 0})
    return chapters


def _get_text_format_url(formats):
    for key, url in (formats or {}).items():
        if key.lower().startswith('text/plain'):
            return url
    return None


def _gutendex_get(path_or_url, params=None):
    url = path_or_url if path_or_url.startswith('http') else f'{GUTENDEX_API}{path_or_url}'
    resp = requests.get(url, params=params, timeout=_HTTP_TIMEOUT,
                        headers={'User-Agent': _USER_AGENT})
    resp.raise_for_status()
    return resp.json()


def _curl_fetch_text(url, timeout=_HTTP_TIMEOUT):
    """Fetch a URL's body via the system curl binary — see module
    docstring for why this is used instead of requests for book text.
    """
    try:
        result = subprocess.run(
            ['curl', '-sL', '--fail', '--max-time', str(timeout),
             '-A', _USER_AGENT, url],
            capture_output=True, timeout=timeout + 5,
        )
    except subprocess.TimeoutExpired as exc:
        raise requests.RequestException(f'curl timed out fetching {url}') from exc
    if result.returncode != 0:
        raise requests.RequestException(
            f'curl exited {result.returncode} fetching {url}: '
            f'{result.stderr.decode(errors="replace")[:200]}')
    return result.stdout.decode('utf-8', errors='replace')


def get_classics_list():
    """Curated classics shelf as plain dicts — shared by the web route
    and the terminal reader (anetbbs/features/bbs_ui.py) so there's one
    implementation of "fetch metadata for the fixed classics list".
    """
    results = []
    for gid, fallback_title, fallback_author in CLASSIC_GUTENBERG_IDS:
        title, author = fallback_title, fallback_author
        try:
            info = _gutendex_get(f'/{gid}')
            title = info.get('title') or title
            authors = info.get('authors') or []
            if authors:
                author = ', '.join(a.get('name', '') for a in authors if a.get('name'))
        except Exception as exc:
            logger.info('Gutendex lookup failed for classic %s, using fallback: %s', gid, exc)
        results.append({
            'source': 'gutenberg', 'source_id': gid,
            'title': title, 'author': author,
        })
    return results


def search_books(query):
    """Search Gutendex, returning plain dicts — shared by the web route
    and the terminal reader. Raises on a Gutendex-side failure (unlike
    the route, which turns that into a JSON error response); callers
    decide how to present that.
    """
    data = _gutendex_get('', params={'search': query})
    books = []
    for book in data.get('results', []) or []:
        if not _get_text_format_url(book.get('formats')):
            continue  # skip books with no readable plain-text format
        authors = book.get('authors') or []
        books.append({
            'source': 'gutenberg',
            'source_id': str(book.get('id')),
            'title': book.get('title') or 'Untitled',
            'author': ', '.join(a.get('name', '') for a in authors if a.get('name')) or 'Unknown',
            'languages': book.get('languages') or [],
            'download_count': book.get('download_count') or 0,
        })
    return books[:40]


@ebooks_bp.route('/classics')
@login_required
def classics():
    return jsonify({'books': get_classics_list()})


@ebooks_bp.route('/search')
@login_required
def search():
    query = (request.args.get('q') or '').strip()
    if not query:
        return jsonify({'books': [], 'error': 'empty query'}), 400
    try:
        books = search_books(query)
    except Exception as exc:
        logger.warning('Gutendex search failed for %r: %s', query, exc)
        return jsonify({'books': [], 'error': 'search service unavailable'}), 502
    return jsonify({'books': books})


def _fetch_and_cache_book(source_id):
    cached = EbookCache.query.filter_by(source='gutenberg', source_id=source_id).first()
    if cached:
        return cached

    info = _gutendex_get(f'/{source_id}')
    text_url = _get_text_format_url(info.get('formats'))
    if not text_url:
        return None

    raw = _curl_fetch_text(text_url)
    cleaned = _clean_gutenberg_text(raw)
    chapters = _split_chapters(cleaned)

    authors = info.get('authors') or []
    author = ', '.join(a.get('name', '') for a in authors if a.get('name')) or 'Unknown'
    languages = info.get('languages') or []

    cached = EbookCache(
        source='gutenberg',
        source_id=source_id,
        title=info.get('title') or 'Untitled',
        author=author,
        language=languages[0] if languages else None,
        content=cleaned,
        chapters_json=json.dumps(chapters),
        fetched_at=datetime.utcnow(),
    )
    db.session.add(cached)
    db.session.commit()
    return cached


@ebooks_bp.route('/book/<source_id>')
@login_required
def get_book(source_id):
    try:
        book = _fetch_and_cache_book(source_id)
    except requests.RequestException as exc:
        logger.warning('Failed to fetch Gutenberg book %s: %s', source_id, exc)
        return jsonify({'error': 'Could not download this book right now. Try again shortly.'}), 502

    if book is None:
        return jsonify({'error': 'No readable text format available for this book.'}), 404

    return jsonify({
        'source': book.source,
        'source_id': book.source_id,
        'title': book.title,
        'author': book.author,
        'language': book.language,
        'content': book.content,
        'chapters': json.loads(book.chapters_json or '[]'),
    })


@ebooks_bp.route('/book/<source_id>/download')
@login_required
def download_book(source_id):
    book = EbookCache.query.filter_by(source='gutenberg', source_id=source_id).first()
    if book is None:
        abort(404)
    safe_title = re.sub(r'[^A-Za-z0-9 _-]', '', book.title or 'book').strip() or 'book'
    filename = f'{safe_title}.txt'
    return Response(
        book.content or '',
        mimetype='text/plain',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


@ebooks_bp.route('/bookmarks')
@login_required
def list_bookmarks():
    source_id = request.args.get('source_id')
    q = EbookBookmark.query.filter_by(user_id=current_user.id)
    if source_id:
        q = q.filter_by(source='gutenberg', source_id=source_id)
    marks = q.order_by(EbookBookmark.created_at.desc()).all()
    return jsonify({'bookmarks': [{
        'id': m.id, 'source': m.source, 'source_id': m.source_id,
        'title': m.title, 'author': m.author, 'name': m.name,
        'position': m.position,
        'created_at': m.created_at.isoformat() if m.created_at else None,
    } for m in marks]})


@ebooks_bp.route('/bookmarks', methods=['POST'])
@login_required
def add_bookmark():
    data = request.get_json(silent=True) or {}
    source_id = (data.get('source_id') or '').strip()
    if not source_id:
        return jsonify({'error': 'source_id required'}), 400
    mark = EbookBookmark(
        user_id=current_user.id,
        source='gutenberg',
        source_id=source_id,
        title=(data.get('title') or '')[:300],
        author=(data.get('author') or '')[:300],
        name=(data.get('name') or 'Bookmark')[:100],
        position=int(data.get('position') or 0),
        created_at=datetime.utcnow(),
    )
    db.session.add(mark)
    db.session.commit()
    return jsonify({'id': mark.id}), 201


@ebooks_bp.route('/bookmarks/<int:bookmark_id>', methods=['DELETE'])
@login_required
def delete_bookmark(bookmark_id):
    mark = EbookBookmark.query.filter_by(id=bookmark_id, user_id=current_user.id).first()
    if mark is None:
        abort(404)
    db.session.delete(mark)
    db.session.commit()
    return jsonify({'ok': True})


@ebooks_bp.route('/history')
@login_required
def list_history():
    entries = (EbookReadingHistory.query
               .filter_by(user_id=current_user.id)
               .order_by(EbookReadingHistory.last_read_at.desc())
               .limit(30).all())
    return jsonify({'history': [{
        'source': h.source, 'source_id': h.source_id,
        'title': h.title, 'author': h.author,
        'last_position': h.last_position,
        'last_read_at': h.last_read_at.isoformat() if h.last_read_at else None,
    } for h in entries]})


@ebooks_bp.route('/history', methods=['POST'])
@login_required
def update_history():
    data = request.get_json(silent=True) or {}
    source_id = (data.get('source_id') or '').strip()
    if not source_id:
        return jsonify({'error': 'source_id required'}), 400

    entry = EbookReadingHistory.query.filter_by(
        user_id=current_user.id, source='gutenberg', source_id=source_id).first()
    if entry is None:
        entry = EbookReadingHistory(
            user_id=current_user.id, source='gutenberg', source_id=source_id)
        db.session.add(entry)
    entry.title = (data.get('title') or entry.title or '')[:300]
    entry.author = (data.get('author') or entry.author or '')[:300]
    entry.last_position = int(data.get('position') or 0)
    entry.last_read_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True})
