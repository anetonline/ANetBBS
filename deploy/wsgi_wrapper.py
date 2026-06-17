# deploy/wsgi_wrapper.py
"""
Production WSGI entry point for ANetBBS.

Use with gunicorn + eventlet worker:
    gunicorn --worker-class eventlet -w 1 -b 0.0.0.0:5000 deploy.wsgi_wrapper:app

Or via the systemd service file provided in this directory.
"""
# monkey_patch() must be first — before any other imports — so all
# threading.RLock/Event objects are greened from the start.
import eventlet
eventlet.monkey_patch()

from anetbbs.web_app import create_app, socketio  # noqa: F401

app = create_app('production')
