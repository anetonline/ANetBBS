"""Shared Markdown -> sanitized HTML rendering.

Used by the `markdown` Jinja filter (anetbbs/web_app.py) and by the
per-message "render markdown on demand" JSON endpoints (echomail, netmail,
boards, pm) -- factored out so both call sites share the exact same
bleach-sanitization behavior instead of drifting apart.
"""
from markupsafe import Markup, escape


def render_markdown(value) -> Markup:
    """Render Markdown to safe HTML.

    Uses the `markdown` package if available, with sane defaults: hard line
    breaks (so users don't have to type two spaces at end of line), fenced
    code, tables. The output is run through an HTML cleaner via bleach
    before ever reaching Markup().

    Real XSS gap found in a pre-release security audit: the previous
    version wrapped the bleach pass in `try/except ImportError: pass`,
    which meant that if bleach happened to be missing (both it and
    `markdown` are hard requirements per setup.py/requirements.txt, but
    an install with a stale/partial venv that predates bleach being
    added, or a broken pip sync, could still be missing it) the RAW,
    UNSANITIZED markdown output went straight into Markup() -- and
    python-markdown passes embedded raw HTML through unchanged by
    default. This filter renders echomail/netmail bodies, which can
    originate from untrusted external FidoNet/QWK network peers, not
    just local users -- a compromised or malicious peer could have
    shipped a stored-XSS payload that would fire for anyone viewing it
    on an install missing bleach. Now: if bleach is unavailable, fall
    back to the SAME safe escape-then-render path already used when
    `markdown` itself is missing, instead of ever emitting
    unsanitized HTML.
    """
    if not value:
        return Markup('')

    def _escaped_fallback():
        return '<p>' + str(escape(value)).replace('\n', '<br>') + '</p>'

    try:
        import markdown as _md
        html = _md.markdown(
            str(value),
            extensions=['fenced_code', 'nl2br', 'tables'],
            output_format='html5',
        )
    except ImportError:
        # No markdown package — escape and just preserve newlines.
        return Markup(_escaped_fallback())  # nosec B704 -- _escaped_fallback() escape()s value

    try:
        import bleach as _bleach
    except ImportError:
        # bleach missing: do NOT emit the unsanitized markdown output
        # (see docstring) -- degrade to the same safe plain-text path.
        return Markup(_escaped_fallback())  # nosec B704 -- _escaped_fallback() escape()s value

    allowed = _bleach.sanitizer.ALLOWED_TAGS | {
        'p', 'pre', 'br', 'hr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'table', 'thead', 'tbody', 'tr', 'th', 'td', 'img'}
    html = _bleach.clean(html, tags=allowed,
                         attributes={'a': ['href', 'title'],
                                     'img': ['src', 'alt', 'title']},
                         strip=True)
    return Markup(html)  # nosec B704 -- bleach.clean() output, allowlisted tags/attrs
