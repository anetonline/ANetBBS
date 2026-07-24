"""Regression test for a real bug reported live: an inbound echomail
message from a real Synchronet/SBBSecho peer hard-wraps long paragraphs
at a fixed column (~79 chars) with no word-boundary awareness and no
soft-wrap marker -- each of those breaks is a real FTS-0001 line
terminator, so anetbbs/web/render_msg.py correctly rendered every one
as a real line break, but that produced choppy, sometimes mid-word-
split output whenever the sender's wrap width differs from ours.

reflow_hard_wrapped_body() conservatively rejoins lines that look like
a fixed-column wrap artifact, while leaving ASCII art, rule lines,
quoted replies, and list items untouched.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.web.render_msg import reflow_hard_wrapped_body, render_msg_body


def test_mid_word_hard_wrap_is_rejoined():
    # Realistic ~79-col SBBSecho-style hard wrap, breaking mid-word with
    # no soft-wrap marker -- the exact shape of the live bug report.
    text = (
        "Hi all - Codefenix here. Quick intro.I've been a fan of BBSing, ANSI art, and al\n"
        "l things DOS since I was a teen. My dad managed the local RadioShack in my homet\n"
        "own, so I grew up surrounded by Tandy computers and spent countless hours putzin\n"
        "g around with them."
    )
    result = reflow_hard_wrapped_body(text)
    assert 'all things DOS' in result
    assert 'hometown' in result
    # "putzing" is informal slang, not in the bundled dictionary -- an
    # accepted, documented miss (falls back to a space join, same as
    # the pre-fix behavior for that one word) rather than a regression.
    assert '\n' not in result  # one continuous paragraph


def test_real_paragraph_breaks_are_preserved():
    text = (
        "This first paragraph is short.\n"
        "\n"
        "This is a completely separate second paragraph that talks about something else entirely and stands on its own."
    )
    result = reflow_hard_wrapped_body(text)
    assert result.count('\n') >= 1
    assert 'This first paragraph is short.' in result.split('\n')[0]


def test_ascii_art_box_drawing_is_never_touched():
    text = (
        "╔" + "═" * 40 + "╗\n"
        "║ some banner text right here that is long enough to matter ║\n"
        "╚" + "═" * 40 + "╝"
    )
    result = reflow_hard_wrapped_body(text)
    assert result == text


def test_rule_line_is_not_joined_into_surrounding_prose():
    text = (
        "A fairly long line of regular prose text that goes on and on past the wrap column here\n"
        + "=" * 70 + "\n"
        "More regular prose continues down here after the rule line separator."
    )
    result = reflow_hard_wrapped_body(text)
    assert "=" * 70 in result.split('\n')

    lines = result.split('\n')
    rule_idx = next(i for i, l in enumerate(lines) if l.startswith('====='))
    assert lines[rule_idx - 1] != lines[rule_idx]


def test_quoted_reply_lines_are_not_joined_with_prose_above():
    text = (
        "A fairly long line of regular prose text that goes on and on past the wrap column here\n"
        "> this is a quoted reply line that should stand on its own not get merged in"
    )
    result = reflow_hard_wrapped_body(text)
    lines = result.split('\n')
    assert any(l.startswith('>') for l in lines)


def test_short_lines_are_not_forced_together():
    text = "Line one.\nLine two.\nLine three."
    result = reflow_hard_wrapped_body(text)
    assert result == text


def test_render_msg_body_applies_reflow_end_to_end():
    text = (
        "Hi all - Codefenix here. Quick intro.I've been a fan of BBSing, ANSI art, and al\n"
        "l things DOS since I was a teen."
    )
    html = str(render_msg_body(text))
    assert 'all things DOS' in html
