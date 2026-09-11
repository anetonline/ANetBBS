# anetbbs/web/gemini.py
"""
Per-user Gemini capsule editor + browser-friendly viewer.

We don't run a real Gemini-protocol server here (TLS+1965); we just expose
the gemtext content over HTTP at /gemini/<username> for now. A real Gemini
listener would call into this same content."""
from datetime import datetime
from types import SimpleNamespace
from urllib.parse import urlparse

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, Response
from flask_login import login_required, current_user

from ..models import db, GeminiCapsule, User


gemini_bp = Blueprint('gemini', __name__, url_prefix='/gemini')

# Schemes a gemtext "=> target [label]" link line may safely render as a
# clickable <a href>. gemini:// and gopher:// are legitimate non-web
# targets real gemtext authors link to; mailto: is common in a "contact
# me" line.
_GEMTEXT_SAFE_SCHEMES = ('http', 'https', 'gemini', 'gopher', 'mailto')


def _sanitize_gemtext_links(content):
    """Neutralize '=> <target> ...' gemtext link lines whose target isn't
    a safe scheme, before the content ever reaches HTML rendering.

    Real Critical gap found in a security/performance audit:
    templates/gemini/view.html renders each gemtext '=> ' link line
    straight into `<a href="{{ target }}">` with no scheme check at
    all -- unlike every other user-content link renderer in this
    codebase (web/render_msg.py's _linkify(), which only ever linkifies
    https?://, and rss/poller.py's _is_safe_http_url(), which closed
    this exact same class of bug for RSS feed links). A capsule is
    self-published by any registered user (GeminiCapsule.is_published
    is user-controlled, no sysop review), so a line like
    "=> javascript:alert(document.cookie) Click me" would render as a
    real clickable link; any visitor -- including an admin browsing
    /gemini/ -- who clicked it would execute attacker JS same-origin as
    the BBS. A bare relative path (no scheme at all, e.g. "=> /wiki/x")
    is also legitimate and left untouched. Anything else has its
    "=> " marker stripped so the line renders as inert plain text
    instead of a clickable link -- degrading gracefully rather than
    dropping the author's content outright.
    """
    out_lines = []
    for line in (content or '').splitlines():
        if line.startswith('=> '):
            target = line[3:].split(None, 1)[0]
            scheme = urlparse(target).scheme
            if scheme and scheme.lower() not in _GEMTEXT_SAFE_SCHEMES:
                out_lines.append(line[3:])
                continue
        out_lines.append(line)
    return '\n'.join(out_lines)


_GEMINI_DEFAULT = """\
# {username}'s capsule

Welcome to my little corner of the BBS. Edit me at /gemini/edit.

=> https://gemini.circumlunar.space/  Project Gemini

## Recent thoughts

* (write something here)
"""


@gemini_bp.route('/')
def index():
    """List all published capsules."""
    rows = (GeminiCapsule.query.filter_by(is_published=True)
            .order_by(GeminiCapsule.updated_at.desc())
            .limit(100).all())
    return render_template('gemini/index.html', capsules=rows)


@gemini_bp.route('/<username>')
def view(username):
    """View one user's capsule — rendered as gemtext-light HTML."""
    user = User.query.filter_by(username=username).first_or_404()
    cap = GeminiCapsule.query.filter_by(user_id=user.id).first()
    if cap is None or not cap.is_published:
        abort(404)
    # Sanitize before HTML rendering (see _sanitize_gemtext_links's
    # docstring) -- pass a detached duck-typed copy rather than mutating
    # `cap` itself so the sanitized text is never accidentally persisted
    # to the DB. raw_gemtext() below intentionally serves the untouched
    # original: that response is text/gemini, never HTML-rendered.
    safe_capsule = SimpleNamespace(
        title=cap.title,
        content=_sanitize_gemtext_links(cap.content),
        updated_at=cap.updated_at,
    )
    return render_template('gemini/view.html', capsule=safe_capsule, user=user)


@gemini_bp.route('/<username>.gmi')
def raw_gemtext(username):
    """Serve the capsule as raw gemtext (text/gemini)."""
    user = User.query.filter_by(username=username).first_or_404()
    cap = GeminiCapsule.query.filter_by(user_id=user.id).first()
    if cap is None or not cap.is_published:
        abort(404)
    return Response(cap.content or '', mimetype='text/gemini; charset=utf-8')


@gemini_bp.route('/edit', methods=['GET', 'POST'])
@login_required
def edit():
    """Each user manages their own capsule."""
    cap = GeminiCapsule.query.filter_by(user_id=current_user.id).first()
    if cap is None:
        cap = GeminiCapsule(
            user_id=current_user.id,
            title=f'{current_user.username}\'s capsule',
            content=_GEMINI_DEFAULT.format(username=current_user.username),
            is_published=False)
        db.session.add(cap)
        db.session.commit()

    if request.method == 'POST':
        cap.title = (request.form.get('title') or cap.title).strip()
        cap.content = request.form.get('content') or ''
        cap.is_published = bool(request.form.get('is_published'))
        cap.updated_at = datetime.utcnow()
        db.session.commit()
        flash('Capsule saved.', 'success')
        if cap.is_published:
            return redirect(url_for('gemini.view', username=current_user.username))
    return render_template('gemini/edit.html', capsule=cap)
