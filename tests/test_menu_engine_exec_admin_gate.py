"""Regression test for a defense-in-depth gap found in a full
access-control audit: anetbbs/features/menu_engine.py's action-type
dispatch has no gate of its own -- access is enforced only by which
hotkeys get offered to a user (min_access filtering). The 'exec'
action type runs an arbitrary sysop-configured shell command as the
BBS service user with no independent backstop, so a custom menu item
accidentally left at the model's default min_access=0 would let any
logged-in user run it (menu_admin.py's own seed_samples() ships
exactly this: a sample exec item with no min_access override).
_act_exec() now hard-requires is_admin regardless of the menu item's
own min_access, since this action type is sysop-only by design.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.features.menu_engine import _act_exec


class _FakeSession:
    def __init__(self, user):
        self.user = user
        self.written = []

    async def write(self, text):
        self.written.append(text)


class _FakeUI:
    def __init__(self, user):
        self.session = _FakeSession(user)


def test_non_admin_cannot_run_exec_action():
    ui = _FakeUI({'id': 1, 'username': 'regularuser', 'is_admin': False})
    result = asyncio.run(_act_exec(ui, 'echo hello'))
    assert result is None
    assert any('Access denied' in w for w in ui.session.written)


def test_admin_can_still_reach_the_exec_config_parser():
    ui = _FakeUI({'id': 2, 'username': 'sysop', 'is_admin': True})
    # No 'cmd' -- should get past the admin gate and hit the config
    # error path, not the access-denied path.
    result = asyncio.run(_act_exec(ui, ''))
    assert result is None
    assert any('action_args required' in w for w in ui.session.written)
    assert not any('Access denied' in w for w in ui.session.written)


def test_user_with_no_session_user_dict_is_denied():
    ui = _FakeUI(None)
    result = asyncio.run(_act_exec(ui, 'echo hello'))
    assert result is None
    assert any('Access denied' in w for w in ui.session.written)


def test_username_with_a_space_is_not_word_split_into_the_shell_command():
    """Regression test for a real Low-severity finding from a security/
    performance audit (2026-08-31): {user}/{userid}/{dropdir} used to
    be spliced into the shell command string with no quoting at all.
    Usernames are restricted at registration (auth.py's RegisterForm)
    but the allowed charset still includes spaces and apostrophes --
    either can still break a sysop-authored command template's own
    quoting, or (for an unquoted template, the documented convention --
    see docs/05-external-programs.md's `lord.sh -drop {dropdir}`
    example) split what should be ONE argument into several.

    `printf '[%s]' {user}` reveals argument boundaries directly: one
    bracket pair per argument printf actually received. An unquoted
    substitution of a two-word username produces TWO bracket pairs
    (word-split into separate shell words); a properly quoted one
    produces exactly ONE, with the space preserved inside it."""
    ui = _FakeUI({'id': 1, 'username': 'John Doe', 'is_admin': True})
    asyncio.run(_act_exec(ui, "printf '[%s]' {user}"))
    output = ''.join(ui.session.written)
    assert '[John Doe]' in output, output
    assert '[John][Doe]' not in output, output


def test_username_with_an_apostrophe_cannot_break_out_of_template_quoting():
    """The other half of the same fix, using the documented unquoted
    convention (docs/05-external-programs.md's own `lord.sh -drop
    {dropdir}` example -- sysops are not expected to add their own
    quotes around these tokens). Before this fix, an apostrophe in the
    username spliced in raw could still close/reopen quoting elsewhere
    in a sysop's template if one happened to be nearby; shlex.quote()
    escapes it so the username always reaches the child process as one
    literal argument, apostrophe included, regardless of what
    surrounds it in the template."""
    ui = _FakeUI({'id': 2, 'username': "O'Brien", 'is_admin': True})
    asyncio.run(_act_exec(ui, "printf '[%s]' {user}"))
    output = ''.join(ui.session.written)
    assert "[O'Brien]" in output, output
