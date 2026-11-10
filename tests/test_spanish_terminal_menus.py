"""Spanish stock menus retain their hotkeys and sysop customizations."""
import asyncio
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import anetbbs.config as cfg_mod


class _Writer:
    def __init__(self):
        self.written = bytearray()

    def get_extra_info(self, key):
        return ('127.0.0.1', 1234) if key == 'peername' else None

    def write(self, data):
        self.written.extend(data)

    async def drain(self):
        pass

    def is_closing(self):
        return False


class _Reader:
    def __init__(self, keys):
        self.keys = list(keys)

    async def read(self, size=1):
        return self.keys.pop(0) if self.keys else b''


class SpanishTerminalMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._orig_data_dir = cfg_mod.TestingConfig.DATA_DIR
        cls._orig_flask_env = os.environ.get('FLASK_ENV')
        cls._temp = tempfile.TemporaryDirectory(prefix='anetbbs-spanish-menus-')
        cls._db_path = Path(cls._temp.name) / 'menus.db'
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._db_path}'
        cfg_mod.TestingConfig.DATA_DIR = str(Path(cls._temp.name) / 'data')
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        with cls.app.app_context():
            user = User(username='spanishmenus', email='menus@example.com',
                        password_hash='x', access_level=10, is_admin=False,
                        language='es')
            db.session.add(user)
            db.session.commit()
            cls.user_id = user.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        cfg_mod.TestingConfig.DATA_DIR = cls._orig_data_dir
        if cls._orig_flask_env is None:
            os.environ.pop('FLASK_ENV', None)
        else:
            os.environ['FLASK_ENV'] = cls._orig_flask_env
        with cls.app.app_context():
            from anetbbs.models import db
            db.session.remove()
            db.engine.dispose()
        cls._temp.cleanup()

    def setUp(self):
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.addCleanup(self.ctx.pop)
        from anetbbs.features.i18n import seed_default_translations
        from anetbbs.models import db, MenuTranslation
        MenuTranslation.query.delete()
        db.session.commit()
        seed_default_translations()

    def test_stock_menus_translate_without_changing_hotkeys_or_actions(self):
        from anetbbs.features.i18n import DEFAULT_TRANSLATIONS
        from anetbbs.features.menu_engine import DEFAULT_MENUS, _apply_menu_translations
        from anetbbs.models import BbsMenu, BbsMenuItem
        spanish = DEFAULT_TRANSLATIONS['es']
        for definition in DEFAULT_MENUS:
            with self.subTest(menu=definition['name']):
                menu = BbsMenu.query.filter_by(name=definition['name']).one()
                rows = BbsMenuItem.query.filter_by(menu_id=menu.id).order_by(
                    BbsMenuItem.sort_order, BbsMenuItem.id).all()
                source = [(row.hotkey, row.label, row.action_type, row.action_args)
                          for row in rows]
                title, items = _apply_menu_translations(menu.name, menu.title, source, 'es')
                self.assertEqual(title, spanish[f'menu.{menu.name}.title'])
                self.assertEqual([(hk, action, args) for hk, _, action, args in items],
                                 [(hk, action, args) for hk, _, action, args in source])
                self.assertEqual([label for _, label, _, _ in items],
                                 [spanish[f'menu.{menu.name}.item.{hk}']
                                  for hk, _, _, _ in source])
                self.assertEqual(menu.title, definition['title'])
        self.assertEqual(spanish['menu.main.item.M'], 'Tablones de mensajes')
        self.assertEqual(spanish['menu.main.item.W'], 'Cambiar contraseña')
        self.assertEqual(spanish['menu.chat_systems.item.Q'], 'Volver al menú principal')

    def test_bundled_pack_preserves_custom_wording_and_repurposed_hotkeys(self):
        from anetbbs.features.menu_engine import _apply_menu_translations
        source = [('M', 'Our community boards', 'boards', None),
                  ('B', 'Bulletins', 'ansi', 'house-rules'),
                  ('P', 'PM Inbox', 'pm', 'custom-argument'),
                  ('Z', 'Local game', 'door', 'local-game')]
        title, items = _apply_menu_translations('main', 'Our BBS Lobby', source, 'es')
        self.assertEqual(title, 'Our BBS Lobby')
        self.assertEqual(items, source)
        # The same protection applies after a return hotkey is redirected.
        source = [('Q', 'Return to Main Menu', 'goto', 'our-lobby')]
        _, items = _apply_menu_translations('chat_systems', 'Chat Systems', source, 'es')
        self.assertEqual(items, source)

    def test_sysop_translation_edits_override_custom_source_wording(self):
        from anetbbs.features.menu_engine import _apply_menu_translations
        from anetbbs.models import db, MenuTranslation
        MenuTranslation.query.filter_by(lang='es', key='menu.main.title').one().text = 'Nuestra BBS'
        MenuTranslation.query.filter_by(lang='es', key='menu.main.item.M').one().text = 'Nuestra comunidad'
        db.session.add(MenuTranslation(lang='es', key='menu.community.title', text='Comunidad'))
        db.session.add(MenuTranslation(lang='es', key='menu.community.item.Z', text='Normas locales'))
        db.session.commit()
        title, items = _apply_menu_translations(
            'main', 'Our BBS Lobby', [('M', 'Community', 'goto', 'community')], 'es')
        self.assertEqual(title, 'Nuestra BBS')
        self.assertEqual(items, [('M', 'Nuestra comunidad', 'goto', 'community')])
        title, items = _apply_menu_translations(
            'community', 'Community', [('Z', 'Local rules', 'ansi', 'rules')], 'es')
        self.assertEqual(title, 'Comunidad')
        self.assertEqual(items, [('Z', 'Normas locales', 'ansi', 'rules')])

    def test_missing_rows_fall_back_and_explicit_other_language_rows_still_apply(self):
        from anetbbs.features.menu_engine import _apply_menu_translations
        from anetbbs.models import db, MenuTranslation
        MenuTranslation.query.filter_by(lang='es', key='menu.main.item.M').delete()
        db.session.add(MenuTranslation(lang='de', key='menu.main.item.M', text='Gemeinschaft'))
        db.session.commit()
        source = [('M', 'Message Boards', 'boards', ''), ('Z', 'Custom', 'door', 'z')]
        _, items = _apply_menu_translations('main', 'ANetBBS - Main Menu', source, 'es')
        self.assertEqual(items, source)
        source = [('M', 'Our community', 'goto', 'community')]
        _, items = _apply_menu_translations('main', 'Our lobby', source, 'de')
        self.assertEqual(items, [('M', 'Gemeinschaft', 'goto', 'community')])
        with patch('anetbbs.features.i18n.get_translations') as lookup:
            self.assertEqual(_apply_menu_translations('main', 'Our lobby', source, 'en'),
                             ('Our lobby', source))
            lookup.assert_not_called()

    def test_reseeding_preserves_menu_and_translation_edits(self):
        from anetbbs.features.i18n import seed_default_translations
        from anetbbs.features.menu_engine import seed_default_menus
        from anetbbs.models import BbsMenu, BbsMenuItem, db, MenuTranslation
        menu = BbsMenu.query.filter_by(name='main').one()
        item = BbsMenuItem.query.filter_by(menu_id=menu.id, hotkey='M').one()
        old_menu = menu.title
        old_label = item.label
        try:
            menu.title = 'Our BBS Lobby'
            item.label = 'Community boards'
            translated = MenuTranslation.query.filter_by(
                lang='es', key='menu.main.item.M').one()
            translated.text = 'Comunidad local'
            db.session.commit()
            counts = (BbsMenu.query.count(), BbsMenuItem.query.count(),
                      MenuTranslation.query.count())
            for _ in range(2):
                seed_default_menus()
                seed_default_translations()
            db.session.expire_all()
            self.assertEqual((BbsMenu.query.count(), BbsMenuItem.query.count(),
                              MenuTranslation.query.count()), counts)
            self.assertEqual(menu.title, 'Our BBS Lobby')
            self.assertEqual(item.label, 'Community boards')
            self.assertEqual(translated.text, 'Comunidad local')
        finally:
            menu.title = old_menu
            item.label = old_label
            db.session.commit()

    def test_bundled_text_fits_ansi_cells_and_encodes_in_cp437(self):
        from anetbbs.features.i18n import DEFAULT_TRANSLATIONS
        for key, text in DEFAULT_TRANSLATIONS['es'].items():
            if not key.startswith('menu.'):
                continue
            with self.subTest(key=key):
                self.assertEqual(text.encode('cp437').decode('cp437'), text)
                self.assertEqual(text.encode('utf-8').decode('utf-8'), text)
                if '.item.' in key:
                    # Standard ANSI mode uses a 30-character column,
                    # including the six characters in "  [X] ".
                    self.assertLessEqual(len(text), 24)

    def test_real_session_renders_accents_and_navigates_with_original_hotkeys(self):
        from anetbbs.core.session import BBSSession
        from anetbbs.features.menu_engine import run_menu
        for encoding in ('cp437', 'utf-8'):
            for mode in ('ansi', 'ascii'):
                with self.subTest(encoding=encoding, mode=mode):
                    writer = _Writer()
                    # Main H -> Chat; Chat Q -> Main; Main Q/Y -> logoff.
                    session = BBSSession(_Reader([b'H', b'Q', b'Q', b'Y']), writer,
                                         config={}, forced_term_mode=mode)
                    session.encoding = encoding
                    session.user = {'id': self.user_id, 'access_level': 10,
                                    'is_admin': False, 'language': 'es'}
                    with patch('anetbbs.core.mods_override.has_core_override', return_value=False):
                        asyncio.run(run_menu(session))
                    output = bytes(writer.written).decode(encoding)
                    plain = re.sub(r'\x1b\[[0-9;?]*[A-Za-z]', '', output)
                    title = ('ANetBBS - Menú principal' if encoding == 'cp437'
                             else 'ANETBBS - MENÚ PRINCIPAL')
                    self.assertEqual(plain.count(title), 2)
                    self.assertIn('SISTEMAS DE CHAT', plain)
                    self.assertIn('[W] Cambiar contraseña', plain)
                    self.assertIn('[U] Usuarios en línea', plain)
                    self.assertIn('[Q] Volver al menú principal', plain)
                    self.assertNotIn('MEN?', plain)
                    self.assertNotIn('\ufffd', plain)
                    self.assertNotIn('>', plain)


if __name__ == '__main__':
    unittest.main()
