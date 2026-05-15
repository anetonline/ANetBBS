# anetbbs/web/docs.py
"""In-app documentation viewer.

Reads markdown files from the docs/ directory at the project root,
renders them with the existing markdown filter, and exposes:
  /docs/                - index (table of contents)
  /docs/<slug>          - single doc page
  /tutorial             - slideshow walkthrough for new sysops
"""
from pathlib import Path
from flask import Blueprint, render_template, abort, url_for


docs_bp = Blueprint('docs', __name__, url_prefix='/docs')


def _docs_dir():
    """Return Path to the project's docs/ directory.
    Falls back gracefully if it doesn't exist."""
    here = Path(__file__).resolve().parent.parent.parent
    return here / 'docs'


def _list_docs():
    """Return list of {slug, title, order} dicts, sorted by order."""
    out = []
    base = _docs_dir()
    if not base.is_dir():
        return out
    for p in sorted(base.glob('*.md')):
        slug = p.stem
        title = slug.replace('-', ' ').replace('_', ' ').title()
        order = 100
        try:
            first_line = p.read_text(encoding='utf-8',
                                      errors='replace').splitlines()[0]
            if first_line.startswith('# '):
                title = first_line[2:].strip()
        except Exception:
            pass
        # Filename prefixes like "01-foo.md" sort first.
        if '-' in slug and slug.split('-', 1)[0].isdigit():
            order = int(slug.split('-', 1)[0])
            title = title.split(' ', 1)[1] if ' ' in title else title
        out.append({'slug': slug, 'title': title, 'order': order, 'path': p})
    out.sort(key=lambda d: (d['order'], d['title'].lower()))
    return out


# Category groupings for the index page. Slugs not listed here fall
# under "Reference". The category order here is the order they're
# rendered in. The same `slugs` list is also used by view.html to
# group the sidebar.
DOC_CATEGORIES = [
    ('Getting Started', 'bi-rocket-takeoff', [
        '00-overview', '01-installing', 'INSTALL',
        'preinstall-tutorial',
    ]),
    ('Daily Operations', 'bi-gear', [
        '02-sysop-daily-ops', '03-menus',
        '09-multinode-nodespy', '11-spam-control',
        '12-upgrading',
    ]),
    ('Networks & Mail', 'bi-envelope', [
        '06-echomail', '07-file-areas',
    ]),
    ('Look & Feel', 'bi-palette', [
        '04-ansi-screens', '08-themes',
    ]),
    ('Doors & Games', 'bi-controller', [
        '05-external-programs', '14-door-games', '15-synchronet-compat',
    ]),
    ('Web Features', 'bi-globe2', [
        '10-personal-pages', '13-image-galleries', '16-rss-reader',
    ]),
    ('Development', 'bi-code-slash', [
        '17-development',
    ]),
    ('Reference', 'bi-book', [
        'CHANGELOG', 'PORTS', 'SECURITY', 'MSP_LOOPBACK_TEST',
    ]),
]


def _categorize(docs):
    """Group flat docs list into the DOC_CATEGORIES buckets.

    Anything not explicitly listed falls into a final 'Other' bucket so
    new doc files don't disappear if someone forgets to update the
    category map."""
    by_slug = {d['slug']: d for d in docs}
    out = []
    placed = set()
    for cat_name, icon, slugs in DOC_CATEGORIES:
        bucket = []
        for s in slugs:
            d = by_slug.get(s)
            if d is None:
                continue
            bucket.append(d)
            placed.add(s)
        if bucket:
            out.append({'name': cat_name, 'icon': icon, 'docs': bucket})
    leftovers = [d for d in docs if d['slug'] not in placed]
    if leftovers:
        out.append({'name': 'Other', 'icon': 'bi-three-dots', 'docs': leftovers})
    return out


@docs_bp.route('/')
def index():
    docs = _list_docs()
    categories = _categorize(docs)
    return render_template('docs/index.html',
                           docs=docs, categories=categories)


@docs_bp.route('/<slug>')
def view(slug):
    base = _docs_dir()
    target = base / f'{slug}.md'
    if not target.is_file():
        abort(404)
    body = target.read_text(encoding='utf-8', errors='replace')
    # Rewrite relative .md links to point at our /docs/<slug> route so
    # cross-references between docs pages work in the browser.
    import re as _re
    body = _re.sub(
        r'\]\((\d{0,3}-?[A-Za-z0-9_-]+)\.md\)',
        lambda m: ']({})'.format(url_for('docs.view',
                                          slug=m.group(1))),
        body)
    docs = _list_docs()
    categories = _categorize(docs)
    return render_template('docs/view.html',
                           body=body, slug=slug, docs=docs,
                           categories=categories)
