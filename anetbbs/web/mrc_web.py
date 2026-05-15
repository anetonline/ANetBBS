"""
MRC Web Blueprint
Provides web interface for MRC chat via bridge service.

Auth model: the page itself uses @login_required. The WS endpoint upstream
(/mrcws → 127.0.0.1:8080) is gated by nginx auth_request → /mrc/auth-check
which returns 200 only for logged-in users. So an unauthenticated user
can't even open the WebSocket, even if they know the URL.
"""
from flask import Blueprint, render_template, current_app, request, abort
from flask_login import login_required, current_user

mrc_bp = Blueprint('mrc', __name__, url_prefix='/mrc')


@mrc_bp.route('/auth-check')
def auth_check():
    """Internal endpoint used by nginx auth_request to gate /mrcws.

    Returns 200 if the current request has a logged-in session, 401 otherwise.
    Never reached by users directly — nginx is the only caller."""
    if not getattr(current_user, 'is_authenticated', False):
        abort(401)
    return ('', 204)


@mrc_bp.route('/')
@login_required
def index():
    """MRC chat page - requires authentication"""
    use_ssl = current_app.config.get('MRC_BRIDGE_USE_SSL', False)
    ws_path = current_app.config.get('MRC_BRIDGE_WS_PATH', '/mrcws')

    # When deployed behind nginx the WS path is proxied relative to the
    # current host (e.g. wss://example.com/mrcws → bridge /ws).
    # Fall back to the legacy host:port form for direct connections.
    legacy_host = current_app.config.get('MRC_BRIDGE_HOST', '')
    legacy_port = current_app.config.get('MRC_BRIDGE_PORT', 8080)
    protocol = 'wss' if (use_ssl or request.is_secure) else 'ws'

    if legacy_host and legacy_host not in ('localhost', '127.0.0.1', '0.0.0.0'):
        # Explicit host configured — use it (direct bridge connection)
        bridge_ws_url = f"{protocol}://{legacy_host}:{legacy_port}{ws_path}"
    else:
        # Behind nginx proxy — construct relative to current host
        bridge_ws_url = f"{protocol}://{request.host}{ws_path}"

    # Get suggested handle from current user
    suggested_handle = current_user.username if current_user.is_authenticated else ''

    return render_template(
        'mrc/index.html',
        bridge_ws_url=bridge_ws_url,
        suggested_handle=suggested_handle
    )
