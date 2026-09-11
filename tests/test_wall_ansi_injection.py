"""Regression tests for anetbbs/features/wall.py's ANSI/control-byte
injection fix.

Real vulnerability found in a security/performance audit: unlike
mrc_chat.py's own _pipe_to_ansi()/_strip_pipe() (fixed in an earlier
audit pass), wall.py's pipe-code renderer only ever rewrote |NN tokens
-- it never stripped any raw ESC byte already present in a post's text.
WallPost.line1/line2/display_name/username aren't only ever
locally-typed content: sync_wall_inbound() (anetbbs/echomail/
interbbs_sync.py) also materializes WallPost rows straight from inbound
echomail message bodies relayed by OTHER BBSes on the ANET_WALL
InterBBS network, with only a 200-char truncation and a word-filter
applied -- no ANSI/control-byte stripping of its own. A malicious/
compromised peer BBS could post a wall message containing a raw ANSI/
CSI escape sequence, synced in and rendered straight to every local
caller's real terminal that opens the wall.

Fixed by stripping well-formed CSI/OSC sequences and the whole C0
control range (+ DEL) at render time, in _pipe_to_ansi()/_strip_pipes()
and in _render_post()'s uname extraction -- the same fix shape already
used in mrc_chat.py.
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.features import wall


class _FakePost:
    def __init__(self, line1='', line2=None, display_name=None,
                 username='alice', node=1, post_id=1):
        import datetime
        self.created_at = datetime.datetime(2026, 1, 1, 12, 0, 0)
        self.line1 = line1
        self.line2 = line2
        self.display_name = display_name
        self.username = username
        self.node = node
        self.id = post_id


_EVIL = '\x1b[2J\x1b[HEvilPost'


def test_pipe_to_ansi_strips_raw_csi_sequence():
    out = wall._pipe_to_ansi(_EVIL)
    assert '\x1b[2J' not in out
    assert '\x1b[H' not in out or out.count('\x1b[H') == 0
    assert 'EvilPost' in out


def test_pipe_to_ansi_still_translates_legitimate_pipe_colors():
    out = wall._pipe_to_ansi('|12red |09blue')
    assert '\x1b[' in out
    assert 'red' in out and 'blue' in out


def test_strip_pipes_strips_raw_csi_sequence():
    out = wall._strip_pipes(_EVIL)
    assert '\x1b' not in out
    assert 'EvilPost' in out


def test_strip_pipes_still_removes_pipe_codes():
    out = wall._strip_pipes('|12red |09blue')
    assert '|' not in out
    assert out == 'red blue'


def test_render_post_ansi_mode_strips_injected_escape_from_line1():
    post = _FakePost(line1=_EVIL)
    out = wall._render_post(post, is_admin=False, user_c='', W=79, ascii_mode=False)
    text = out.decode('cp437')
    assert '\x1b[2J' not in text
    assert 'EvilPost' in text


def test_render_post_ascii_mode_strips_injected_escape_from_line1():
    post = _FakePost(line1=_EVIL)
    out = wall._render_post(post, is_admin=False, user_c='', W=79, ascii_mode=True)
    text = out.decode('cp437')
    assert '\x1b[2J' not in text
    assert 'EvilPost' in text


def test_render_post_strips_injected_escape_from_remote_display_name():
    post = _FakePost(line1='hi', display_name=_EVIL)
    out = wall._render_post(post, is_admin=False, user_c='', W=79, ascii_mode=False)
    text = out.decode('cp437')
    assert '\x1b[2J' not in text
    assert 'EvilPost' in text
