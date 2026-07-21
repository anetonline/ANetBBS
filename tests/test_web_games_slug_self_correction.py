"""Regression test for a real bug caught live: the WEB_GAMES seeding loop
in web_app.py only inserted a Game row if its slug didn't already exist,
with no logic to fix an EXISTING row whose game_type had since become
wrong for that slug.

Concretely: ANetDarkForces's terminal door originally shipped under slug
'darkforces' (game_type='builtin_python'). A later release added a real
web/canvas entry that also wanted the 'darkforces' slug and moved the
terminal door to 'darkforces-term'. On a fresh install this is fine, but
on an in-place upgrade of an already-deployed BBS, the OLD 'darkforces'
row (game_type='builtin_python') already existed -- the "insert only if
missing" seeding loop silently left it untouched, so it kept routing to
the terminal port forever, while the freshly-inserted 'darkforces-term'
row was ALSO the terminal port. Net result: two Game Center entries that
were both actually the terminal edition, and the canvas game was never
reachable at all.

The fix (matching the BUNDLED_DOORS loop's own established pattern)
self-corrects an existing row's game_type/web_game_module on every boot,
without touching sysop-customizable fields (name/description/category/
icon/sort_order) that a BUNDLED_DOORS-style blanket overwrite would have
clobbered.
"""
import os

import pytest


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / 'web_games_seed_test.db')


def _boot(db_path):
    import anetbbs.config as cfg_mod
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    return app


def test_fresh_install_gets_correct_types(db_path):
    app = _boot(db_path)
    with app.app_context():
        from anetbbs.models import Game
        darkforces = Game.query.filter_by(slug='darkforces').first()
        assert darkforces is not None
        assert darkforces.game_type == 'builtin_web'
        assert darkforces.web_game_module == 'darkforces'
        # darkforces-term (the terminal edition) is currently retired from
        # BUNDLED_DOORS -- see test_darkforces_term_retired.py -- so a
        # fresh install never seeds it at all.
        assert Game.query.filter_by(slug='darkforces-term').first() is None


def test_stale_row_from_an_older_release_self_corrects_on_next_boot(db_path):
    # Simulate exactly what a real in-place upgrade looked like: an
    # existing 'darkforces' row still carrying the OLD (pre-migration)
    # game_type from before the web entry existed.
    app = _boot(db_path)
    with app.app_context():
        from anetbbs.models import db, Game
        stale = Game.query.filter_by(slug='darkforces').first()
        stale.game_type = 'builtin_python'
        stale.web_game_module = 'anetbbs.features.darkforces_term:launch'
        db.session.commit()

    # Re-boot against the SAME db file, matching a sysop restarting the
    # service after deploying the fix.
    app2 = _boot(db_path)
    with app2.app_context():
        from anetbbs.models import Game
        darkforces = Game.query.filter_by(slug='darkforces').first()
        assert darkforces.game_type == 'builtin_web', \
            'stale row must self-correct back to the web game type on the next boot'
        assert darkforces.web_game_module == 'darkforces'


def test_self_correction_does_not_clobber_sysop_customized_fields(db_path):
    # A sysop renaming/re-describing/re-categorizing a built-in web game
    # via the admin UI must survive a restart -- only game_type/
    # web_game_module are routing-critical and safe to force back to the
    # registry's own values.
    app = _boot(db_path)
    with app.app_context():
        from anetbbs.models import db, Game
        row = Game.query.filter_by(slug='darkforces').first()
        row.name = 'Sysop Renamed This'
        row.description = 'A custom description the sysop wrote.'
        row.sort_order = 999
        db.session.commit()

    app2 = _boot(db_path)
    with app2.app_context():
        from anetbbs.models import Game
        row = Game.query.filter_by(slug='darkforces').first()
        assert row.name == 'Sysop Renamed This'
        assert row.description == 'A custom description the sysop wrote.'
        assert row.sort_order == 999
        assert row.game_type == 'builtin_web'  # still self-corrected
