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
# Bold+base combined (1;3X), not bare aixterm 90-97 -- some terminals
# emulating classic ANSI.SYS don't recognize 90-97 (see the same fix
# in anetbbs/features/ansi_ui.py for the full story on the BBS side).
C_CYAN = '\x1b[1;36m'
C_GREEN = '\x1b[1;32m'
C_YELLOW = '\x1b[1;33m'
C_RED = '\x1b[1;31m'
C_GREY = '\x1b[1;30m'


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


def next_steps_lines(service_mode, install_dir):
    """The 'how do I actually start this thing' instructions printed at
    the end of the wizard -- a real bug this fixes: the old version
    printed a single hardcoded `sudo systemctl start anetbbs-web
    anetbbs` line unconditionally, even for a sysop who explicitly
    declined systemd installation a few steps earlier (service_mode
    '3', or '2' before this function existed at all) -- confusing at
    best (a service that was never installed), actively wrong at worst
    (implying root is needed when the whole point of choosing that
    path was to avoid it). Pulled out into its own function (returning
    plain strings, not printing directly) so this specific mapping is
    testable without driving the whole interactive wizard (real venv
    creation, real pip install, real DB init) end to end.
    """
    lines = []
    n = 1
    if service_mode == '1':
        lines.append(f'  {n}. Start services: '
                     f'{C_CYAN}sudo systemctl start anetbbs-web anetbbs{C_RESET}')
        n += 1
    elif service_mode == '2':
        lines.append(f'  {n}. Start services: '
                     f'{C_CYAN}systemctl --user start anetbbs-web anetbbs{C_RESET}')
        n += 1
        lines.append(f'  {n}. Keep them running after you log out: '
                     f'{C_CYAN}loginctl enable-linger $USER{C_RESET}')
        lines.append('     (needs no root on most systems — if it errors, ask '
                     'whoever administers this box to run it once for your '
                     'account)')
        n += 1
    else:
        pyexe = Path(install_dir) / 'venv' / 'bin'
        lines.append(f'  {n}. Start it yourself, e.g. in tmux/screen:')
        lines.append(f'     {C_CYAN}{pyexe}/python {install_dir}/deploy/serve.py'
                     f'{C_RESET}  (web)')
        lines.append(f'     {C_CYAN}{pyexe}/anetbbs{C_RESET}  '
                     '(telnet/SSH/rlogin, if enabled)')
        n += 1
    lines.append(f'  {n}. Visit /admin/checklist to verify everything is configured')
    n += 1
    lines.append(f'  {n}. /admin/control panel for live ops')
    return lines


def build_env_lines(*, secret_key, db_uri, data_dir, install_dir, web_port,
                    bbs_name, bbs_desc, sysop_name, bbs_nodes,
                    telnet_enabled, telnet_port, ssh_enabled, ssh_port,
                    rlogin_enabled, rlogin_port, echo_enabled, domain):
    """Build the .env file content as a list of lines -- pulled out of
    main() so it's directly testable without driving the whole
    interactive wizard (real venv creation, pip install, DB init).

    MSP_ENABLED/SYSTAT_ENABLED are deliberately written as `false`
    unconditionally: config.py's own Config.MSP_ENABLED/.SYSTAT_ENABLED
    default to `true` when unset, but this wizard never asks about
    either (they're privileged ports, 18 and 11, out of scope for this
    quick-start flow), so leaving them out of .env would silently
    inherit "on" and log a permission-denied bind failure on every
    single boot for every install this wizard creates, root or not --
    confirmed live. install.sh's own wizard already gets this right
    (writes *_ENABLED=false whenever the sysop declines that feature in
    ITS OWN prompts); this brings anetbbs-install to the same safe
    default. A failed bind doesn't crash anything else -- this is about
    not logging a needless error on every boot, not a stability fix.
    """
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
        'MSP_ENABLED=false',
        'SYSTAT_ENABLED=false',
    ]
    if domain:
        env_lines.append(f'BBS_DOMAIN={domain}')
    return env_lines


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
    if rlogin_enabled:
        # Unlike the web/telnet/SSH defaults above (5000/2233/2234, all
        # unprivileged), rlogin's own conventional port 513 IS
        # privileged (<1024) -- binding it needs root or an explicitly
        # granted capability. Telnet/SSH avoid this because ANetBBS
        # deliberately doesn't default to their own traditional
        # privileged ports (23/22); rlogin's prompt still offers 513
        # as the default since that's the real protocol convention
        # (some rlogin clients assume it), so this warns rather than
        # silently changing the default out from under a sysop who DOES
        # have root and wants the conventional port.
        warn('Port 513 is privileged (<1024) -- binding it needs root '
             'or an explicitly granted capability. If you don\'t have '
             'root, pick a port above 1024 (e.g. 5130) below.')
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
        env_lines = build_env_lines(
            secret_key=secret_key, db_uri=db_uri, data_dir=data_dir,
            install_dir=install_dir, web_port=web_port, bbs_name=bbs_name,
            bbs_desc=bbs_desc, sysop_name=sysop_name, bbs_nodes=bbs_nodes,
            telnet_enabled=telnet_enabled, telnet_port=telnet_port,
            ssh_enabled=ssh_enabled, ssh_port=ssh_port,
            rlogin_enabled=rlogin_enabled, rlogin_port=rlogin_port,
            echo_enabled=echo_enabled, domain=domain,
        )
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

    # 12. Persistent background service (optional)
    step('Keep it running (optional)')
    info('How should the BBS run persistently, instead of just in this')
    info('terminal? Three options:')
    info(f'  {C_BOLD}1{C_RESET}) System-wide systemd service — needs root once, to write')
    info('     /etc/systemd/system/*. Starts on boot for everyone.')
    info(f'  {C_BOLD}2{C_RESET}) User systemd service — no root at all. Runs under your own')
    info('     account (systemctl --user); survives logout via loginctl')
    info('     linger, which most systems let you enable for yourself.')
    info(f'  {C_BOLD}3{C_RESET}) Skip — run it yourself (a terminal, tmux, screen, or your')
    info('     own supervisor).')
    service_mode = ask('Choice', default='2',
                       validator=lambda s: (s in ('1', '2', '3'),
                                            'Enter 1, 2, or 3.'))
    if service_mode == '1':
        try:
            _install_systemd_units(install_dir,
                                   web_port, telnet_enabled, ssh_enabled,
                                   rlogin_enabled)
            ok('System systemd units installed')
        except Exception as exc:
            err(f'Could not install systemd units: {exc}')
            warn('Skipped — install them by hand from deploy/*.service')
            service_mode = '3'
    elif service_mode == '2':
        try:
            _install_user_systemd_units(install_dir,
                                        web_port, telnet_enabled, ssh_enabled,
                                        rlogin_enabled)
            ok('User systemd units installed (no root used)')
        except Exception as exc:
            err(f'Could not install user systemd units: {exc}')
            warn('Skipped — install them by hand from deploy/user/*.service')
            service_mode = '3'
    else:
        info('Skipped — install one of the templates in deploy/ later, '
             'or just run it yourself.')

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
    for line in next_steps_lines(service_mode, install_dir):
        print(line)
    print()


def _install_systemd_units(install_dir, web_port, tel, ssh, rl):
    user = os.environ.get('SUDO_USER') or os.environ.get('USER')
    if not user:
        raise RuntimeError(
            'Could not determine which user to run the systemd services '
            'as -- neither $SUDO_USER nor $USER is set. Re-run with sudo '
            'from a normal login shell.')
    units_dir = install_dir / 'deploy'
    target_dir = Path('/etc/systemd/system')
    if not target_dir.is_dir() or not os.access(str(target_dir), os.W_OK):
        raise RuntimeError(f'{target_dir} not writable — re-run with sudo.')

    common_replacements = [
        ('/opt/anetbbs', str(install_dir)),
        ('User=anetbbs', f'User={user}'),
        ('Group=anetbbs', f'Group={user}'),
    ]

    # Real gap found in a full install/update re-verify audit: this used
    # to copy anetbbs-telnet.service + anetbbs-ssh.service + anetbbs-
    # rlogin.service verbatim from deploy/ -- but telnet/SSH/rlogin have
    # been ONE combined anetbbs.service for a while now (Ubuntu's
    # systemd EnvironmentFile directive wins over per-unit Environment=
    # overrides, so those old split units couldn't reliably share .env
    # and ended up fighting each other for ports -- see deploy/
    # anetbbs.service's own comment). anetbbs-rlogin.service doesn't
    # even exist as a file in deploy/ any more, so that part silently
    # no-op'd too. Every install.sh/update.sh-provisioned system already
    # writes the single combined unit; this wizard was the one place
    # still installing the broken legacy trio, meaning telnet/SSH/
    # rlogin were effectively non-functional for anyone using this
    # installer. Also inject the same AmbientCapabilities grant install.
    # sh/update.sh add so FTP (port 21, enabled later by hand in .env,
    # same as install.sh's flow) doesn't silently fail to bind.
    installed = []
    for unit in ('anetbbs-web.service', 'anetbbs.service'):
        src = units_dir / unit
        if not src.exists():
            continue
        body = src.read_text()
        for old, new in common_replacements:
            body = body.replace(old, new)
        if 'AmbientCapabilities=' not in body:
            body = body.replace(
                'ExecStart=',
                'AmbientCapabilities=CAP_NET_BIND_SERVICE\n'
                'CapabilityBoundingSet=CAP_NET_BIND_SERVICE\nExecStart=',
                1)
        (target_dir / unit).write_text(body)
        installed.append(unit)

    subprocess.run(['systemctl', 'daemon-reload'], check=False)

    # Enable each unit so it auto-starts on boot. anetbbs.service is one
    # shared process for telnet/SSH/rlogin/FTP/PETSCII -- enable it if
    # the sysop turned on any of the protocols this wizard prompts for.
    enable_list = []
    if 'anetbbs-web.service' in installed:
        enable_list.append('anetbbs-web.service')
    if (tel or ssh or rl) and 'anetbbs.service' in installed:
        enable_list.append('anetbbs.service')
    for unit in enable_list:
        subprocess.run(['systemctl', 'enable', unit],
                       check=False, capture_output=True)


def _install_user_systemd_units(install_dir, web_port, tel, ssh, rl):
    """The no-root counterpart to _install_systemd_units() above --
    writes systemd USER units (~/.config/systemd/user/, managed with
    `systemctl --user`, no sudo anywhere in this function) instead of
    system-level ones. Real differences from the system-level path,
    not just a changed target directory:

    - No User=/Group= substitution -- a user unit always runs as
      whichever account owns the user systemd instance, there's no
      other identity it could run as.
    - No AmbientCapabilities grant -- user units can't bind privileged
      ports (<1024) without additional systemd privilege delegation
      most distros don't enable by default, so this deliberately does
      NOT attempt to make MSP/SYSTAT/Finger/FTP work here. Those
      already require root in the system-level path (see docs/
      INSTALL.md §6) and stay out of scope for a genuinely rootless
      install -- this wizard's own default ports (5000/2233/2234) are
      already unprivileged, which is the whole point.
    - WantedBy=default.target, not multi-user.target -- the correct
      target for a user-mode unit; multi-user.target doesn't exist in
      a user systemd instance's own target graph.

    Does NOT call `loginctl enable-linger` itself -- that's a
    one-time, separate decision the sysop makes explicitly (see the
    wizard's own printed next-steps), not something to do silently as
    a side effect of installing unit files. Whether it needs root
    varies by system (many distros allow a user to enable their own
    linger via polkit; some hardened setups don't) -- attempting it
    silently here and failing partway through would be a worse
    experience than a clear, separate instruction the sysop can act on
    (or ask a box admin to run once) when they're ready.
    """
    units_dir = install_dir / 'deploy' / 'user'
    target_dir = Path.home() / '.config' / 'systemd' / 'user'
    target_dir.mkdir(parents=True, exist_ok=True)

    installed = []
    for unit in ('anetbbs-web.service', 'anetbbs.service'):
        src = units_dir / unit
        if not src.exists():
            continue
        body = src.read_text().replace('/opt/anetbbs', str(install_dir))
        (target_dir / unit).write_text(body)
        installed.append(unit)

    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=False)

    enable_list = []
    if 'anetbbs-web.service' in installed:
        enable_list.append('anetbbs-web.service')
    if (tel or ssh or rl) and 'anetbbs.service' in installed:
        enable_list.append('anetbbs.service')
    for unit in enable_list:
        subprocess.run(['systemctl', '--user', 'enable', unit],
                       check=False, capture_output=True)


if __name__ == '__main__':
    main()
