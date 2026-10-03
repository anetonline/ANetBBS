"""End-to-end regression test for real ANSI-art rendering in the
ANetBBS Enhanced Client (Jerry's ask: "use your cp437 ansi skills and
make a bad ass intro (welcome.ans) for this enhanced client").

_show_ansi_screen() used to hard-skip entirely for 'enhanced' mode
(raw CP437 bytes would have corrupted the JSON-only protocol). Now it
renders real color art via anetbbs.features.ansi_html._run_vt() (the
same VT parser already built for the web ANSI editor's HTML preview --
reused rather than writing a second ANSI parser) into a structured
'screen' message (anetbbs.features.enhanced_protocol.encode_screen())
the client draws directly onto its Canvas, no ANSI parsing happening
client-side at all.

Drives the REAL _show_ansi_screen() against the REAL welcome-art file
this change shipped as a sample (data/mods/text/welcome_enhanced_sample.ans,
built via a CP437-correct art-generation pass -- see the chat history
for the real c_source_utf8_literal_trap-shaped bug that pass caught
and fixed: '\\xNN' escapes in the builder script meant literal
codepoints U+00NN, not the target CP437 glyphs, and a per-row rstrip()
before centering independently misaligned each of the 6 block-logo
rows -- both confirmed via an actual rendered PNG preview before
shipping, not just code review), matching
tests/test_ansi_mods_text_override.py's established fixture pattern
(_app() patched to a fake config pointing DATA_DIR at a temp dir)
rather than a hand-rolled miniature ANSI snippet, so this is also a
real regression test for the sample file itself.
"""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.core.session import BBSSession

_REAL_ART_PATH = (Path(__file__).resolve().parents[1]
                  / 'data' / 'mods' / 'text' / 'welcome_enhanced_sample.ans')


class _FakeWriter:
    def __init__(self):
        self.written = bytearray()

    def write(self, data):
        self.written += data

    async def drain(self):
        pass

    def get_extra_info(self, key, default=None):
        return default

    def close(self):
        pass


def _make_session(**kwargs):
    writer = _FakeWriter()
    session = BBSSession(object(), writer, config={}, **kwargs)
    return session, writer


@unittest.skipUnless(_REAL_ART_PATH.is_file(),
                     'requires the real welcome_enhanced_sample.ans sample file')
class EnhancedAnsiArtRenderingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        data_dir = Path(self._tmp.name)
        (data_dir / 'mods' / 'text').mkdir(parents=True)
        (data_dir / 'text').mkdir(parents=True)
        # Copy the REAL shipped sample art's raw bytes in -- not a
        # hand-typed miniature snippet -- so this test actually
        # exercises the file Jerry will look at.
        (data_dir / 'mods' / 'text' / 'welcome_enhanced_sample.ans').write_bytes(
            _REAL_ART_PATH.read_bytes())

        class _FakeApp:
            config = {'DATA_DIR': str(data_dir)}

        self._patcher = patch('anetbbs.features.bbs_ui._app', return_value=_FakeApp())
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_renders_a_single_valid_screen_message(self):
        session, writer = _make_session(forced_term_mode='enhanced')
        asyncio.run(session._show_ansi_screen('welcome_enhanced_sample'))

        out = bytes(writer.written).decode('utf-8')
        msg = json.loads(out)
        self.assertEqual(msg['type'], 'screen')
        self.assertGreater(len(msg['rows']), 5)

    def test_contains_the_real_tagline_text_as_a_readable_run(self):
        session, writer = _make_session(forced_term_mode='enhanced')
        asyncio.run(session._show_ansi_screen('welcome_enhanced_sample'))

        msg = json.loads(bytes(writer.written).decode('utf-8'))
        all_text = ' '.join(
            run.get('text', '') for row in msg['rows'] for run in row['runs'])
        self.assertIn('E N H A N C E D', all_text)
        self.assertIn('C L I E N T', all_text)

    def test_block_logo_rows_are_consistently_aligned(self):
        """Regression for the real per-row-rstrip centering bug: every
        row of the big block-letter logo must start at the SAME
        column -- if centering ever goes back to independently
        rstripping/centering each row, the logo's rows drift to
        different horizontal offsets and the letters visually
        scramble (confirmed live via the rendered PNG preview before
        this fix)."""
        session, writer = _make_session(forced_term_mode='enhanced')
        asyncio.run(session._show_ansi_screen('welcome_enhanced_sample'))

        msg = json.loads(bytes(writer.written).decode('utf-8'))
        # The logo rows are the ones using the solid block-fill glyph
        # INSIDE the double-line box border ('║', the box's own
        # left/right rule) -- not just "any row with a block-fill
        # glyph", which also now matches the top/bottom gradient bars
        # (solid-block-with-color-changes, not shade-density, since
        # the live fix for "looks like gaps between blocks" moved
        # those off light/medium shade characters -- see welcome
        # art's own build script). Those gradient bars are OUTSIDE the
        # box (no '║' prefix) and legitimately start one column
        # further right than the box's own content by design.
        logo_row_first_cols = [
            row['runs'][0]['col'] for row in msg['rows']
            if any('█' in run.get('text', '') for run in row['runs'])
            and any('║' in run.get('text', '') for run in row['runs'])
        ]
        self.assertGreaterEqual(len(logo_row_first_cols), 4,
                                'expected several rows of block-letter logo')
        self.assertEqual(len(set(logo_row_first_cols)), 1,
                         f'logo rows start at inconsistent columns: {logo_row_first_cols}')

    def test_uses_real_cp437_glyphs_not_raw_latin1_codepoints(self):
        """Regression for the real '\\xNN escape means U+00NN, not the
        target CP437 glyph' bug: the double-line box border must
        render as the real box-drawing character (U+2550 '=', U+2551
        '|', ...), not its Latin-1 mojibake look-alike (U+00CD 'Í',
        U+00C9 'É', ...) that a literal '\\xNN' escape in the art
        source would have produced."""
        session, writer = _make_session(forced_term_mode='enhanced')
        asyncio.run(session._show_ansi_screen('welcome_enhanced_sample'))

        msg = json.loads(bytes(writer.written).decode('utf-8'))
        all_text = ''.join(
            run.get('text', '') for row in msg['rows'] for run in row['runs'])
        self.assertIn('═', all_text)  # real double-line horizontal
        self.assertNotIn('Í', all_text)  # the Latin-1 mojibake look-alike


if __name__ == '__main__':
    unittest.main()
