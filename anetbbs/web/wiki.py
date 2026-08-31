"""Wiki blueprint — collaborative documentation pages.

Routes:
  /wiki/                       index (page list, recent edits)
  /wiki/<slug>                 view a page (auto-redirects to /wiki/ for empty)
  /wiki/<slug>/edit            edit form (login required; lock honored)
  /wiki/<slug>/history         revision list
  /wiki/<slug>/rev/<rev_num>   view a specific revision
  /wiki/<slug>/diff/<a>/<b>    unified diff between two revisions
  /wiki/<slug>/delete          soft-delete (admin only)
  /wiki/<slug>/lock            toggle edit lock (admin only)
  /wiki/<slug>/restore         undo soft-delete (admin only)
  /wiki/new                    create-new form (suggests slug from title)
  /wiki/recent                 latest edits across the wiki
  /wiki/search?q=...           full-text search across body + title
  /wiki/all                    plain alphabetical index of every page
  /wiki/wanted                 pages linked-to but not yet created
  /wiki/orphans                pages no other page links to

Auth model:
  - Anyone (incl. logged-out) can READ.
  - Logged-in users can EDIT and CREATE pages, except locked ones.
  - Admins can lock, delete, rename slugs, and edit locked pages.
"""
import difflib
import json
from datetime import datetime

from flask import (Blueprint, render_template, request, redirect,
                   url_for, abort, flash, jsonify, current_app)
from flask_login import login_required, current_user
from sqlalchemy import or_, func
from sqlalchemy.exc import IntegrityError

from ..models import db, WikiPage, WikiRevision, Post
from ..wiki.slug import slugify
from ..wiki.render import render as render_wiki, extract_outgoing_links


wiki_bp = Blueprint('wiki', __name__, url_prefix='/wiki')

# Real gap found in a security/performance audit: nothing capped a
# wiki page body's length -- the only bound was Flask's app-wide
# MAX_CONTENT_LENGTH (110 MB, sized for file uploads, a completely
# different use case). A page body is user-authored text/markup;
# even a very long real article runs tens of KB, so a much smaller,
# dedicated cap closes both the DB-bloat risk (WikiPage.body is an
# unbounded db.Text column) and the render cost risk (render_wiki()
# runs on every view AND on every keystroke-triggered /preview call).
_WIKI_BODY_MAX_CHARS = 500_000


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

from .access_control import require_admin as _admin_required


def _all_slugs():
    """Return a set of every live page slug — used by the renderer
    to flag missing-target [[wiki-links]] as red links."""
    rows = db.session.query(WikiPage.slug).filter_by(is_deleted=False).all()
    return {r[0] for r in rows}


def _get_page_or_redirect(slug):
    """Resolve a slug to a live WikiPage, or return None.

    Slugs are stored as already-slugified, but tolerate visitors typing
    a title-cased URL like /wiki/Getting-Started by re-slugifying
    once and 301'ing to the canonical form.
    """
    canonical = slugify(slug)
    if canonical != slug:
        return ('redirect', canonical)
    p = WikiPage.query.filter_by(slug=canonical, is_deleted=False).first()
    return ('page', p)


def _topnav_pages():
    """A small set of navigation anchors shown on every wiki page."""
    return [
        ('home', 'Home'),
        ('getting-started', 'Getting Started'),
        ('features', 'Features'),
        ('sysop-guide', 'Sysop Guide'),
        ('faq', 'FAQ'),
    ]


def _cache_outgoing_links(page, body):
    """Recompute and store `page.outgoing_links_cache` from `body`.

    Called on every save so wanted()/orphans() can read this instead of
    re-running extract_outgoing_links() (a regex scan that first strips
    fenced/inline code) over every page's full body on every single
    visit to those two pages."""
    page.outgoing_links_cache = json.dumps(
        sorted(extract_outgoing_links(body)))


def _save_revision(page, body, title, edit_summary, author):
    """Append a new WikiRevision to `page` and update the page's
    canonical fields. Caller must commit."""
    last_num = (db.session.query(func.max(WikiRevision.rev_num))
                .filter_by(page_id=page.id).scalar() or 0)
    rev = WikiRevision(
        page_id=page.id,
        rev_num=last_num + 1,
        title=title[:200],
        body=body or '',
        edit_summary=(edit_summary or '')[:300],
        author_id=getattr(author, 'id', None),
        author_ip=request.remote_addr,
    )
    db.session.add(rev)
    page.title = title[:200]
    page.body = body or ''
    page.summary = (edit_summary or '')[:300]
    page.updated_at = datetime.utcnow()
    page.updated_by_id = getattr(author, 'id', None)
    _cache_outgoing_links(page, page.body)
    return rev


# ----------------------------------------------------------------------
# Index, list, recent, search
# ----------------------------------------------------------------------

@wiki_bp.route('/')
def index():
    """Wiki landing page — renders the 'home' page if it exists,
    otherwise shows a friendly 'create the home page' placeholder."""
    home = WikiPage.query.filter_by(slug='home', is_deleted=False).first()
    recent = (WikiPage.query
              .filter_by(is_deleted=False)
              .order_by(WikiPage.updated_at.desc())
              .limit(8).all())
    total = WikiPage.query.filter_by(is_deleted=False).count()
    if home:
        body_html = render_wiki(home.body, _all_slugs())
    else:
        body_html = None
    return render_template('wiki/index.html',
                           home=home, body_html=body_html,
                           recent=recent, total=total,
                           nav=_topnav_pages())


@wiki_bp.route('/all')
def all_pages():
    pages = (WikiPage.query
             .filter_by(is_deleted=False)
             .order_by(WikiPage.title).all())
    by_letter = {}
    for p in pages:
        ch = (p.title[0] if p.title else '?').upper()
        if not ch.isalpha():
            ch = '#'
        by_letter.setdefault(ch, []).append(p)
    return render_template('wiki/all.html',
                           by_letter=sorted(by_letter.items()),
                           total=len(pages),
                           nav=_topnav_pages())


@wiki_bp.route('/recent')
def recent():
    page_num = max(1, int(request.args.get('page', 1)))
    pagn = (WikiRevision.query
            .order_by(WikiRevision.created_at.desc())
            .paginate(page=page_num, per_page=50, error_out=False))
    return render_template('wiki/recent.html', pagination=pagn,
                           nav=_topnav_pages())


@wiki_bp.route('/search')
def search():
    q = (request.args.get('q') or '').strip()
    results = []
    if q:
        like = f'%{q}%'
        results = (WikiPage.query
                   .filter(WikiPage.is_deleted == False)
                   .filter(or_(WikiPage.title.ilike(like),
                               WikiPage.body.ilike(like),
                               WikiPage.slug.ilike(like)))
                   .order_by(WikiPage.updated_at.desc())
                   .limit(100).all())
    return render_template('wiki/search.html', q=q, results=results,
                           nav=_topnav_pages())


def _all_pages_outgoing_links():
    """Yield (page, {outgoing slugs}) for every live page.

    Real perf issue found in a broader web-UI audit: wanted()/orphans()
    used to call extract_outgoing_links() (a regex scan that first
    strips fenced/inline code) over every page's full body, from
    scratch, on every single visit to either page -- uncached, so it
    got slower as the wiki grew. Reads the cache _save_revision()
    maintains instead. Rows that predate the cache column (NULL) are
    self-healed here: computed once and written back, so this degrades
    to the old per-page cost only once per page, ever, not once per
    page per visit.
    """
    dirty = False
    for p in WikiPage.query.filter_by(is_deleted=False).all():
        if p.outgoing_links_cache is None:
            _cache_outgoing_links(p, p.body)
            dirty = True
        yield p, set(json.loads(p.outgoing_links_cache))
    if dirty:
        db.session.commit()


@wiki_bp.route('/wanted')
def wanted():
    """Pages that get linked to but don't exist yet."""
    live = _all_slugs()
    wanted_counts = {}
    for p, links in _all_pages_outgoing_links():
        for slug in links:
            if slug not in live:
                wanted_counts[slug] = wanted_counts.get(slug, 0) + 1
    items = sorted(wanted_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return render_template('wiki/wanted.html', items=items,
                           nav=_topnav_pages())


@wiki_bp.route('/orphans')
def orphans():
    """Pages no other page links to (other than home)."""
    incoming = set()
    for p, links in _all_pages_outgoing_links():
        incoming.update(links)
    orphans_list = (WikiPage.query
                    .filter_by(is_deleted=False)
                    .filter(WikiPage.slug != 'home')
                    .filter(~WikiPage.slug.in_(incoming))
                    .order_by(WikiPage.title).all()) if incoming \
                   else WikiPage.query.filter_by(is_deleted=False).all()
    return render_template('wiki/orphans.html', orphans=orphans_list,
                           nav=_topnav_pages())


# ----------------------------------------------------------------------
# View / create
# ----------------------------------------------------------------------

@wiki_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new():
    """Create-new form. Suggests slug from title; user can override.
    Posting straight to /wiki/<slug>/edit also works for known slugs."""
    if request.method == 'POST':
        title = (request.form.get('title') or '').strip()
        slug_in = (request.form.get('slug') or '').strip()
        slug = slugify(slug_in or title)
        if not title or not slug:
            flash('Title is required.', 'warning')
            return render_template('wiki/new.html', title=title, slug=slug_in,
                                   nav=_topnav_pages())
        # Existing? send to edit form for it.
        existing = WikiPage.query.filter_by(slug=slug).first()
        if existing and not existing.is_deleted:
            flash(f'A page already exists at /wiki/{slug} — '
                  f'edit it instead.', 'info')
            return redirect(url_for('wiki.edit', slug=slug))
        return redirect(url_for('wiki.edit', slug=slug,
                                 title=title))
    return render_template('wiki/new.html', title='', slug='',
                           nav=_topnav_pages())


@wiki_bp.route('/<slug>')
def view(slug):
    """Render a page. Missing pages show a 'create this page' stub."""
    kind, val = _get_page_or_redirect(slug)
    if kind == 'redirect':
        return redirect(url_for('wiki.view', slug=val), code=301)
    page = val
    if page is None:
        # Soft-deleted page? Tell admin so they can restore.
        deleted = WikiPage.query.filter_by(slug=slug, is_deleted=True).first()
        return render_template('wiki/missing.html', slug=slug,
                               deleted=deleted,
                               nav=_topnav_pages()), 404
    # Best-effort view counter — single UPDATE, no contention concern
    # for a small site.
    try:
        page.view_count = (page.view_count or 0) + 1
        db.session.commit()
    except Exception:
        db.session.rollback()
    body_html = render_wiki(page.body, _all_slugs())
    return render_template('wiki/view.html', page=page,
                           body_html=body_html,
                           nav=_topnav_pages())


# ----------------------------------------------------------------------
# Edit / save
# ----------------------------------------------------------------------

def _edit_gate_message(user):
    """Real gap found in a security/performance audit: this check used
    to live inline in edit() only -- revert() (which also writes a new
    WikiRevision and overwrites page.body/page.title, exactly like a
    normal save) never called it at all, only the page-lock check. A
    brand-new account with zero posts, which edit() correctly blocks,
    could still vandalize any unlocked page by POSTing straight to
    /wiki/<slug>/revert/<rev_num> with any existing revision number --
    discarding every later legitimate edit with no reputation gate
    stopping them. Extracted so both routes enforce the identical
    check instead of edit()'s own copy silently drifting out of sync
    with whichever route the gate was meant to cover.

    Returns the flash message string if the current user is blocked by
    the minimum-post-count/account-age gate, or None if they may
    proceed (admins are always exempt)."""
    if getattr(user, 'is_admin', False):
        return None
    min_posts = int(current_app.config.get('WIKI_MIN_POSTS', 5))
    min_days = int(current_app.config.get('WIKI_MIN_DAYS', 3))
    if not (min_posts or min_days):
        return None
    post_count = Post.query.filter_by(author_id=user.id).count()
    account_age = (datetime.utcnow() - user.created_at).days
    if post_count >= min_posts and account_age >= min_days:
        return None
    parts = []
    if post_count < min_posts:
        parts.append(f'at least {min_posts} post{"s" if min_posts != 1 else ""}')
    if account_age < min_days:
        parts.append(f'an account at least {min_days} day{"s" if min_days != 1 else ""} old')
    return f'Wiki editing requires {" and ".join(parts)}.'


@wiki_bp.route('/<slug>/edit', methods=['GET', 'POST'])
@login_required
def edit(slug):
    canonical = slugify(slug)
    if canonical != slug:
        return redirect(url_for('wiki.edit', slug=canonical), code=301)
    page = WikiPage.query.filter_by(slug=canonical).first()
    is_new = page is None or page.is_deleted

    # Lock check (admin override).
    if (page and page.is_locked
            and not getattr(current_user, 'is_admin', False)):
        flash('This page is locked. Only sysops can edit it.', 'warning')
        return redirect(url_for('wiki.view', slug=canonical))

    # Edit gate — minimum post count + account age (admins exempt).
    gate_message = _edit_gate_message(current_user)
    if gate_message:
        flash(gate_message, 'warning')
        return redirect(url_for('wiki.view', slug=canonical))

    if request.method == 'POST':
        title = (request.form.get('title') or '').strip()
        body = request.form.get('body') or ''
        edit_summary = (request.form.get('edit_summary') or '').strip()
        if not title:
            flash('Title is required.', 'warning')
            return render_template('wiki/edit.html', page=page,
                                   title=title, body=body,
                                   edit_summary=edit_summary,
                                   slug=canonical, is_new=is_new,
                                   nav=_topnav_pages())
        if len(body) > _WIKI_BODY_MAX_CHARS:
            flash(f'Page body too long (max {_WIKI_BODY_MAX_CHARS:,} characters).',
                 'danger')
            return render_template('wiki/edit.html', page=page,
                                   title=title, body=body,
                                   edit_summary=edit_summary,
                                   slug=canonical, is_new=is_new,
                                   nav=_topnav_pages())

        if is_new:
            page = WikiPage(slug=canonical, title=title[:200],
                            body=body, summary=edit_summary[:300],
                            created_by_id=current_user.id,
                            updated_by_id=current_user.id,
                            is_deleted=False)
            db.session.add(page)
            db.session.flush()  # need page.id for the revision FK
        _save_revision(page, body, title, edit_summary, current_user)
        try:
            db.session.commit()
            flash(f'Saved "{title}".', 'success')
        except Exception as exc:  # pylint: disable=broad-except
            db.session.rollback()
            flash(f'Save failed: {exc}', 'danger')
            return render_template('wiki/edit.html', page=page,
                                   title=title, body=body,
                                   edit_summary=edit_summary,
                                   slug=canonical, is_new=is_new,
                                   nav=_topnav_pages())
        return redirect(url_for('wiki.view', slug=canonical))

    # GET — populate form
    title = (request.args.get('title')
             or (page.title if page and not page.is_deleted else
                 canonical.replace('-', ' ').title()))
    body = (request.args.get('body')
            or (page.body if page and not page.is_deleted else ''))
    return render_template('wiki/edit.html', page=page,
                           title=title, body=body,
                           edit_summary='',
                           slug=canonical, is_new=is_new,
                           nav=_topnav_pages())


@wiki_bp.route('/<slug>/preview', methods=['POST'])
@login_required
def preview(slug):
    """Live preview endpoint — JS calls this from the edit form."""
    body = request.form.get('body') or ''
    if len(body) > _WIKI_BODY_MAX_CHARS:
        return jsonify({'error': f'Body too long (max {_WIKI_BODY_MAX_CHARS:,} characters).'}), 413
    html = str(render_wiki(body, _all_slugs()))
    return jsonify({'html': html})


# ----------------------------------------------------------------------
# History / revisions / diff
# ----------------------------------------------------------------------

@wiki_bp.route('/<slug>/history')
def history(slug):
    page = WikiPage.query.filter_by(slug=slugify(slug)).first_or_404()
    revs = (page.revisions
            .order_by(WikiRevision.rev_num.desc())
            .limit(200).all())
    return render_template('wiki/history.html', page=page, revs=revs,
                           nav=_topnav_pages())


@wiki_bp.route('/<slug>/rev/<int:rev_num>')
def view_revision(slug, rev_num):
    page = WikiPage.query.filter_by(slug=slugify(slug)).first_or_404()
    rev = (page.revisions
           .filter_by(rev_num=rev_num).first_or_404())
    body_html = render_wiki(rev.body, _all_slugs())
    return render_template('wiki/revision.html', page=page, rev=rev,
                           body_html=body_html,
                           nav=_topnav_pages())


@wiki_bp.route('/<slug>/diff/<int:a>/<int:b>')
def diff(slug, a, b):
    page = WikiPage.query.filter_by(slug=slugify(slug)).first_or_404()
    ra = page.revisions.filter_by(rev_num=a).first_or_404()
    rb = page.revisions.filter_by(rev_num=b).first_or_404()
    diff_lines = list(difflib.unified_diff(
        (ra.body or '').splitlines(),
        (rb.body or '').splitlines(),
        fromfile=f'r{ra.rev_num}', tofile=f'r{rb.rev_num}',
        lineterm=''))
    return render_template('wiki/diff.html', page=page,
                           a=ra, b=rb, diff=diff_lines,
                           nav=_topnav_pages())


@wiki_bp.route('/<slug>/revert/<int:rev_num>', methods=['POST'])
@login_required
def revert(slug, rev_num):
    """Roll the page back to a prior revision by writing a new
    revision with that revision's body. Keeps the audit trail intact."""
    page = WikiPage.query.filter_by(slug=slugify(slug)).first_or_404()
    if (page.is_locked
            and not getattr(current_user, 'is_admin', False)):
        abort(403)
    gate_message = _edit_gate_message(current_user)
    if gate_message:
        flash(gate_message, 'warning')
        return redirect(url_for('wiki.view', slug=page.slug))
    target = page.revisions.filter_by(rev_num=rev_num).first_or_404()
    _save_revision(page, target.body, target.title,
                   f'Reverted to revision {rev_num}', current_user)
    try:
        db.session.commit()
        flash(f'Reverted to revision {rev_num}.', 'success')
    except Exception as exc:  # pylint: disable=broad-except
        db.session.rollback()
        flash(f'Revert failed: {exc}', 'danger')
    return redirect(url_for('wiki.view', slug=page.slug))


# ----------------------------------------------------------------------
# Admin actions: lock, delete, restore, rename
# ----------------------------------------------------------------------

@wiki_bp.route('/<slug>/lock', methods=['POST'])
@login_required
@_admin_required
def toggle_lock(slug):
    page = WikiPage.query.filter_by(slug=slugify(slug)).first_or_404()
    page.is_locked = not page.is_locked
    db.session.commit()
    flash(f'{"Locked" if page.is_locked else "Unlocked"} "{page.title}".',
          'success')
    return redirect(url_for('wiki.view', slug=page.slug))


@wiki_bp.route('/<slug>/delete', methods=['POST'])
@login_required
@_admin_required
def delete(slug):
    page = WikiPage.query.filter_by(slug=slugify(slug)).first_or_404()
    page.is_deleted = True
    db.session.commit()
    flash(f'Deleted "{page.title}". Restore from /wiki/<slug> 404 page '
          f'or by re-creating with the same slug.', 'success')
    return redirect(url_for('wiki.index'))


@wiki_bp.route('/<slug>/restore', methods=['POST'])
@login_required
@_admin_required
def restore(slug):
    page = WikiPage.query.filter_by(slug=slugify(slug),
                                     is_deleted=True).first_or_404()
    page.is_deleted = False
    db.session.commit()
    flash(f'Restored "{page.title}".', 'success')
    return redirect(url_for('wiki.view', slug=page.slug))


@wiki_bp.route('/<slug>/rename', methods=['POST'])
@login_required
@_admin_required
def rename(slug):
    page = WikiPage.query.filter_by(slug=slugify(slug)).first_or_404()
    new_slug = slugify(request.form.get('new_slug') or '')
    if not new_slug:
        flash('Need a new slug.', 'warning')
        return redirect(url_for('wiki.view', slug=page.slug))
    if WikiPage.query.filter_by(slug=new_slug).first():
        flash(f'A page already exists at /wiki/{new_slug}.', 'danger')
        return redirect(url_for('wiki.view', slug=page.slug))
    old_slug = page.slug
    page.slug = new_slug
    try:
        # Real gap found in a security/performance audit: the check
        # above and this write aren't atomic -- two concurrent renames
        # to the SAME new_slug could both pass the check, then the
        # second one hits WikiPage.slug's own unique constraint and
        # raised an unhandled 500 instead of the same friendly message
        # the check above already gives a sequential collision. The
        # try/except has to wrap _save_revision() too, not just the
        # explicit commit() below -- _save_revision()'s own query
        # (looking up the last WikiRevision.rev_num) triggers
        # SQLAlchemy's autoflush, which is what actually flushes the
        # pending page.slug change and hits the constraint, before
        # this function's own commit() is ever reached. Admin-only and
        # needs precise double-submission timing to hit for real, so
        # low-severity/cosmetic -- but free to fix with the same
        # pattern already used for polls.py's vote() race.
        _save_revision(page, page.body, page.title,
                       f'Renamed from {old_slug} to {new_slug}', current_user)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash(f'A page already exists at /wiki/{new_slug}.', 'danger')
        return redirect(url_for('wiki.view', slug=old_slug))
    flash(f'Renamed /wiki/{old_slug} → /wiki/{new_slug}.', 'success')
    return redirect(url_for('wiki.view', slug=new_slug))
