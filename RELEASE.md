# ANetBBS v1.0a2.146 — Fix dial-out directory: wrap DB query in app_context

## Changes

Root cause: `_load_directory()` in `features/dialout.py` queried the database
without a Flask `app_context()`. The terminal process (telnet/SSH) runs outside
gunicorn and has no ambient Flask context, so SQLAlchemy raised an exception
that was silently swallowed by `except Exception: pass`, causing the function
to always fall back to `DEFAULT_DIRECTORY`.

Both the original `DialoutDestination` query and the new `PeerBbs` query were
broken for the same reason. Added `with _app().app_context():` wrapping both
queries — the same pattern used everywhere else in terminal-side DB code
(bbs_ui.py, menu_engine.py, session.py).

Files changed: `features/dialout.py`
