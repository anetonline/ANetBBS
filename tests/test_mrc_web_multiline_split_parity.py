"""Regression test for a real feature-parity gap (2026-09-22, Jerry's
own ask): the terminal MRC client (and uMRC itself) silently
auto-splits an over-limit line into multiple word-boundary "(1/3) ..."
chunks; the web client -- designed years ago, most recently touched
last year -- instead just refused to send anything over the limit at
all ("Message too long").

Ported mrc_chat.py's real _split_for_wire() to JS (anetbbs/templates/
mrc/index.html's splitForWire()), matching it exactly: same
word-boundary splitting, same 8-char "(NN/NN) " budget reservation,
same hard-cut for a single word longer than the whole budget, same
repeat_prefix-on-every-chunk convention.

This test proves the port didn't drift from the original by running
BOTH implementations -- the real Python one (imported directly, not
reimplemented) and the real JS one (extracted from the actual template
and run under Node, not reimplemented either) -- against the same
inputs and asserting byte-identical output. Requires `node` on PATH;
skips (not fails) if it's unavailable, matching this repo's existing
"skip when an external optional tool is missing" convention.
"""
import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.core  # noqa: F401  (resolves a circular import if mrc_chat is imported first)
from anetbbs.features.mrc_chat import _split_for_wire

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = REPO_ROOT / "anetbbs" / "templates" / "mrc" / "index.html"


def _extract_split_for_wire_js() -> str:
    html = TEMPLATE_PATH.read_text()
    m = re.search(
        r"function splitForWire\(text, cap, repeatPrefix\) \{.*?\n    \}\n",
        html, re.DOTALL)
    assert m is not None, "couldn't find splitForWire() in the MRC web template"
    return m.group(0)


@unittest.skipUnless(shutil.which("node"), "node not available")
class SplitForWireJsPythonParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js_fn = _extract_split_for_wire_js()

    def _split_js(self, text, cap, repeat_prefix=""):
        script = (
            self.js_fn +
            "\nconst args = JSON.parse(process.argv[1]);\n"
            "console.log(JSON.stringify(splitForWire(args.text, args.cap, args.repeatPrefix)));\n"
        )
        payload = json.dumps({"text": text, "cap": cap, "repeatPrefix": repeat_prefix})
        out = subprocess.run(["node", "-e", script, payload],
                              capture_output=True, text=True, check=True)
        return json.loads(out.stdout)

    def _assert_parity(self, text, cap, repeat_prefix=""):
        py_result = _split_for_wire(text, cap, repeat_prefix)
        js_result = self._split_js(text, cap, repeat_prefix)
        self.assertEqual(
            py_result, js_result,
            f"JS/Python split_for_wire diverged for text={text!r} cap={cap} "
            f"prefix={repeat_prefix!r}:\n  python: {py_result!r}\n  js:     {js_result!r}")

    def test_short_message_single_chunk_no_tag(self):
        self._assert_parity("hello world", 140)

    def test_short_message_with_color_prefix(self):
        self._assert_parity("hello world", 140, "|10")

    def test_long_message_splits_on_word_boundaries(self):
        self._assert_parity(
            "the quick brown fox jumps over the lazy dog " * 10, 140)

    def test_long_message_with_repeat_prefix_on_every_chunk(self):
        self._assert_parity(
            "the quick brown fox jumps over the lazy dog " * 10, 140, "|12")

    def test_single_word_longer_than_budget_is_hard_cut(self):
        self._assert_parity("a" * 300, 140)

    def test_exact_boundary_length_is_one_chunk(self):
        text = "x" * 140
        self._assert_parity(text, 140)

    def test_one_char_over_boundary_splits(self):
        text = "x" * 141
        self._assert_parity(text, 140)

    def test_empty_text_returns_no_chunks(self):
        self._assert_parity("", 140)

    def test_small_cap_with_large_prefix(self):
        self._assert_parity("a bunch of words to split apart here", 20, "|15")

    def test_trailing_newline_is_stripped(self):
        self._assert_parity("hello world\n", 140)


if __name__ == '__main__':
    unittest.main()
