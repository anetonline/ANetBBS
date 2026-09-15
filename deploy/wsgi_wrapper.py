# deploy/wsgi_wrapper.py
"""
DEPRECATED -- NOT USED BY ANY CURRENT DEPLOYMENT PATH. Kept only as a
historical reference; confirmed via a security/performance audit that
nothing in this repo (no systemd unit, no install.sh/update.sh-
generated config, no Docker entrypoint) references this file at all.

Written for gunicorn + the eventlet worker class, which this project
stopped using in v1.0a2.67 (`gunicorn --worker-class=eventlet` is
broken on Python 3.12 -- fork()+greenlet crash; see deploy/serve.py's
own comment). deploy/serve.py (eventlet's own native WSGI server) is
the real, current production entry point every systemd unit and
install.sh actually use -- start there, not here.
"""
# monkey_patch() must be first — before any other imports — so all
# threading.RLock/Event objects are greened from the start.
import eventlet
eventlet.monkey_patch()

from anetbbs.web_app import create_app, socketio  # noqa: F401

app = create_app('production')
