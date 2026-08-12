# anetbbs/core/mods_override.py
"""Generic sysop override loader for ANetBBS's own core Python code --
extends the data/mods/ tree (already covering Synchronet-compat door
scripts via games/synchronet_compat.py's load(), and ANSI/menu text
screens via features/ansi_ui.py and core/session.py's
_show_ansi_screen) to ANetBBS's native core.

Real Synchronet precedent, per Jerry directly: login.js and logon.js
are core system scripts (not doors) that Synchronet's own engine
loads by filename from exec/, and a sysop's modified copy in mods/ is
used automatically instead -- there's no door-only carve-out on
Synchronet's side, because in Synchronet everything (doors AND the
core login/logon flow) is just a script loaded by filename. ANetBBS's
own core isn't script-driven the same way -- login_screen() etc. are
compiled-in Python methods, not files loaded by name at runtime -- so
a matching override point has to be built explicitly per call site
rather than falling out "for free". This module is that mechanism,
written so any future core screen can opt in the same way the login
menu does (see core/session.py's login_screen()).

A sysop's override is a COMPLETE replacement file, not a diff or a
patch -- data/mods/core/<name>.py defining the expected function --
matching Synchronet's own "keep a modified full copy" convention for
login.js/logon.js in mods/.
"""
import importlib.util
import logging
import os

logger = logging.getLogger(__name__)


async def call_core_override(mod_name: str, func_name: str, stock_fn, *args):
    """Try data/mods/core/<mod_name>.py's <func_name>(*args) first --
    fall back to stock_fn() (a zero-arg async callable, so the caller
    controls exactly what stock arguments get bound without us ever
    building an un-awaited coroutine) if the override file doesn't
    exist, fails to import, or doesn't define the expected function.
    A broken sysop override degrades to stock behavior instead of
    taking down whatever called this (a login attempt, etc).

    Re-read from disk on every call, not cached -- these are
    infrequent (once per login, say), so there's no real cost, and a
    sysop editing their override live doesn't need to restart the
    whole BBS to see it take effect, matching how the mods/text/ ANSI
    overrides already behave.
    """
    try:
        from ..features.bbs_ui import _app
        data_dir = _app().config.get('DATA_DIR', '')
        override_path = os.path.join(data_dir, 'mods', 'core', f'{mod_name}.py')
        if os.path.isfile(override_path):
            spec = importlib.util.spec_from_file_location(
                f'anetbbs_mods_core_{mod_name}', override_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            fn = getattr(module, func_name, None)
            if fn is not None:
                return await fn(*args)
            logger.warning(
                "data/mods/core/%s.py exists but has no %s() -- "
                "falling back to the stock version", mod_name, func_name)
    except Exception:
        logger.exception(
            "data/mods/core/%s.py failed to load/run -- falling back "
            "to the stock version", mod_name)
    return await stock_fn()
