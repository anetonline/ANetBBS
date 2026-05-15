# deploy/wsgi_wrapper.py
"""
Production WSGI entry point for ANetBBS.

Use with gunicorn + eventlet worker:
    gunicorn --worker-class eventlet -w 1 -b 0.0.0.0:5000 deploy.wsgi_wrapper:app

Or via the systemd service file provided in this directory.
"""
from anetbbs.web_app import create_app, socketio  # noqa: F401

app = create_app('production')
