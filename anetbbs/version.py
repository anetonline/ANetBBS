"""Single source of truth for the running ANetBBS version.

`VERSION` is a string read once at import time from the top-level
``VERSION`` file shipped with the source tree. Everything that wants
to display the version (CLI ``--version`` flags, web nav footer,
admin dashboard) imports from here.

If the VERSION file is missing for some reason (corrupted install,
local hack), we fall back to ``'unknown'`` rather than crashing —
the rest of the BBS keeps working.
"""
import os


def _read_version() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    candidates = [
        os.path.join(root, 'VERSION'),
        # Some legacy installs kept VERSION inside the package dir
        os.path.join(here, 'VERSION'),
    ]
    for path in candidates:
        try:
            with open(path, 'r') as fh:
                v = fh.read().strip()
            if v:
                return v
        except OSError:
            continue
    return 'unknown'


VERSION = _read_version()
"""Cached version string. Re-read with ``reload(anetbbs.version)`` after
swapping the VERSION file out (e.g. during an update.sh run)."""
