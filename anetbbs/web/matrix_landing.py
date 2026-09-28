# anetbbs/web/matrix_landing.py
"""Stock pre-login web "Matrix" landing page -- a connection-options
page shown to a logged-out visitor before the normal home page,
gated behind Config.WEB_MATRIX_ENABLED (Admin -> Settings, off by
default).

This is a real, built-in feature (unlike a data/mods/core/web_landing.py
override, which is a full custom-code escape hatch a sysop drops in
themselves) -- promoted from a sysop's own private mod to a proper
admin-toggleable capability at Jerry's request (2026-09-29), matching
the same "started as a mod, proved out, got promoted to core" path the
terminal lightbar login menu itself already took. A sysop who wants
full custom logic/copy still uses a data/mods/core/web_landing.py
override, which main.py's index() checks FIRST and which always wins
over this stock page when present.

Shown once per browser session (a plain cookie, same mechanism the
original mod used) -- never re-nags on every page load, and (per the
caller in main.py) never shown to an already-authenticated visitor.
"""
from flask import make_response, redirect, url_for

SKIP_COOKIE = 'anetbbs_matrix_seen'
SKIP_PARAM = 'mtx'


def render_stock_web_matrix(request):
    """Returns a Flask response to show instead of the home page, or
    None to fall through to the normal home page unchanged."""
    if request.cookies.get(SKIP_COOKIE):
        return None

    if request.args.get(SKIP_PARAM) == 'skip':
        resp = make_response(redirect(url_for('main.index')))
        resp.set_cookie(SKIP_COOKIE, '1')  # session cookie: no max_age
        return resp

    from ..features.bbs_ui import _app
    cfg = _app().config
    bbs_name = cfg.get('BBS_NAME', 'ANetBBS')
    domain = (cfg.get('BBS_DOMAIN', '') or '').strip()

    ways = []
    if cfg.get('TELNET_ENABLED', True):
        ways.append(f"Telnet &mdash; port {cfg.get('TELNET_PORT', 2233)}")
    if cfg.get('SSH_ENABLED', True):
        ways.append(f"SSH &mdash; port {cfg.get('SSH_PORT', 2234)}")
    if cfg.get('RLOGIN_ENABLED', False):
        ways.append(f"Rlogin &mdash; port {cfg.get('RLOGIN_PORT', 513)}")
    connect_host = domain or request.host.split(':')[0]
    ways_html = ''.join(f'<li>{w} to <code>{connect_host}</code></li>' for w in ways)

    continue_url = url_for('main.index', **{SKIP_PARAM: 'skip'})

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{bbs_name} &mdash; Connect</title>
<style>
  body {{ background:#0b0b12; color:#c8ffd4; font-family: 'Courier New', monospace;
          display:flex; align-items:center; justify-content:center;
          min-height:100vh; margin:0; padding:16px; }}
  .card {{ max-width:560px; border:1px solid #2f6d3f; border-radius:6px;
           padding:28px; background:#0f1a12; }}
  h1 {{ color:#39ff6a; font-size:1.4rem; margin:0 0 4px; }}
  p.tagline {{ color:#6fae7f; margin:0 0 20px; }}
  .opt {{ display:block; text-align:center; padding:14px 16px; margin:10px 0;
          border-radius:4px; text-decoration:none; font-weight:bold; }}
  .opt.primary {{ background:#123d1f; color:#7dffab; border:1px solid #39ff6a; }}
  .opt.primary:hover {{ background:#1a5a2c; }}
  .opt.secondary {{ background:transparent; color:#8fbf9c; border:1px solid #2f6d3f; }}
  .opt.secondary:hover {{ background:#132018; color:#c8ffd4; }}
  ul {{ color:#8fbf9c; font-size:0.92rem; padding-left:18px; }}
  code {{ color:#c8ffd4; }}
</style>
</head>
<body>
  <div class="card">
    <h1>{bbs_name}</h1>
    <p class="tagline">Choose how you'd like to connect.</p>
    <a class="opt primary" href="/terminal/">Connect in the Browser (ANSI terminal)</a>
    <a class="opt secondary" href="{continue_url}">Continue to the website &rarr;</a>
    <p>Prefer a real terminal client? Connect directly:</p>
    <ul>{ways_html}</ul>
  </div>
</body>
</html>"""
    return html
