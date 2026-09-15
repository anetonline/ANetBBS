"""Regression test for tools/scaffold_builtin_door.py
(anetbbs-scaffold-door), the door-development-SDK starter template
added in the gap-analysis follow-up round (post-v1.0.77) to lower the
barrier for writing a new builtin_python door -- the one door type
docs/17-development.md previously had zero tutorial content for.

Covers: a valid slug produces an importable, coroutine-function door
module and a matching draft (is_active=False) Game row; an invalid
slug is rejected before touching the filesystem or database; a
duplicate slug is refused rather than silently overwriting/duplicating.
"""
import importlib
import inspect
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod

from tools.scaffold_builtin_door import build_parser, main as scaffold_main  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parent.parent
FEATURES_DIR = REPO_ROOT / 'anetbbs' / 'features'


class ScaffoldBuiltinDoorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp_db = str(Path(__file__).resolve().parent / '.scaffold_door_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)

    def setUp(self):
        self._generated_files = []
        self.ctx = self.app.app_context()
        self.ctx.push()
        from anetbbs.models import db, Game
        db.session.query(Game).filter(Game.slug.like('scafftest%')).delete(
            synchronize_session=False)
        db.session.commit()

    def tearDown(self):
        for p in self._generated_files:
            if p.exists():
                p.unlink()
        self.ctx.pop()

    def _run(self, *cli_args):
        parser = build_parser()
        args = parser.parse_args(cli_args)
        module_path = FEATURES_DIR / f'{args.slug}.py'
        self._generated_files.append(module_path)

        old_argv = sys.argv
        sys.argv = ['scaffold_builtin_door.py', *cli_args]
        try:
            return scaffold_main()
        finally:
            sys.argv = old_argv

    def test_valid_slug_creates_importable_door_and_draft_game_row(self):
        rc = self._run('scafftest1', 'Scaffold Test One')
        self.assertEqual(rc, 0)

        module_file = FEATURES_DIR / 'scafftest1.py'
        self.assertTrue(module_file.exists())

        mod = importlib.import_module('anetbbs.features.scafftest1')
        self.assertTrue(hasattr(mod, 'launch_scafftest1'))
        self.assertTrue(inspect.iscoroutinefunction(mod.launch_scafftest1))
        sig = inspect.signature(mod.launch_scafftest1)
        self.assertEqual(list(sig.parameters), ['session', 'username'])

        from anetbbs.models import Game
        game = Game.query.filter_by(slug='scafftest1').first()
        self.assertIsNotNone(game)
        self.assertEqual(game.game_type, 'builtin_python')
        self.assertEqual(game.web_game_module,
                         'anetbbs.features.scafftest1:launch_scafftest1')
        self.assertFalse(game.is_active,
                         'a freshly scaffolded door must stay hidden '
                         '(is_active=False) until a sysop reviews it')

    def test_invalid_slug_is_rejected_before_touching_disk_or_db(self):
        rc = self._run('Not A Valid Slug!', 'Bad Slug Door')
        self.assertNotEqual(rc, 0)
        self.assertFalse((FEATURES_DIR / 'Not A Valid Slug!.py').exists())
        from anetbbs.models import Game
        self.assertIsNone(Game.query.filter_by(name='Bad Slug Door').first())

    def test_duplicate_slug_refused_not_overwritten(self):
        rc1 = self._run('scafftest2', 'First Version')
        self.assertEqual(rc1, 0)
        module_file = FEATURES_DIR / 'scafftest2.py'
        original_content = module_file.read_text()

        rc2 = self._run('scafftest2', 'Second Version')
        self.assertNotEqual(rc2, 0)
        self.assertEqual(module_file.read_text(), original_content,
                         'a duplicate-slug run must not overwrite the '
                         'existing door file')

        from anetbbs.models import Game
        count = Game.query.filter_by(slug='scafftest2').count()
        self.assertEqual(count, 1,
                         'a duplicate-slug run must not create a second Game row')


if __name__ == '__main__':
    unittest.main()
