# anetbbs/installer/wizard.py
"""
Interactive ANetBBS install wizard.

Run as `anetbbs-install` (added to console_scripts). Walks the sysop
through every required setting, generates .env, creates venv if
missing, installs systemd units pointing at this checkout, initializes
the database, creates the admin user, and prints a "you're done"
summary.

Designed to be safe to re-run — won't clobber an existing .env
without explicit confirmation, and skips work that's already done.
"""
import os
import sys
import secrets
import getpass
import subprocess
from pathlib import Path


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

C_RESET = '\x1b[0m'
C_BOLD = '\x1b[1m'
C_CYAN = '\x1b[96m'
C_GREEN = '\x1b[92m'
C_YELLOW = '\x1b[93m'
C_RED = '\x1b[91m'
C_GREY = '\x1b[90m'


def banner(text):
    bar = '═' * (len(text) + 4)
    print(f'\n{C_CYAN}╔{bar}╗{C_RESET}')
    print(f'{C_CYAN}║  {C_BOLD}{text}{C_RESET}{C_CYAN}  ║{C_RESET}')
    print(f'{C_CYAN}╚{bar}╝{C_RESET}\n')


def info(s):    print(f'{C_GREY}{s}{C_RESET}')
def ok(s):      print(f'{C_GREEN}✓ {s}{C_RESET}')
def warn(s):    print(f'{C_YELLOW}! {s}{C_RESET}')
def err(s):     print(f'{C_RED}✗ {s}{C_RESET}')
def step(s):    print(f'\n{C_CYAN}{C_BOLD}» {s}{C_RESET}')


def ask(prompt, default=None, validator=None, secret=False):
    """Prompt with optional default. Re-asks until validator passes."""
    while True:
        suffix = f' [{default}]' if default is not None else ''
        full = f'{C_BOLD}{prompt}{C_RESET}{suffix}: '
        try:
            if secret:
                ans = getpass.getpass(full)
            else:
                ans = input(full)
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(1)
        ans = ans.strip()
        if not ans and default is not None:
            ans = str(default)
        if validator is not None:
            valid, msg = validator(ans)
            if not valid:
                err(msg)
                continue
        return ans


def ask_bool(prompt, default=True):
    d = 'Y/n' if default else 'y/N'
    while True:
        a = ask(f'{prompt} ({d})', '').strip().lower()
        if not a:
            return default
        if a in ('y', 'yes'):
            return True
        if a in ('n', 'no'):
            return False


def is_int(s):
    return (True, '') if s.isdigit() else (False, 'Must be a number.')


def is_port(s):
    if not s.isdigit():
        return False, 'Must be a number.'
    p = int(s)
    return (1 <= p <= 65535, '' if 1 <= p <= 65535 else 'Out of range 1-65535.')


def non_empty(s):
    return (bool(s.strip()), 'Required.')


# ---------------------------------------------------------------------------
# Wizard
# ---------------------------------------------------------------------------

def main():
    banner('ANetBBS — Install Wizard')
    info('This will walk you through a complete install. Defaults in [brackets]\n'
         'are sensible — press Enter to accept. Ctrl-C aborts at any time.')

    # 1. Install directory
    here = Path(__file__).resolve().parent.parent.parent  # anetbbs-rebuilt/
    step('Where is the BBS installed?')
    install_dir = ask('Install directory', default=str(here),
                      validator=lambda s: (Path(s).is_dir(),
                                           'Not a directory.'))
    install_dir = Path(install_dir).resolve()

    env_path = install_dir / '.env'
    if env_path.exists():
        warn(f'{env_path} already exists.')
        if not ask_bool('Overwrite?', default=False):
            info('Keeping existing .env. Will only create missing pieces.')
            existing_env = True
        else:
            existing_env = False
    else:
        existing_env = False

    # 2. BBS branding
    step('BBS branding')
    bbs_name = ask('BBS name', default='ANetBBS', validator=non_empty)
    bbs_desc = ask('BBS description (one line)',
                   default=f'Welcome to {bbs_name}!')
    sysop_name = ask('Sysop name', default='Sysop', validator=non_empty)
    sysop_email = ask('Sysop email', default='sysop@example.com')
    domain = ask('Public domain (or IP) — leave blank if none',
                 default='')

    # 3. Ports & services
    step('Network services')
    web_port = ask('Web port', default='5000', validator=is_port)
    telnet_enabled = ask_bool('Enable Telnet?', default=True)
    telnet_port = ask('Telnet port', default='2233',
                      validator=is_port) if telnet_enabled else '2233'
    ssh_enabled = ask_bool('Enable SSH?', default=True)
    ssh_port = ask('SSH port', default='2234',
                   validator=is_port) if ssh_enabled else '2234'
    rlogin_enabled = ask_bool('Enable rlogin?', default=False)
    rlogin_port = ask('rlogin port', default='513',
                      validator=is_port) if rlogin_enabled else '513'
    bbs_nodes = ask('Number of concurrent terminal nodes (1-100)',
                    default='8',
                    validator=lambda s: (s.isdigit() and 1 <= int(s) <= 100,
                                         'Must be 1-100.'))

    # 4. Echomail
    step('Echomail (FidoNet-style mail)')
    echo_enabled = ask_bool('Enable echomail networks?', default=True)

    # 5. Admin user
    step('Sysop account')
    admin_user = sysop_name
    admin_email = sysop_email
    info(f'Sysop login username: {admin_user}')
    while True:
        admin_pass = ask('Sysop password (min 8 chars)', secret=True,
                         validator=lambda s: (len(s) >= 8,
                                              'Need at least 8 chars.'))
        confirm = ask('Confirm password', secret=True)
        if admin_pass == confirm:
            break
        err("Passwords don't match — try again.")

    # 6. Database
    step('Database')
    data_dir = install_dir / 'data'
    info(f'Using SQLite at {data_dir}/anetbbs.db (default).')
    db_uri = f'sqlite:///{data_dir}/anetbbs.db'

    # 7. SECRET_KEY
    secret_key = secrets.token_hex(32)

    # 8. Write .env
    step('Writing .env')
    if not existing_env:
        env_lines = [
            'FLASK_ENV=production',
            f'SECRET_KEY={secret_key}',
            f'DATABASE_URL={db_uri}',
            f'DATA_DIR={data_dir}',
            f'LOG_FILE={install_dir}/logs/anetbbs.log',
            'WEB_HOST=0.0.0.0',
            f'WEB_PORT={web_port}',
            f'BBS_NAME={bbs_name}',
            f'BBS_DESCRIPTION={bbs_desc}',
            f'SYSOP_NAME={sysop_name}',
            f'BBS_NODES={bbs_nodes}',
            f'TELNET_ENABLED={"true" if telnet_enabled else "false"}',
            f'TELNET_PORT={telnet_port}',
            f'SSH_ENABLED={"true" if ssh_enabled else "false"}',
            f'SSH_PORT={ssh_port}',
            f'SSH_HOST_KEY_FILE={data_dir}/ssh_host_key',
            f'RLOGIN_ENABLED={"true" if rlogin_enabled else "false"}',
            f'RLOGIN_PORT={rlogin_port}',
            f'ECHOMAIL_ENABLED={"true" if echo_enabled else "false"}',
            'LOG_LEVEL=INFO',
            'IDLE_TIMEOUT_SECONDS=0',
        ]
        if domain:
            env_lines.append(f'BBS_DOMAIN={domain}')
        env_path.write_text('\n'.join(env_lines) + '\n')
        env_path.chmod(0o600)
        ok(f'Wrote {env_path}')
    else:
        info(f'Skipped — {env_path} already exists.')

    # 9. Make dirs
    step('Creating directories')
    for d in ('data', 'logs', 'data/uploads', 'data/avatars',
              'data/file-queue', 'data/text', 'data/text/menus'):
        p = install_dir / d
        p.mkdir(parents=True, exist_ok=True)
    ok('data/, logs/, etc. ready')

    # 10. venv + pip install
    step('Python virtual environment')
    venv = install_dir / 'venv'
    if not venv.exists():
        info('Creating venv (this takes a minute)...')
        subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True)
    pip = venv / 'bin' / 'pip'
    info('Installing/updating Python deps...')
    subprocess.run([str(pip), 'install', '--upgrade', 'pip', 'wheel'],
                   check=True, capture_output=True)
    subprocess.run([str(pip), 'install', '-e', str(install_dir)],
                   check=True)
    ok('Python environment ready')

    # 11. Initialize DB & seed admin
    step('Initializing database')
    pyexe = venv / 'bin' / 'python'
    bootstrap = (
        f"import os, sys; "
        f"sys.path.insert(0, {str(install_dir)!r}); "
        f"os.chdir({str(install_dir)!r}); "
        f"os.environ['FLASK_ENV']='production'; "
        f"os.environ['DATABASE_URL']={db_uri!r}; "
        f"from anetbbs.web_app import create_app; "
        f"app = create_app('production'); "
        f"from anetbbs.models import db, User; "
        f"ctx = app.app_context(); ctx.push(); "
        f"db.create_all(); "
        f"u = User.query.filter_by(username={admin_user!r}).first(); "
        f"u = u or User(); "
        f"u.username={admin_user!r}; u.email={admin_email!r}; "
        f"u.is_admin=True; u.is_active=True; "
        f"u.set_password({admin_pass!r}); "
        f"db.session.add(u); db.session.commit(); "
        f"print('admin id', u.id);"
    )
    r = subprocess.run([str(pyexe), '-c', bootstrap],
                       capture_output=True, text=True)
    if r.returncode != 0:
        err('DB init failed:')
        print(r.stderr)
        sys.exit(1)
    ok('DB initialized + admin user created/updated')

    # 12. systemd units
    step('systemd units (optional)')
    if ask_bool('Install systemd unit files now?', default=True):
        try:
            _install_systemd_units(install_dir,
                                   web_port, telnet_enabled, ssh_enabled,
                                   rlogin_enabled)
            ok('systemd units installed (run `systemctl daemon-reload && '
               'sudo systemctl enable --now anetbbs-web` to start)')
        except Exception as exc:
            err(f'Could not install systemd units: {exc}')
            warn('Skipped — install them by hand from deploy/*.service')
    else:
        info('Skipped — install them later from deploy/*.service')

    # 13. /usr/local/bin shortcuts
    step('Installing short command symlinks')
    try:
        from .symlinks import ensure_symlinks
        results = ensure_symlinks(install_dir)
        for name, status in results:
            if status.startswith('failed'):
                warn(f'  {name}: {status}')
            else:
                ok(f'  {name}: {status}')
    except Exception as exc:
        warn(f'Could not install symlinks: {exc}')

    # 14. Done
    banner('Install Complete')
    print(f'  Install dir:  {C_CYAN}{install_dir}{C_RESET}')
    print(f'  Web URL:      {C_CYAN}http://{domain or "localhost"}:{web_port}{C_RESET}')
    print(f'  Admin login:  {C_CYAN}{admin_user}{C_RESET} / (the password you set)')
    if telnet_enabled:
        print(f'  Telnet:       {C_CYAN}{domain or "localhost"} {telnet_port}{C_RESET}')
    if ssh_enabled:
        print(f'  SSH:          {C_CYAN}ssh -p {ssh_port} {admin_user}@{domain or "localhost"}{C_RESET}')
    print()
    print(f'{C_GREEN}Next steps:{C_RESET}')
    print('  1. Start services: sudo systemctl start anetbbs-web '
          'anetbbs-telnet anetbbs-ssh')
    print('  2. Visit /admin/checklist to verify everything is configured')
    print('  3. /admin/control panel for live ops')
    print()


def _install_systemd_units(install_dir, web_port, tel, ssh, rl):
    user = os.environ.get('SUDO_USER') or os.environ.get('USER') or 'stingray'
    units_dir = install_dir / 'deploy'
    target_dir = Path('/etc/systemd/system')
    if not target_dir.is_dir() or not os.access(str(target_dir), os.W_OK):
        raise RuntimeError(f'{target_dir} not writable — re-run with sudo.')

    common_replacements = [
        ('/opt/anetbbs', str(install_dir)),
        ('User=anetbbs', f'User={user}'),
        ('Group=anetbbs', f'Group={user}'),
    ]

    installed = []
    for unit in ('anetbbs-web.service', 'anetbbs-telnet.service',
                 'anetbbs-ssh.service', 'anetbbs-rlogin.service',
                 'anetbbs-mrc-bridge.service'):
        src = units_dir / unit
        if not src.exists():
            continue
        body = src.read_text()
        for old, new in common_replacements:
            body = body.replace(old, new)
        (target_dir / unit).write_text(body)
        installed.append(unit)

    subprocess.run(['systemctl', 'daemon-reload'], check=False)

    # Enable each unit so it auto-starts on boot. We pick which protocols
    # to enable based on which the sysop turned on in the wizard.
    enable_list = ['anetbbs-web.service']
    if tel and 'anetbbs-telnet.service' in installed:
        enable_list.append('anetbbs-telnet.service')
    if ssh and 'anetbbs-ssh.service' in installed:
        enable_list.append('anetbbs-ssh.service')
    if rl and 'anetbbs-rlogin.service' in installed:
        enable_list.append('anetbbs-rlogin.service')
    for unit in enable_list:
        subprocess.run(['systemctl', 'enable', unit],
                       check=False, capture_output=True)


if __name__ == '__main__':
    main()
