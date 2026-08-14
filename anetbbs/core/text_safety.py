# anetbbs/core/text_safety.py
"""Shared helper for a real vulnerability class found in a security
audit, independently in three different subsystems: any place text
that ORIGINATED FROM SOMEONE ELSE (a remote FTN/QWK peer's message
sender name, another user's chat message, etc.) gets written to a
DIFFERENT user's real ANSI terminal without stripping raw escape
sequences first. A malicious sender can embed ANSI/CSI/OSC control
sequences to clear the screen, spoof a fake prompt, hide/reposition
text, or otherwise manipulate the viewing user's terminal -- classic
BBS "ANSI bomb" territory, and it requires no privilege on the
attacker's part: FTN/QWK/IRC/MRC senders don't need a local account.

Not needed for text a user only ever sees echoed back to their OWN
session (that's already under their own control), and not needed
where the data never reaches a real terminal (web UI, logs) -- this
is specifically for cross-user/cross-network display paths.
"""
import re

# Strips well-formed CSI ("\x1b[...letter") and OSC ("\x1b]...BEL-or-ST")
# sequences, plus a few other common bare ESC+char forms (cursor save/
# restore, character-set select, full reset), cleanly -- so ordinary
# chat/message text doesn't come out with ugly bracket/digit residue.
_ESCAPE_SEQUENCE_RE = re.compile(
    r'\x1b(?:\[[0-9;?]*[A-Za-z]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[()][0-9A-Za-z]|[A-Za-z0-9=><~])')

# The actual safety net: strips the ENTIRE C0 control range (+ DEL),
# so a bare or malformed ESC -- or any other control byte -- can never
# survive even if it doesn't match the tidy pattern above. Untrusted
# single-line text (message titles, sender names, chat bodies) has no
# legitimate reason to contain any C0 control byte at all.
_CONTROL_BYTE_RE = re.compile(r'[\x00-\x1f\x7f]')


def strip_untrusted_escapes(text):
    """Remove ANSI escape sequences and other C0 control bytes from
    text that came from someone other than the viewer, before it's
    written to their terminal. Returns '' for a falsy/None input."""
    if not text:
        return ''
    return _CONTROL_BYTE_RE.sub('', _ESCAPE_SEQUENCE_RE.sub('', text))
