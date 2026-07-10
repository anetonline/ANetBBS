"""Minimal markdown -> plain text for the terminal Ask Anet door's pager.

Wiki page bodies are raw markdown source (unlike the RSS reader's
already-plain summaries). This is not a full parser -- good enough for
readability in a terminal pager, not fidelity.
"""
import re

_WIKILINK_RE = re.compile(r'\[\[([^\[\]\|]+)(?:\|([^\[\]]+))?\]\]')
_STEPS = [
    (re.compile(r'^#{1,6}\s*', re.MULTILINE), ''),
    (re.compile(r'\*\*([^*]+)\*\*'), r'\1'),
    (re.compile(r'\*([^*]+)\*'), r'\1'),
    (re.compile(r'`([^`]+)`'), r'\1'),
    (re.compile(r'^\s*[-*]\s+', re.MULTILINE), '  - '),
]


def markdown_to_plain(body):
    text = _WIKILINK_RE.sub(lambda m: m.group(2) or m.group(1), body or '')
    for pat, repl in _STEPS:
        text = pat.sub(repl, text)
    return text
