"""
MRC Web Blueprint
Provides web interface for MRC chat via bridge service.

Auth model: the page itself uses @login_required. The WS endpoint upstream
(/mrcws → 127.0.0.1:8080) is gated by nginx auth_request → /mrc/auth-check
which returns 200 only for logged-in users. So an unauthenticated user
can't even open the WebSocket, even if they know the URL.
"""
import os

from flask import Blueprint, render_template, current_app, request, abort
from flask_login import login_required, current_user

mrc_bp = Blueprint('mrc', __name__, url_prefix='/mrc')

_RUNTIME = os.environ.get('ANETBBS_RUNTIME', 'systemd')


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
    # NOTE: the WS URL the browser actually connects to is built
    # client-side in mrc/index.html's buildWsUrlForChoice() (it lets
    # the user pick between multiple MRC server choices, each with its
    # own ws_path -- something a single server-rendered URL can't
    # represent). That function defaults to `location.host` (same
    # host+port the page itself was loaded from), which is correct
    # when nginx proxies /mrcws on that same port (native installs,
    # docker-compose with its documented nginx/Caddy overlay) but wrong
    # for the single-container "quick start" Docker image, which has no
    # nginx at all -- there, only the bridge's own port (published
    # directly, see docs/22-containers.md) actually answers /mrcws.
    # `mrc_ws_host_override`, when non-empty, tells the JS to use THIS
    # host:port instead of location.host for every server choice's
    # path; empty means "no override, keep using location.host" (the
    # already-correct default for nginx-fronted deployments). Found
    # live-testing the single-container image against a real Docker
    # daemon for the first time: web MRC 404'd while terminal MRC (a
    # separate, direct server-to-server bridge connection, not
    # browser-facing) worked fine.
    legacy_host = current_app.config.get('MRC_BRIDGE_HOST', '')
    legacy_port = current_app.config.get('MRC_BRIDGE_PORT', 8080)

    if legacy_host and legacy_host not in ('localhost', '127.0.0.1', '0.0.0.0'):
        # Explicit public host configured (the documented docker-compose
        # pattern, direct-to-bridge, no nginx involved for this part).
        mrc_ws_host_override = f"{legacy_host}:{legacy_port}"
    elif _RUNTIME == 'docker-single':
        browser_host = request.host.split(':', 1)[0]
        mrc_ws_host_override = f"{browser_host}:{legacy_port}"
    else:
        # Behind nginx proxy (native install or docker-compose with its
        # documented overlay) — /mrcws is proxied on the same
        # public-facing port as the page itself; no override needed.
        mrc_ws_host_override = ''

    # Get suggested handle from current user
    suggested_handle = current_user.username if current_user.is_authenticated else ''

    return render_template(
        'mrc/index.html',
        mrc_ws_host_override=mrc_ws_host_override,
        suggested_handle=suggested_handle
    )
