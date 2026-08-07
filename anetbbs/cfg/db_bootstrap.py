"""Lightweight app-context bootstrap for the anetbbs-cfg terminal tool.

Deliberately does NOT import anetbbs.web_app. That module imports
eventlet (+ monkey-patches stdlib socket/threading/ssl), flask-socketio,
and flask-migrate, then create_app() registers ~76 web blueprints and
compiles their werkzeug URL rules, runs the full column/index migration
sweep, seeds default data, and starts every background service --
extensive setup for the real web/BBS server, none of which a config
screen needs. Profiled cost of that full path: ~1.3s just to import
web_app.py, another ~0.5s on blueprint registration alone. On a
Raspberry Pi 3 (much slower per-op) that's a large share of anetbbs-cfg
taking ~10s to reach its first screen.

This bootstrap only does what the tool actually needs: a Flask app with
`db` (SQLAlchemy) bound to it and an app context to run plain ORM
queries in.

Assumes the schema is already current -- the real anetbbs-web process
keeps it in sync via its own migration sweep on boot, and in practice
that process has always started at least once by the time a sysop
reaches for a config tool. Still calls db.create_all() as a cheap
safety net (a no-op on tables that already exist) for the one edge case
that matters: a brand-new install where anetbbs-web has never run yet.
"""
import os

from flask import Flask

from anetbbs.config import get_config
from anetbbs.models import db


def create_minimal_app(config_name=None):
    app = Flask(__name__)
    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'development')
    app.config.from_object(get_config(config_name))
    db.init_app(app)
    with app.app_context():
        db.create_all()
    return app
