# anetbbs/features/petscii_ui.py
"""
PETSCII terminal-mode menu loop for Commodore 64/128 users (see the
"PETSCII Terminal Support (Phase 1)" plan). Deliberately a fully separate
rendering path from anetbbs/features/menu_engine.py's run_menu() /
bbs_ui.py's BBSMenuUI -- those are cursor-addressed-ANSI-native with no
plain-text fallback anywhere, so this is a hand-built, hardcoded menu
using only plain text + anetbbs/features/petscii_codec.py's control-code
constants, reusing the SAME underlying data-layer models/queries the
ANSI screens use, never their rendering methods.

Current status: vertical-slice placeholder. This lets a sysop confirm
the PETSCII listener/codec/control-codes actually work end-to-end on a
real C64 client or VICE emulator BEFORE the real menu screens (boards,
echomail, PM, files, who's-online, profile) are built out -- see the
plan's staged build order. Games/doors/ANetCRAFT/DarkForces/MRC chat/IRC
are intentionally never offered here at all in Phase 1, not just marked
unavailable.
"""
from .petscii_codec import REVERSE_ON, REVERSE_OFF


async def run_petscii_menu(session):
    """Entry point called from BBSSession right after login when
    term_mode == 'petscii'. Placeholder for the real Phase-1 menu (see
    module docstring) -- confirms the PETSCII wire path works end to end.

    The upper/lowercase charset switch happens once, as early as
    possible (anetbbs/core/petscii_server.py's handle_connection(),
    before session.start() ever runs) -- NOT here, since login itself
    needs it too (a real hardware bug: without it, every password typed
    during login arrives case-flattened to uppercase and authentication
    always fails)."""
    width = getattr(session, 'petscii_width', 40)
    username = (session.user or {}).get('username', 'friend') if isinstance(session.user, dict) else 'friend'

    await session.clear_screen()
    await session.write(f'{REVERSE_ON} ANetBBS -- PETSCII Mode {REVERSE_OFF}\r\n\r\n')
    await session.write(f'Hi, {username}! You are connected via PETSCII\r\n')
    await session.write(f'at {width} columns.\r\n\r\n')
    await session.write('The full menu (message boards, echomail,\r\n'
                         'private messages, file areas) is still\r\n'
                         'being built -- this confirms the connection\r\n'
                         'itself is working correctly.\r\n\r\n')
    await session.read_line('Press ENTER to disconnect...')
