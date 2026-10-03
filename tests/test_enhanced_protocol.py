"""Unit tests for anetbbs/features/enhanced_protocol.py -- the JSON
message protocol for the ANetBBS Enhanced Client (TEST VERSION, see the
"ANetBBS Enhanced Client" plan). Covers encode/decode shapes and the
decode side's job of turning a client->server message into the exact
keystroke byte(s) menu_engine.py's existing hotkey dispatch (and
read_key()/read_line()) already expect, unchanged.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.features import enhanced_protocol


class EncodeTests(unittest.TestCase):
    def test_encode_clear(self):
        self.assertEqual(json.loads(enhanced_protocol.encode_clear()),
                         {'type': 'clear'})

    def test_encode_text_minimal(self):
        msg = json.loads(enhanced_protocol.encode_text('hello'))
        self.assertEqual(msg['type'], 'text')
        self.assertEqual(msg['s'], 'hello')
        self.assertNotIn('x', msg)
        self.assertNotIn('fg', msg)

    def test_encode_text_with_position_and_color(self):
        msg = json.loads(enhanced_protocol.encode_text('hi', x=1, y=2, fg='cyan', bg='blk'))
        self.assertEqual(msg['x'], 1)
        self.assertEqual(msg['y'], 2)
        self.assertEqual(msg['fg'], 'cyan')
        self.assertEqual(msg['bg'], 'blk')

    def test_encode_menu_shapes_items_from_item_list_tuples(self):
        # Exactly the (hotkey, label, action_type, action_args) shape
        # menu_engine.py's own item_list already carries. 'send'
        # defaults to the hotkey itself (a click synthesizes one plain
        # keystroke) when the 4-tuple shape is used -- see
        # test_encode_menu_supports_an_explicit_send_override below for
        # the read_line()-based 5-tuple shape (games.py's door menu).
        item_list = [('A', 'Boards', 'goto_board', ''),
                     ('D', 'ANetDRAW', 'sysop_anetdraw', None)]
        msg = json.loads(enhanced_protocol.encode_menu('Main Menu', item_list))
        self.assertEqual(msg['type'], 'menu')
        self.assertEqual(msg['title'], 'Main Menu')
        self.assertEqual(msg['items'],
                         [{'hotkey': 'A', 'label': 'Boards', 'send': 'A'},
                          {'hotkey': 'D', 'label': 'ANetDRAW', 'send': 'D'}])

    def test_encode_menu_supports_an_explicit_send_override(self):
        item_list = [('16', 'RPG', '', '', '16\r'),
                     ('Q', 'Return', '', '', 'Q\r')]
        msg = json.loads(enhanced_protocol.encode_menu('Door Games', item_list))
        self.assertEqual(msg['items'],
                         [{'hotkey': '16', 'label': 'RPG', 'send': '16\r'},
                          {'hotkey': 'Q', 'label': 'Return', 'send': 'Q\r'}])

    def test_encode_cursor(self):
        msg = json.loads(enhanced_protocol.encode_cursor(5, 10))
        self.assertEqual(msg, {'type': 'cursor', 'x': 5, 'y': 10})


class DecodeClientMessageTests(unittest.TestCase):
    def test_key_message_becomes_utf8_bytes(self):
        raw = json.dumps({'type': 'key', 'ch': 'A'})
        self.assertEqual(enhanced_protocol.decode_client_message(raw), b'A')

    def test_click_message_synthesizes_hotkey_byte(self):
        raw = json.dumps({'type': 'click', 'hotkey': 'D'})
        self.assertEqual(enhanced_protocol.decode_client_message(raw), b'D')

    def test_enter_and_backspace_pass_through_as_their_control_bytes(self):
        self.assertEqual(
            enhanced_protocol.decode_client_message(json.dumps({'type': 'key', 'ch': '\r'})),
            b'\r')
        self.assertEqual(
            enhanced_protocol.decode_client_message(json.dumps({'type': 'key', 'ch': '\x7f'})),
            b'\x7f')

    def test_malformed_json_is_ignored_not_an_error(self):
        self.assertEqual(enhanced_protocol.decode_client_message('{not json'), b'')

    def test_non_dict_json_is_ignored(self):
        self.assertEqual(enhanced_protocol.decode_client_message('[1,2,3]'), b'')
        self.assertEqual(enhanced_protocol.decode_client_message('"just a string"'), b'')

    def test_unknown_type_is_ignored(self):
        raw = json.dumps({'type': 'resize', 'cols': 80, 'rows': 25})
        self.assertEqual(enhanced_protocol.decode_client_message(raw), b'')

    def test_key_message_with_empty_or_missing_ch_is_ignored(self):
        self.assertEqual(
            enhanced_protocol.decode_client_message(json.dumps({'type': 'key', 'ch': ''})), b'')
        self.assertEqual(
            enhanced_protocol.decode_client_message(json.dumps({'type': 'key'})), b'')

    def test_click_message_with_empty_or_missing_hotkey_is_ignored(self):
        self.assertEqual(
            enhanced_protocol.decode_client_message(json.dumps({'type': 'click', 'hotkey': ''})), b'')
        self.assertEqual(
            enhanced_protocol.decode_client_message(json.dumps({'type': 'click'})), b'')

    def test_non_string_ch_is_ignored(self):
        raw = json.dumps({'type': 'key', 'ch': 5})
        self.assertEqual(enhanced_protocol.decode_client_message(raw), b'')


if __name__ == '__main__':
    unittest.main()
