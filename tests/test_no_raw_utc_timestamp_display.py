"""Guard test against regressing the Eastern-time display fix.

Every timestamp display in the app used to be raw, unconverted UTC --
sysops had asked repeatedly for Eastern (EST/EDT) display and it had
never actually been implemented anywhere. Fixed by adding a single
shared conversion point (anetbbs/core/tz.py: to_eastern()/
fmt_eastern(), plus the `eastern` Jinja filter registered in
web_app.py) and converting every display site (162 template
occurrences + 30 terminal/PETSCII-UI occurrences + a handful of Python
view pre-formatting sites) to go through it.

This test scans for new `.strftime(` calls added directly on a
datetime in a template or in the terminal/PETSCII UI files WITHOUT
going through the `eastern` filter / fmt_eastern() helper -- catching
a future regression before it ships, the same way this bug went
unfixed for a long time because nothing caught it. A short, deliberate
allowlist covers genuine machine-format / protocol call sites that
correctly stay UTC (sitemap XML, RSS pubDate, QWK/BinkP wire formats,
JSON APIs, filenames) -- see each entry's comment for why.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# file (relative to repo root) -> set of 1-based line numbers that are
# a deliberate, reviewed exception -- not a gap.
ALLOWLIST = {
    # sitemap.xml <lastmod> -- machine format, SEO crawler consumption.
    'anetbbs/web/main.py': {338},
    # 184-185: _latest_payload()'s public JSON API body (peer installs
    # checking for updates) -- correctly a machine ISO-8601 format;
    # the *display* side (admin/upgrades.html's `upstream.published_at`)
    # already goes through the `eastern` filter, which also accepts an
    # ISO string. 3109: backup download filename, not a display value.
    'anetbbs/web/upgrades.py': {185},
    'anetbbs/web/admin.py': {3109},
    # RSS pubDate/lastBuildDate -- RFC-822 explicitly declares a UTC
    # +0000 offset; not a plain display site.
    'anetbbs/web/feeds.py': {27, 38},
    # QWK packet wire format (MESSAGES.DAT header spec) -- offline
    # reader protocol, not a display site.
    'anetbbs/web/qwk_user.py': {67, 107, 108},
    'anetbbs/web/qwk_hub.py': {124, 188},
    # FTN kludge line / CTCP TIME reply -- protocol internals.
    'anetbbs/web/netmail.py': {231},
    'anetbbs/web/irc_web.py': {330},
    # data-started="...Z" -- feeds a client-side JS "live elapsed"
    # widget (new Date() parsing), not read directly by a human.
    'anetbbs/templates/echomail/admin/logs.html': {48},
    # filename-safe timestamp suffixes, not display values.
    'anetbbs/web/ansi_editor.py': {243, 308, 393, 417},
    'anetbbs/web/hub_admin.py': {1296},
    'anetbbs/web/control.py': {611},
    # Finger protocol response to an arbitrary remote client -- not a
    # sysop/user-facing screen in this app.
    'anetbbs/core/finger_server.py': {104, 118},
    # MRC chat has its OWN pre-existing, deliberate per-user /set tz
    # feature (see mrc_chat.py's own docstring) -- not naive UTC
    # display, intentionally left alone rather than folded into the
    # global Eastern conversion.
    'anetbbs/features/mrc_chat.py': set(),  # no .strftime( there anyway
    # These two files use the OTHER correct pattern: convert once via
    # to_eastern() at the source of the value, then call plain
    # .strftime() on the now-already-Eastern-aware datetime, instead of
    # calling fmt_eastern() at each individual display site. Equally
    # correct, just a different shape than the rest of the codebase --
    # see each file's own comment at the to_eastern() call site.
    'anetbbs/features/display_codes.py': {65, 66, 67, 86, 87},
    'anetbbs/features/anetirc2.py': {621},
}


def _iter_target_files():
    for p in (ROOT / 'anetbbs' / 'templates').rglob('*.html'):
        yield p
    for name in ('bbs_ui.py', 'wall.py', 'multinode.py', 'menu_engine.py',
                 'lastcallers.py', 'petscii_ui.py', 'display_codes.py',
                 'anetirc2.py'):
        p = ROOT / 'anetbbs' / 'features' / name
        if p.exists():
            yield p
    p = ROOT / 'anetbbs' / 'core' / 'session.py'
    if p.exists():
        yield p


class NoRawUtcTimestampDisplayTests(unittest.TestCase):
    def test_no_unconverted_strftime_in_templates_or_terminal_ui(self):
        offenders = []
        for path in _iter_target_files():
            rel = str(path.relative_to(ROOT))
            allowed = ALLOWLIST.get(rel, set())
            text = path.read_text(encoding='utf-8')
            for i, line in enumerate(text.split('\n'), 1):
                if '.strftime(' in line and i not in allowed:
                    offenders.append(f'{rel}:{i}: {line.strip()}')
        self.assertEqual(offenders, [],
            "Found .strftime( call(s) on a datetime that bypass the "
            "shared Eastern-time display conversion (anetbbs/core/tz.py "
            "-- use the `eastern` Jinja filter in templates, or "
            "fmt_eastern()/to_eastern() in Python). If this is a "
            "genuine machine-format exception (API JSON, filename, "
            "protocol wire format), add it to ALLOWLIST in this test "
            "with a comment explaining why -- don't just delete the "
            "assertion.\n" + '\n'.join(offenders))


if __name__ == '__main__':
    unittest.main()
