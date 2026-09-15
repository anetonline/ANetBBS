"""
ANetBBS<->ANetBBS MSP loopback test.

Spins up two BBS app instances in-process (different DBs, different MSP/SYSTAT
ports), sends an MSP message from instance A to instance B, then queries
B's SYSTAT from A. Reports pass/fail per step.

Manual diagnostic script, not a pytest/unittest test -- run directly:
    python3 tests/msp_loopback_check.py

(Named without a test_*/​*_test.py-matching name and gated behind
`if __name__ == '__main__':` on purpose: it starts real MSP/SYSTAT network
listeners, deletes/recreates /tmp scratch directories, and mutates
os.environ['DATABASE_URL']/['SECRET_KEY'] process-wide with no restoration
-- none of that should ever happen just from pytest importing a module
during test collection.)
"""
import os
import sys
import shutil
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Two isolated data directories
DIR_A = '/tmp/anetbbs-loopback-a'
DIR_B = '/tmp/anetbbs-loopback-b'

PORT_A_MSP, PORT_A_SYS = 12018, 12011
PORT_B_MSP, PORT_B_SYS = 12118, 12111

# --- SSRF-guard test bypass, THIS PROCESS ONLY -----------------------------
# send_msp()/query_systat() route every outbound connection through
# anetbbs.core.net_safety.resolve_safe_destination(), which refuses
# loopback/private destinations by design (a real security-audit fix --
# both functions are reachable from free-text user input, with no admin
# gate, so allowing loopback there in production would hand any logged-in
# user an internal-network-recon oracle). This script deliberately talks
# to 127.0.0.1 to simulate two separate BBS processes on one machine --
# exactly what that guard exists to refuse for real traffic -- so it
# always fails send_msp()/query_systat() unpatched (confirmed live:
# "Connections to private/internal addresses are not allowed").
#
# Patched here, in THIS diagnostic script's own process only, to allow
# loopback strictly on the four ports this script itself uses.
# anetbbs/web/imsg.py and anetbbs/features/bbs_ui.py (the real send/query
# call sites reachable by an actual user) are never touched -- they still
# import the unpatched function from anetbbs.core.net_safety directly.
import anetbbs.core.net_safety as _net_safety
import anetbbs.msp.client as _msp_client

_LOOPBACK_TEST_PORTS = frozenset({PORT_A_MSP, PORT_A_SYS, PORT_B_MSP, PORT_B_SYS})
_real_resolve_safe_destination = _net_safety.resolve_safe_destination


def _resolve_safe_destination_allow_loopback_test_ports(host, port, own_ports=None):
    if own_ports is None and port in _LOOPBACK_TEST_PORTS:
        own_ports = _LOOPBACK_TEST_PORTS
    return _real_resolve_safe_destination(host, port, own_ports=own_ports)


# Patch both the defining module (systat.py does a fresh `from ..core.
# net_safety import resolve_safe_destination` inside query_systat() on
# every call, so patching the source attribute is enough there) and
# msp/client.py's already-bound top-level import (send_msp() imports it
# once at module-load time, so patching net_safety's own attribute alone
# would not reach client.py's already-resolved local name).
_net_safety.resolve_safe_destination = _resolve_safe_destination_allow_loopback_test_ports
_msp_client.resolve_safe_destination = _resolve_safe_destination_allow_loopback_test_ports


def make_app(data_dir, msp_port, systat_port, bbs_name):
    """Create an isolated BBS app instance pointed at *data_dir*."""
    from anetbbs.web_app import create_app
    # Override before create_app so the engine uses the right URI.
    os.environ['DATABASE_URL'] = f'sqlite:///{data_dir}/anetbbs.db'
    os.environ['SECRET_KEY'] = 'test-loopback-' + bbs_name
    app = create_app()
    app.config['DATA_DIR'] = data_dir
    app.config['UPLOADS_DIR'] = os.path.join(data_dir, 'uploads')
    app.config['MSP_PORT'] = msp_port
    app.config['SYSTAT_PORT'] = systat_port
    app.config['BBS_NAME'] = bbs_name
    return app


def start_listeners(app):
    """Start MSP + SYSTAT listeners on app's configured ports.
    Skip the auto-startup ones that already grabbed the default low ports."""
    from anetbbs.msp.server import start_msp_server, stop_msp_server
    from anetbbs.msp.systat import start_systat_server, stop_systat_server
    # Stop any previously-started ones in this process
    stop_msp_server()
    stop_systat_server()
    time.sleep(0.2)
    start_msp_server(app)
    start_systat_server(app)
    time.sleep(0.4)


def step(label, ok, detail=''):
    mark = '✓' if ok else '✗'
    print(f'  {mark}  {label}{": " + detail if detail else ""}')
    return ok


# _create_default_data() writes the fallback admin's one-time password to
# the REAL repo's data/ directory (see the comment at its read site in
# main()/step 4 below) -- not either DIR_A/DIR_B temp dir. Saved and
# restored around the run so this diagnostic never leaves a stray
# password file (or clobbers a real one) in the actual working repo.
real_admin_pw_file = Path(__file__).resolve().parents[1] / 'data' / 'admin_password.txt'


def main():
    print('=== ANetBBS MSP loopback test ===')

    shutil.rmtree(DIR_A, ignore_errors=True)
    shutil.rmtree(DIR_B, ignore_errors=True)
    os.makedirs(DIR_A, exist_ok=True)
    os.makedirs(DIR_B, exist_ok=True)

    _pw_backup = real_admin_pw_file.read_bytes() if real_admin_pw_file.exists() else None

    try:
        # Each instance lives in its own process-level state. Instead of
        # trying to host both at once in this single Python process (port
        # conflicts on the listener-singletons), we run instance B as the
        # listener and instance A as a client. That's the realistic
        # deploy: each BBS is a process.
        print('\n[Setup] Building instance B (listener) ...')
        app_b = make_app(DIR_B, PORT_B_MSP, PORT_B_SYS, 'TEST-BBS-B')
        start_listeners(app_b)
        print(f'  instance B: MSP {PORT_B_MSP}, SYSTAT {PORT_B_SYS}, dir {DIR_B}')

        # --- Step 1: send MSP from A to B ---
        print('\n[1] Sending MSP from A to B (admin@127.0.0.1)')
        from anetbbs.msp.client import send_msp
        ok = send_msp('127.0.0.1', recipient='admin', message='Hello from A',
                      sender='alice@TEST-BBS-A', port=PORT_B_MSP, timeout=5.0)
        step('send_msp returned True', ok)
        time.sleep(0.6)

        # --- Step 2: verify it persisted in B ---
        print('\n[2] Verify B persisted the message')
        with app_b.app_context():
            from anetbbs.models import InstantMessage
            msgs = InstantMessage.query.order_by(InstantMessage.id.desc()).limit(1).all()
            found = msgs and msgs[0].body == 'Hello from A'
            detail = ''
            if msgs:
                m = msgs[0]
                detail = f"id={m.id} from={m.sender_label!r} body={m.body!r}"
            step('InstantMessage row exists with correct body', bool(found), detail)

        # --- Step 3: SYSTAT query A -> B ---
        print('\n[3] SYSTAT query 127.0.0.1:%d (B)' % PORT_B_SYS)
        from anetbbs.msp.systat import query_systat
        reply = query_systat('127.0.0.1', port=PORT_B_SYS, timeout=3.0)
        step('reply contains BBS name', 'TEST-BBS-B' in reply,
             f'len={len(reply)}')
        step('reply mentions "active"', 'active' in reply.lower())

        # --- Step 4: imsg send route splits user@host ---
        print('\n[4] /imsg/send accepts "admin@127.0.0.1" pasted into recipient')
        app_a = make_app(DIR_A, PORT_A_MSP, PORT_A_SYS, 'TEST-BBS-A')
        app_a.config['WTF_CSRF_ENABLED'] = False
        client = app_a.test_client()
        # Login as admin. The fallback admin account gets a fresh, random
        # one-time password on every create_app() call (web_app.py's
        # _create_default_data() -- a hardcoded 'admin123' default was
        # deliberately removed there), so it must be read back rather than
        # assumed. It's also written to the REAL repo's data/ directory,
        # not DIR_A -- _create_default_data() runs inside create_app(),
        # before make_app()'s app.config['DATA_DIR'] override (above) ever
        # takes effect, so at write time app.config['DATA_DIR'] still
        # resolves to Config.DATA_DIR's fixed default. real_admin_pw_file's
        # save/restore around this function keeps that from leaving a
        # stray password file behind in the real repo.
        admin_password = real_admin_pw_file.read_text().strip() \
            if real_admin_pw_file.exists() else 'admin123'
        client.post('/auth/login', data={'username': 'admin', 'password': admin_password})
        # Post the MSP send form with combined user@host
        resp = client.post('/imsg/send', data={
            'recipient': 'admin@127.0.0.1',
            'host': '',
            'port': PORT_B_MSP,
            'message': 'Pasted user@host should split',
            'submit': 'Send',
        }, follow_redirects=False)
        # Successful submit should 302 to /imsg/
        ok_route = resp.status_code in (302, 303)
        step('/imsg/send returned redirect (success)', ok_route,
             f'status={resp.status_code}')
        time.sleep(0.6)
        with app_b.app_context():
            msgs = InstantMessage.query.order_by(InstantMessage.id.desc()).limit(1).all()
            body = msgs[0].body if msgs else ''
            step('B received the second message', body == 'Pasted user@host should split',
                 f'body={body!r}')

        print('\n=== Loopback test complete ===')
    finally:
        from anetbbs.msp.server import stop_msp_server
        from anetbbs.msp.systat import stop_systat_server
        stop_msp_server()
        stop_systat_server()
        if _pw_backup is not None:
            real_admin_pw_file.write_bytes(_pw_backup)
        else:
            real_admin_pw_file.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
