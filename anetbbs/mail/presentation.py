"""Shared, non-destructive display formatting for Internet Mail bodies."""
import re

from markupsafe import Markup, escape


_QUOTE_INTRO_RE = re.compile(r'^\s*.+\bwrote:\s*$', re.IGNORECASE)
_ANSI_ESCAPE_RE = re.compile(
    r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[()][0-9A-Za-z])')
_CONTROL_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]')


def display_lines(body):
    """Return display lines with excessive blank space normalized.

    Stored message content is never changed. The single blank line between
    paragraphs is kept; longer blank runs and blank padding at the edges are
    reduced for readable WebUI and terminal views.
    """
    text = (body or '').replace('\r\n', '\n').replace('\r', '\n')
    text = _ANSI_ESCAPE_RE.sub('', text)
    text = _CONTROL_RE.sub('', text)
    raw_lines = text.split('\n')
    lines = []
    blank_pending = False
    for line in raw_lines:
        if not line.strip():
            if lines:
                blank_pending = True
            continue
        if blank_pending:
            lines.append('')
            blank_pending = False
        lines.append(line)

    result = []
    for index, line in enumerate(lines):
        quoted = line.lstrip().startswith('>')
        if not quoted and _QUOTE_INTRO_RE.match(line):
            quoted = any(next_line.lstrip().startswith('>')
                         for next_line in lines[index + 1:]
                         if next_line.strip())
        result.append((line, quoted))
    return result


def render_body_html(body):
    """Render a safe plain-text body with visually distinct quoted lines."""
    # Markup.format()/join() escape every non-Markup argument, so each
    # line is escaped by markupsafe itself; only the constant span wrapper
    # is trusted HTML.
    quoted_tpl = Markup('<span class="mail-quoted">{}</span>')
    rendered = []
    for line, quoted in display_lines(body):
        rendered.append(quoted_tpl.format(line) if quoted else escape(line))
    return Markup('\n').join(rendered)
