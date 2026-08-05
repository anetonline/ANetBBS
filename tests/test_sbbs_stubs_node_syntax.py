"""Regression test for a real crash found live testing Chicken Delivery
on the Pi3 (the first real interactive door to actually exercise
frame.js/sprite.js/tree.js/funclib.js through the Node.js compat
fallback path -- see anetbbs/games/sbbs_stubs/json-client.js's own
docstring for the broader Synchronet JSON-RPC integration this door
was built to prove out):

    SyntaxError: Unexpected identifier 'each'
        at .../frame.js:433
        for each(var c in this.__relations__.child)

`for each (var x in y)` is a SpiderMonkey/E4X-only language extension,
removed from JS years ago and never supported by Node's V8 -- and
since a JS syntax error fails at PARSE time for the entire file, this
crash happened before any of the door's own code ever ran, regardless
of whether that specific line would even execute for a given door's
usage pattern. Found and fixed across 8 vendored library files
(frame.js, tree.js, cvslib.js, json-chat.js, layout.js, json-db.js) --
each occurrence converted to explicit `Object.keys()` iteration, the
same fix pattern already established (and confirmed working) in
cnflib.js's getBytes() before this project ever noticed the wider
problem. funclib.js had a *different*, deeper issue on the same
crash path (real E4X XML literal syntax in toXML(), not just `for
each`) -- fixed by making that one function fail loudly if ever
actually called instead of attempting to polyfill E4X/XMLList for a
function nothing in this project's bundled door set actually uses.

Deliberately NOT fixed (real, but not on any currently-bundled door's
load() path -- see the module-level skip list below): callsign.js and
funclib.js's toXML() both need genuine E4X XML support Node doesn't
have at all (not just a syntax swap); dns.js and inihelper.js use
`class`/`enum` as parameter names, reserved words in modern JS but not
in old SpiderMonkey.
"""
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[1]
STUBS_DIR = REPO_ROOT / 'anetbbs' / 'games' / 'sbbs_stubs'
_NODE_PATH = '/usr/bin/node'

# Files a real, currently-bundled door's load() chain can actually
# reach (chickendelivery.js -> sbbsdefs.js, frame.js, tree.js,
# sprite.js, json-client.js, event-timer.js; sprite.js/tree.js also
# pull in funclib.js) -- these MUST parse cleanly under Node, since a
# syntax error in any of them is a guaranteed crash for any door that
# touches Frame/Sprite/Tree, not just Chicken Delivery. Real, known,
# NOT-yet-fixed E4X/reserved-word issues in files nothing currently
# loads (callsign.js, dns.js, inihelper.js, rss-atom.js) are
# deliberately excluded here -- fixing those is real future work, but
# guarding files nothing calls doesn't protect anything today.
REACHABLE_FILES = [
    'sbbsdefs.js', 'frame.js', 'tree.js', 'sprite.js',
    'json-client.js', 'event-timer.js', 'funclib.js',
    # Also part of the wider fix even though not on Chicken Delivery's
    # own path -- other bundled/future doors reach these.
    'cvslib.js', 'json-chat.js', 'layout.js', 'json-db.js',
    # Bubble Boggle's own load() chain (game.js -> graphic.js,
    # sbbsdefs.js, funclib.js, calendar.js; graphic.js internally
    # load()s cga_defs.js/ansiterm_lib.js via the 2-arg scope form).
    'graphic.js', 'calendar.js', 'cga_defs.js', 'ansiterm_lib.js',
    # FatFish's own load() chain -- random lake terrain generation.
    'mapgenerator.js',
    # Dice Warz ][ 's own load() chain -- text input widget (json-chat.js
    # already covered above, but never actually exercised by a bundled
    # door until now).
    'inputline.js',
]


@unittest.skipUnless(Path(_NODE_PATH).is_file(), 'requires a real Node.js binary')
class SbbsStubsNodeSyntaxTests(unittest.TestCase):
    def test_reachable_vendored_libraries_parse_cleanly_under_node(self):
        failures = []
        for name in REACHABLE_FILES:
            path = STUBS_DIR / name
            self.assertTrue(path.is_file(), f'{name} not found at {path}')
            result = subprocess.run(
                [_NODE_PATH, '--check', str(path)],
                capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                failures.append(f'{name}: {result.stderr.strip()}')
        self.assertEqual(failures, [], 'Node syntax errors found:\n' + '\n'.join(failures))

    def test_no_for_each_syntax_remains_in_reachable_files(self):
        """Direct guard against the exact bug class regressing (e.g. a
        future vendored-file update reintroducing `for each`) --
        checks the real E4X `for each (` construct specifically, not
        the English phrase "for each" that legitimately appears in
        comments."""
        import re
        pattern = re.compile(r'for\s+each\s*\(')
        offenders = []
        for name in REACHABLE_FILES:
            content = (STUBS_DIR / name).read_text()
            for lineno, line in enumerate(content.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith('//') or stripped.startswith('*'):
                    continue
                if pattern.search(line):
                    offenders.append(f'{name}:{lineno}: {line.strip()}')
        self.assertEqual(offenders, [], 'for-each (E4X) syntax found:\n' + '\n'.join(offenders))


if __name__ == '__main__':
    unittest.main()
