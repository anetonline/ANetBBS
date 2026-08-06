"""Wiki page rendering: markdown + [[wiki-links]] + sanitization.

[[Page Title]]      → /wiki/page-title (red link if missing)
[[slug|Display]]    → display-text link to /wiki/slug
[[Page Title#sec]]  → /wiki/page-title#sec

The renderer pre-processes wiki-link syntax into HTML anchor tags
*before* handing the body to python-markdown, so they survive intact
through to the bleach sanitizer.
"""
import re
from markupsafe import Markup, escape

from .slug import slugify

# Matches [[anything inside]] — non-greedy. Captures the inner text.
_WIKILINK_RE = re.compile(r'\[\[([^\[\]\|]+)(?:\|([^\[\]]+))?\]\]')


def _build_wikilink_html(target, label, known_slugs):
    """Return an <a> tag for a single wiki link.

    `target` is the raw link target (may include #anchor).
    `label` is the optional pipe-separated display text (or None).
    `known_slugs` is a set of existing page slugs — used to flag
    red-links for missing pages so authors notice broken refs.
    """
    target = (target or '').strip()
    label = (label or '').strip() or target
    anchor = ''
    if '#' in target:
        target, anchor = target.split('#', 1)
        anchor = '#' + slugify(anchor, allow_hash=True)
    slug = slugify(target)
    href = f'/wiki/{slug}{anchor}'
    cls = 'wiki-link' if slug in known_slugs else 'wiki-link wiki-link-missing'
    title = '' if slug in known_slugs else ' title="page does not exist yet — click to create"'
    return f'<a href="{href}" class="{cls}"{title}>{escape(label)}</a>'


_FENCE_RE = re.compile(r'(?ms)^```.*?^```', re.MULTILINE)
_INLINE_CODE_RE = re.compile(r'`[^`\n]+`')
_PLACEHOLDER_RE = re.compile(r'\x00WIKI(INLINE|FENCE)(\d+)\x00')


def preprocess_wikilinks(body, known_slugs):
    """Replace every [[...]] token with rendered anchor HTML.

    Skips text inside fenced code blocks (```…```) and inline code
    spans (`…`) so example tokens like `[[Page Name]]` displayed
    inside docs aren't mistaken for real links.
    """
    if not body:
        return ''

    def repl(m):
        return _build_wikilink_html(m.group(1), m.group(2), known_slugs)

    # 1. Pull out fenced blocks → placeholders.
    fences = []
    def fence_sub(m):
        fences.append(m.group(0))
        return f'\x00WIKIFENCE{len(fences) - 1}\x00'
    protected = _FENCE_RE.sub(fence_sub, body)

    # 2. Pull out inline `code` spans → placeholders.
    inlines = []
    def inline_sub(m):
        inlines.append(m.group(0))
        return f'\x00WIKIINLINE{len(inlines) - 1}\x00'
    protected = _INLINE_CODE_RE.sub(inline_sub, protected)

    # 3. Replace wiki-links in the non-code text.
    protected = _WIKILINK_RE.sub(repl, protected)

    # 4. Restore.
    #
    # Real perf bug found live (same shape as web/render_msg.py's fixed
    # reflow bug): calling .replace() once per placeholder against the
    # *whole* (already-restored, ever-growing) `protected` string made
    # this O(n^2) in the combined body length -- confirmed 3+ seconds on
    # a synthetic ~470KB page with ~12,000 code spans, uncached and
    # re-run on every single page view. A single regex pass resolving
    # every placeholder at once (fences and inline spans together,
    # order-independent since each match already carries its own kind
    # and index) is one linear scan instead of N whole-string rescans.
    def _restore(m):
        kind, idx = m.group(1), int(m.group(2))
        return inlines[idx] if kind == 'INLINE' else fences[idx]
    return _PLACEHOLDER_RE.sub(_restore, protected)


def render(body, known_slugs=None):
    """Render a wiki body to safe HTML.

    Steps:
      1. Pre-process [[wiki-links]] into anchor HTML (so the markdown
         parser doesn't choke on the brackets).
      2. Run through python-markdown with fenced code, tables, nl2br,
         attr_list, toc, sane_lists.
      3. Sanitize via bleach with a small whitelist that allows our
         wiki-link class plus headings / tables / images / code.

    Real XSS gap found in a pre-release security audit: step 3 used to
    be wrapped in `try/except ImportError: pass`, so if bleach happened
    to be missing (a hard requirement per setup.py, but a stale/partial
    venv could still lack it) the RAW, unsanitized markdown output went
    straight into Markup() -- and python-markdown passes embedded raw
    HTML through unchanged by default. Wiki editing only requires
    @login_required (any registered user, not admin-only -- see
    web/wiki.py's edit()), so this was reachable by any account, not
    just trusted sysops, and would fire for every visitor of that page.
    Now: if bleach is unavailable, fall back to the same safe escape-
    then-render path already used when `markdown` itself is missing.
    """
    if not body:
        return Markup('')
    known_slugs = set(known_slugs or ())

    # Step 1: wiki-links → HTML anchors
    pre = preprocess_wikilinks(body, known_slugs)

    def _escaped_fallback():
        return '<p>' + str(escape(pre)).replace('\n', '<br>') + '</p>'

    # Step 2: markdown
    try:
        import markdown as _md
        html = _md.markdown(
            pre,
            extensions=['fenced_code', 'tables', 'nl2br',
                        'attr_list', 'toc', 'sane_lists'],
            output_format='html5',
        )
    except ImportError:
        # No markdown package — escape and just preserve newlines.
        return Markup(_escaped_fallback())  # nosec B704 -- _escaped_fallback() escape()s pre

    # Step 3: sanitize
    try:
        import bleach as _bleach
    except ImportError:
        # bleach missing: do NOT emit the unsanitized markdown output
        # (see docstring) -- degrade to the same safe plain-text path.
        return Markup(_escaped_fallback())  # nosec B704 -- _escaped_fallback() escape()s pre

    allowed_tags = _bleach.sanitizer.ALLOWED_TAGS | {
        'p', 'pre', 'br', 'hr', 'div', 'span',
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'table', 'thead', 'tbody', 'tr', 'th', 'td',
        'img', 'sup', 'sub', 'kbd', 'mark', 'ins', 'del',
    }
    allowed_attrs = {
        'a': ['href', 'title', 'class', 'id', 'name'],
        'img': ['src', 'alt', 'title', 'width', 'height'],
        'span': ['class', 'id'],
        'div': ['class', 'id'],
        'h1': ['id'], 'h2': ['id'], 'h3': ['id'],
        'h4': ['id'], 'h5': ['id'], 'h6': ['id'],
        'th': ['align', 'colspan', 'rowspan'],
        'td': ['align', 'colspan', 'rowspan'],
        'code': ['class'],
    }
    html = _bleach.clean(
        html, tags=allowed_tags, attributes=allowed_attrs,
        protocols=['http', 'https', 'mailto', 'ftp', 'gopher'],
        strip=True)

    return Markup(html)  # nosec B704 -- bleach.clean() output, allowlisted tags/attrs


def extract_outgoing_links(body):
    """Return a set of slugs the body links to via [[wiki-link]].

    Skips wiki-link tokens inside fenced code blocks and inline
    `code` spans — they're examples, not real refs.
    """
    out = set()
    if not body:
        return out
    # Strip fenced blocks + inline code BEFORE scanning so example
    # tokens don't pollute the result.
    stripped = _FENCE_RE.sub('', body)
    stripped = _INLINE_CODE_RE.sub('', stripped)
    for m in _WIKILINK_RE.finditer(stripped):
        target = (m.group(1) or '').strip()
        if '#' in target:
            target = target.split('#', 1)[0]
        if target:
            out.add(slugify(target))
    return out
