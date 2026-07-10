"""Ask Anet -- the in-BBS help guru (web UI).

See anetbbs/guru/ for the retrieval mechanism: SQLite FTS5 over the wiki,
no external or local AI model. See personality.DISCLOSURE for the
required user-facing explanation of what this actually is.
"""
from flask import Blueprint, render_template, request

from ..guru import search as guru_search, personality

guru_bp = Blueprint('guru', __name__, url_prefix='/guru')


@guru_bp.route('/')
def index():
    q = (request.args.get('q') or '').strip()
    results = guru_search.search(q, limit=8) if q else []
    return render_template(
        'guru/index.html', q=q, results=results,
        disclosure=personality.DISCLOSURE,
        intro=personality.intro(),
        not_found=personality.not_found())
