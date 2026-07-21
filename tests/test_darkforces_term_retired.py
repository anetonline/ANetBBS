"""Regression tests for pulling ANetDarkForces (Terminal Edition) back out
of the live product (anetbbs/web_app.py's BUNDLED_DOORS list + the
deactivation block right after its seeding loop).

Context: reported live after further playtesting -- the terminal port's
visuals need more offline iteration before it's ready for players. Rather
than delete anetbbs/features/darkforces_term.py (its own test suite in
tests/test_darkforces_term.py still exercises the module directly and
keeps passing), the fix removes its BUNDLED_DOORS registry entry so fresh
installs never seed it, and deactivates (not deletes) any existing row an
earlier release (v1.0b2.168-173) already seeded, so GameScore/GameSession
history tied to it survives. The canvas/web edition (slug 'darkforces',
game_type='builtin_web') is untouched and stays the only reachable way to
play ANetDarkForces in the meantime.
"""
import os

import pytest


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / 'darkforces_term_retired_test.db')


def _boot(db_path):
    import anetbbs.config as cfg_mod
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    return app


def test_fresh_install_never_seeds_the_terminal_edition(db_path):
    app = _boot(db_path)
    with app.app_context():
        from anetbbs.models import Game
        assert Game.query.filter_by(slug='darkforces-term').first() is None
        # The web/canvas edition is unaffected by the terminal edition's removal.
        web = Game.query.filter_by(slug='darkforces').first()
        assert web is not None
        assert web.game_type == 'builtin_web'
        assert web.is_active is True


def test_existing_active_row_from_a_prior_release_gets_deactivated_on_next_boot(db_path):
    # Simulate an install that already seeded darkforces-term back when it
    # was still in BUNDLED_DOORS (v1.0b2.168-173) -- manually recreate
    # that row the way the old seeding loop would have.
    app = _boot(db_path)
    with app.app_context():
        from anetbbs.models import db, Game
        row = Game(
            name='ANetDarkForces (Terminal)', slug='darkforces-term',
            description='...', category='action', icon='bi-crosshair',
            game_type='builtin_python',
            web_game_module='anetbbs.features.darkforces_term:launch',
            sort_order=2, is_active=True, max_nodes=1,
        )
        db.session.add(row)
        db.session.commit()

    # Re-boot against the SAME db file, matching a sysop restarting the
    # service after deploying this release.
    app2 = _boot(db_path)
    with app2.app_context():
        from anetbbs.models import Game
        row = Game.query.filter_by(slug='darkforces-term').first()
        assert row is not None, 'row must be deactivated, not deleted -- preserves score history'
        assert row.is_active is False

    # And a third boot confirms this is stable, not flip-flopping.
    app3 = _boot(db_path)
    with app3.app_context():
        from anetbbs.models import Game
        row = Game.query.filter_by(slug='darkforces-term').first()
        assert row.is_active is False


def test_retired_game_no_longer_reachable_via_game_center_or_play_route(db_path):
    app = _boot(db_path)
    with app.app_context():
        from anetbbs.models import db, Game, User
        row = Game(
            name='ANetDarkForces (Terminal)', slug='darkforces-term',
            description='...', category='action', icon='bi-crosshair',
            game_type='builtin_python',
            web_game_module='anetbbs.features.darkforces_term:launch',
            sort_order=2, is_active=True, max_nodes=1,
        )
        db.session.add(row)
        u = User(username='dfterm_alice', email='dfterm_alice@example.com', is_admin=False)
        u.set_password('password123')
        db.session.add(u)
        db.session.commit()

    # Re-boot to trigger the deactivation block, then hit the routes.
    app2 = _boot(db_path)
    app2.config['WTF_CSRF_ENABLED'] = False
    client = app2.test_client()
    resp = client.post('/auth/login', data={'username': 'dfterm_alice', 'password': 'password123'},
                        follow_redirects=True)
    assert resp.status_code == 200

    lobby = client.get('/games/')
    assert lobby.status_code == 200
    assert 'ANetDarkForces (Terminal)' not in lobby.get_data(as_text=True)

    play = client.get('/games/darkforces-term/play')
    assert play.status_code == 404


@pytest.fixture(autouse=True)
def _reset_db_uri():
    import anetbbs.config as cfg_mod
    orig = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
    yield
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = orig
