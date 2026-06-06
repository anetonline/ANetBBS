# ANetBBS v1.0a2.84 — Fix: custom ANSI menu files not loading

## What's fixed

### Custom `.ans` menu files were silently ignored

The `load_menu_ansi()` helper introduced in v1.0a2.83 used
`os.environ.get('DATA_DIR', '')` to locate the files. `DATA_DIR` is never
written to `.env` — it's derived from `__file__` at runtime inside `config.py`
and not exported as an environment variable. The result was that
`load_menu_ansi()` always got an empty string, returned `None` immediately, and
fell back to the built-in banner every time.

Fixed: the path is now computed from `__file__` directly, the same way
`config.py` derives `BASE_DIR` / `DATA_DIR`:

```
<install_root>/anetbbs/features/ansi_ui.py
              ↑ parent ↑ parent ↑ parent → install root → + data/
```

No configuration change needed — drop your `.ans` files in place and they
will be picked up immediately.
