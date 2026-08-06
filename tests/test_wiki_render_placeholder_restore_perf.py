"""Regression test for a real perf bug found live: preprocess_wikilinks()
restored fenced-code/inline-code placeholders by calling .replace() once
per placeholder against the *whole* (already-partially-restored) body --
same O(n^2) shape as the render_msg.py reflow bug fixed earlier the same
session. Confirmed 3+ seconds on a ~470KB synthetic page with ~12,000
inline code spans; a single regex pass resolving every placeholder at
once fixes it while preserving identical output.
"""
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.wiki.render import preprocess_wikilinks


def test_wikilinks_and_code_spans_render_correctly():
    body = (
        'Hi [[Page One]] and [[Page Two|display]] and '
        '`inline code [[not a link]]` and a fence:\n'
        '```\ncode [[also not a link]]\n```\nend.'
    )
    out = preprocess_wikilinks(body, {'page-one'})
    assert '<a href="/wiki/page-one" class="wiki-link">Page One</a>' in out
    assert 'display</a>' in out
    # Tokens inside inline code / fenced blocks must stay literal, not
    # get treated as real [[wiki-links]].
    assert 'inline code [[not a link]]' in out
    assert 'code [[also not a link]]' in out


def test_placeholder_restore_is_not_quadratic():
    words = 'the quick brown fox jumps over the lazy dog'.split()
    random.seed(4)
    parts = [f'{random.choice(words)} `code{i}` ' for i in range(12000)]
    body = ' '.join(parts)
    t0 = time.time()
    preprocess_wikilinks(body, set())
    elapsed = time.time() - t0
    # Was 3+ seconds before the fix; generously bounded well under that.
    assert elapsed < 1.0
