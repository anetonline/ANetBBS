"""Recheck BBS credentials for active Socket.IO connections.

HTTP cookies are revalidated by Flask-Login's user loader. Socket.IO keeps
the session from its handshake, and some streaming handlers use only their
connection ID, so they also need a check before events and while idle.
"""
from flask import current_app, session
from flask_socketio import SocketIO

from .models import User, db


def _identifier_is_current(identifier):
    try:
        user_id = int(str(identifier).partition(':')[0])
    except (TypeError, ValueError):
        return False
    user = db.session.get(User, user_id, populate_existing=True)
    return bool(
        user is not None
        and str(identifier) == user.get_id()
        and user.is_active and not user.is_locked
        and (user.is_verified or user.is_admin)
    )


class RevokingSocketIO(SocketIO):
    """Invalidate authenticated sockets when their login ID is revoked."""

    def __init__(self, *args, **kwargs):
        self._auth_connections = {}
        self._auth_sweep_task = None
        super().__init__(*args, **kwargs)

    def init_app(self, app, **kwargs):
        # App factories/tests may bind this extension to a new server.
        self._auth_connections.clear()
        self._auth_sweep_task = None
        return super().init_app(app, **kwargs)

    def _handle_event(self, handler, message, namespace, sid, *args):
        def guarded_handler(*handler_args):
            key = (namespace, sid)
            if message == 'disconnect':
                self._auth_connections.pop(key, None)
                return handler(*handler_args)

            identifier = self._auth_connections.get(key, session.get('_user_id'))
            if identifier is not None and not _identifier_is_current(identifier):
                self._auth_connections.pop(key, None)
                if message != 'connect':
                    self.server.disconnect(sid, namespace=namespace)
                return False

            result = handler(*handler_args)
            if message == 'connect' and result is not False:
                # A remember cookie may be loaded by the connect handler.
                identifier = session.get('_user_id')
                if identifier is not None:
                    self._auth_connections[key] = str(identifier)
                    if not current_app.testing and self._auth_sweep_task is None:
                        app = current_app._get_current_object()
                        self._auth_sweep_task = self.start_background_task(
                            self._auth_sweep_loop, app, self.server)
            return result

        return super()._handle_event(
            guarded_handler, message, namespace, sid, *args)

    def revoke_expired_auth_connections(self):
        """Disconnect stale logins, including idle subscriptions.

        Called with an app context; explicit entry point also lets tests
        exercise idle revocation without timers or external services.
        """
        for (namespace, sid), identifier in list(self._auth_connections.items()):
            if not self.server.manager.is_connected(sid, namespace):
                self._auth_connections.pop((namespace, sid), None)
                continue
            if not _identifier_is_current(identifier):
                self._auth_connections.pop((namespace, sid), None)
                self.server.disconnect(sid, namespace=namespace)

    def _auth_sweep_loop(self, app, server):
        # One task per server process, rather than a task per connection.
        while self.server is server:
            self.sleep(5)
            if self.server is not server:
                return
            try:
                if self._auth_connections:
                    with app.app_context():
                        self.revoke_expired_auth_connections()
            except Exception:
                # A temporary DB failure must not permanently stop rechecks.
                app.logger.exception('Socket.IO authentication recheck failed')
