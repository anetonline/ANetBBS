# anetbbs/web/finger.py
"""
Finger client UI. Lets users query remote BBSes' finger services
(RFC 1288 — TCP 79).
"""
import socket
from flask import Blueprint, render_template, request
from flask_login import login_required


finger_bp = Blueprint('finger', __name__, url_prefix='/finger')


def _do_finger(host, user='', port=79, timeout=10):
    """Synchronous finger query. Returns the raw response text."""
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except (OSError, socket.gaierror) as exc:
        return f'[connection failed: {exc}]'
    try:
        query = (user + '\r\n').encode('utf-8', errors='replace')
        sock.sendall(query)
        chunks = []
        while True:
            try:
                data = sock.recv(4096)
            except OSError:
                break
            if not data:
                break
            chunks.append(data)
            if len(b''.join(chunks)) > 64 * 1024:
                break
        return b''.join(chunks).decode('utf-8', errors='replace')
    finally:
        try:
            sock.close()
        except OSError:
            pass


@finger_bp.route('/', methods=['GET', 'POST'])
@login_required
def index():
    response = ''
    host = ''
    user = ''
    if request.method == 'POST':
        host = (request.form.get('host') or '').strip()
        user = (request.form.get('user') or '').strip()
        port = request.form.get('port', type=int) or 79
        if host:
            response = _do_finger(host, user=user, port=port)
    return render_template('finger/index.html',
                           response=response, host=host, user=user)
