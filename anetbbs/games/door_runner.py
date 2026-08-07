# anetbbs/games/door_runner.py
"""
PTY Process Manager for ANetBBS Game Center door games.

Handles launching door games (DOS via DOSBox, native binaries, etc.) in a
pseudo-terminal and bridging I/O to the WebSocket/xterm.js layer.
"""
import os
import pty
import fcntl
import signal
import struct
import termios
import logging
import threading
from datetime import datetime

from ..models import db, GameSession
from .dropfile import write_drop_file
from .node_manager import allocate_node, release_node
from .node_paths import build_token_context, expand_tokens

logger = logging.getLogger(__name__)

# dosemu2 packaging varies a lot by distro -- natively apt-packageable on
# Debian/Ubuntu, needs a third-party repo (RPM Fusion or a COPR) on
# Fedora/RHEL, AUR-only on Arch (not in the official repos at all), and
# openSUSE's status is unclear. A bare "sudo apt install dosemu2" was
# wrong/useless for anyone not on Debian/Ubuntu -- this covers all four.
DOSEMU2_INSTALL_HINT = (
    "Install dosemu2: Debian/Ubuntu: sudo apt install dosemu2 | "
    "Fedora/RHEL: enable RPM Fusion or a COPR providing dosemu2, then "
    "sudo dnf install dosemu2 | "
    "Arch: AUR-only (yay -S dosemu2 or paru -S dosemu2) | "
    "openSUSE: check the Packman repo or build from source "
    "(https://github.com/dosemu2/dosemu2)"
)

# session_id -> DoorSession
_sessions = {}
_sessions_lock = threading.Lock()

# Pending DOS bridges waiting for PTY to be bound — keyed by tmp_id stamped
# into the dosbox conf path so launch_door_game's parent can pair them up.
_bridge_registry = {}


class DoorSession:
    """Represents a running door game PTY session."""

    def __init__(self, session_id, master_fd, pid):
        self.session_id = session_id
        self.master_fd = master_fd
        self.pid = pid
        self.started_at = datetime.utcnow()
        # For door_dosemu pts mode: the COM1 pts slave fd used for both
        # reading game output and writing user keystrokes (COM1 RX).
        # com1_w doubles as both the write and read fd in pts mode.
        # None for all other game types (PTY master is used for all I/O).
        self.com1_w = None
        # pts symlink path for door_dosemu — cleaned up on close.
        self.pts_path = None
        # Optional TCP bridge for DOSBox sessions. Set by launch_door_game
        # right after construction for door_dos game type. None otherwise.
        self.dos_bridge = None
        self.startup_delay_secs = 0.0
        # Temp files (currently: Synchronet Node.js compat script + combined
        # run script) that must persist on disk for the life of the game
        # process, and so can't self-delete at creation time -- cleaned up
        # in close(). Empty for game types that don't create any.
        self.temp_files = []

    def write(self, data):
        """Write raw bytes to the door (user → game)."""
        try:
            if isinstance(data, str):
                data = data.encode('utf-8', errors='replace')
        except (UnicodeDecodeError, AttributeError):
            return
        # door_dos: route keystrokes through the TCP bridge so they land on
        # DOSBox's nullmodem-emulated COM1. The PTY's master_fd isn't
        # connected to anything useful in this mode (DOSBox's stdin is
        # ignored — its actual I/O is via the TCP serial).
        if self.dos_bridge is not None:
            self.dos_bridge.write(data)
            return
        # Startup filter for door_dosemu (startup_delay_secs > 0).
        # Block ALL browser input during the startup window so no keystroke
        # can pollute COM1 RX before TW2002's connection check and ESC[6n
        # exchange are complete (both handled server-side via _pty_reader).
        if self.startup_delay_secs > 0.0:
            elapsed = (datetime.utcnow() - self.started_at).total_seconds()
            if elapsed < self.startup_delay_secs:
                return
        # Filter Ctrl+C (0x03) to prevent DOS INT 23h / BREAK from aborting
        # the running program via the PTY.
        data = data.replace(b'\x03', b'')
        if not data:
            return
        # door_dosemu: keyboard goes to the COM1 RX pipe, not the PTY master.
        _out_fd = self.com1_w if self.com1_w is not None else self.master_fd
        try:
            os.write(_out_fd, data)
        except OSError as exc:
            logger.warning('PTY write error (session %d): %s', self.session_id, exc)

    def resize(self, rows, cols):
        """Resize the PTY window."""
        try:
            winsize = struct.pack('HHHH', rows, cols, 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
        except OSError as exc:
            logger.warning('PTY resize error (session %d): %s', self.session_id, exc)

    def close(self):
        """Terminate the game process and clean up the PTY fd + TCP bridge."""
        try:
            os.kill(self.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError as exc:
            logger.warning('Kill error (session %d): %s', self.session_id, exc)
        if self.dos_bridge is not None:
            try:
                self.dos_bridge.stop()
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning('Bridge stop error (session %d): %s',
                               self.session_id, exc)
            self.dos_bridge = None
        try:
            os.close(self.master_fd)
        except OSError:
            pass
        if self.com1_w is not None:
            try:
                os.close(self.com1_w)
            except OSError:
                pass
            self.com1_w = None
        # pts symlink is removed by dosemu2 on exit; clean up defensively.
        if self.pts_path is not None:
            try:
                os.unlink(self.pts_path)
            except OSError:
                pass
            self.pts_path = None
        for _tf in self.temp_files:
            try:
                os.unlink(_tf)
            except OSError:
                pass
        self.temp_files = []


def _js_str(s):
    """Quote a string for safe insertion in a JS expression."""
    import json
    return json.dumps(s)


def _resolve_path(p, base=None):
    """Convert a possibly-relative path to absolute, anchored at base."""
    if not p:
        return p
    if os.path.isabs(p):
        return p
    if base is None:
        # This file is anetbbs/games/door_runner.py — install root is 3 dirs up.
        base = os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
    return os.path.normpath(os.path.join(base, p))


def _find_jsexec(game):
    """Locate a Synchronet jsexec binary on the host. Returns the absolute
    path or None. Honors a few common install layouts in priority order:
      1. game.synchronet_exec_dir/jsexec   (per-game override)
      2. $SBBSEXEC                          (env var)
      3. /sbbs/exec/jsexec                  (Synchronet install convention)
      4. /opt/synchronet/exec/jsexec
      5. /usr/local/sbbs/exec/jsexec
      6. shutil.which('jsexec')             (PATH lookup)
    """
    import shutil
    candidates = []
    exec_dir = (game.synchronet_exec_dir or '').strip() if game else ''
    if exec_dir:
        candidates.append(os.path.join(exec_dir, 'jsexec'))
    sbbs_exec = os.environ.get('SBBSEXEC') or os.environ.get('SBBS_EXEC')
    if sbbs_exec:
        candidates.append(os.path.join(sbbs_exec, 'jsexec') if os.path.isdir(sbbs_exec) else sbbs_exec)
    candidates.extend([
        '/sbbs/exec/jsexec',
        '/opt/synchronet/exec/jsexec',
        '/usr/local/sbbs/exec/jsexec',
        '/usr/local/synchronet/exec/jsexec',
    ])
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    found = shutil.which('jsexec')
    return found if found and os.path.isfile(found) else None


def _write_msgbase_ini_override(game, exec_dir):
    """When a sysop has configured an InterBBS score-sharing area for
    this door (Game.msgbase_area_id), write/update its own real
    modopts.ini config file with `sub = <area tag>` before each launch --
    Minesweeper's own real, documented config path (confirmed against
    its own sysop docs, pasted directly by Jerry) for the same "syncdata"
    override its own syncdata.js auto-detect would otherwise try to find
    by scanning message areas. Using this path instead of building real
    msg_area.sub enumeration is a deliberate, smaller-scoped choice --
    see the design plan for why.

    Applies to BOTH the real-jsexec path and the Node compat-shim
    fallback -- this is the real Synchronet engine's own config
    convention, not something specific to either runtime.

    Read-modify-write: only ever touches the `sub` key in the
    `[minesweeper]` section, preserving anything else a sysop may have
    hand-edited in the same file. Best-effort -- any failure here (DB
    unavailable, disk error) is logged and swallowed, never blocks the
    door from launching; msgbase_area_id being unset at all is the
    common case and returns immediately with no DB/disk touch.
    """
    area_id = getattr(game, 'msgbase_area_id', None)
    if not area_id:
        return
    try:
        import configparser
        from flask import has_app_context

        def _resolve_tag():
            from ..models import EchoArea
            area = EchoArea.query.get(area_id)
            return area.tag if area else None

        if has_app_context():
            tag = _resolve_tag()
        else:
            # Same throwaway Flask+DB bootstrap used elsewhere in this
            # file (play_door_game_telnet) -- _build_command can be
            # called from contexts with no app context already pushed.
            from flask import Flask
            from anetbbs.config import get_config
            from ..features.db_scope import transient_app_context
            _app = Flask(__name__)
            _app.config.from_object(get_config(os.environ.get('FLASK_ENV', 'production')))
            db.init_app(_app)
            with transient_app_context(_app):
                tag = _resolve_tag()

        if not tag:
            logger.warning('msgbase_area_id=%s on game %s has no matching '
                           'EchoArea (deleted?) -- skipping modopts.ini write',
                           area_id, getattr(game, 'slug', '?'))
            return

        ini_path = os.path.join(exec_dir, 'modopts.ini')
        parser = configparser.ConfigParser()
        parser.optionxform = str  # preserve key case -- real Synchronet inis are case-sensitive
        if os.path.isfile(ini_path):
            try:
                parser.read(ini_path)
            except configparser.Error as exc:
                logger.warning('modopts.ini at %s unparseable, rewriting: %s',
                               ini_path, exc)
        if not parser.has_section('minesweeper'):
            parser.add_section('minesweeper')
        parser.set('minesweeper', 'sub', tag)
        os.makedirs(exec_dir, exist_ok=True)
        with open(ini_path, 'w') as f:
            parser.write(f)
    except Exception as exc:
        logger.warning('msgbase_area_id modopts.ini write failed for game %s: %s',
                       getattr(game, 'slug', '?'), exc)


def _find_mplc():
    """Locate Mystic's .mps Pascal compiler.

    Search order:
      1. ``$MYSTIC_MPLC_PATH``                (explicit env override)
      2. ``$MYSTIC_BBS_PATH``'s sibling      (install.sh's mystic-bundle layout)
      3. ``/opt/mystic/mplc``                 (recommended bundle path)
      4. ``shutil.which('mplc')``             (PATH lookup)
      5. ``/usr/local/bin/mplc``              (manual sysop install)

    Returns the absolute path or ``None`` if mplc isn't installed.
    """
    import shutil
    cand = []
    env_override = os.environ.get('MYSTIC_MPLC_PATH', '').strip()
    if env_override:
        cand.append(env_override)
    mystic_bin = os.environ.get('MYSTIC_BBS_PATH', '').strip()
    if mystic_bin:
        cand.append(os.path.join(os.path.dirname(mystic_bin), 'mplc'))
    cand.extend([
        '/opt/mystic/mplc',
        '/usr/local/bin/mplc',
    ])
    for c in cand:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    found = shutil.which('mplc')
    return found if found and os.path.isfile(found) else None


def _ensure_mps_compiled(mps_path, cwd):
    """If mps_path is a .mps source, compile it to .mpx with mplc when:

      - mplc is available on the host AND
      - either the .mpx doesn't exist OR the .mps is newer than the .mpx

    Returns the path the runtime should actually load (.mpx if compile
    succeeded; the original .mps if mplc isn't installed — caller can decide
    whether to error). Returns None on compile failure (caller falls back
    to the original path and the runtime will produce its own error).
    """
    import subprocess
    if not mps_path or not mps_path.lower().endswith('.mps'):
        return mps_path
    mpx_path = mps_path[:-4] + '.mpx'

    # If the bytecode is up-to-date, skip the compile.
    try:
        if os.path.isfile(mpx_path) and os.path.getmtime(mpx_path) >= os.path.getmtime(mps_path):
            return mpx_path
    except OSError:
        pass

    mplc = _find_mplc()
    if not mplc:
        # No compiler — assume the sysop committed a pre-compiled .mpx
        # next to the .mps. If neither exists we'll let the runtime
        # fail downstream with its own clear error.
        return mpx_path if os.path.isfile(mpx_path) else mps_path

    try:
        result = subprocess.run(
            [mplc, mps_path],
            cwd=cwd or os.path.dirname(mps_path) or '/tmp',  # nosec B108 -- subprocess cwd scratch default, no persistent data written
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            logger.warning('mplc failed for %s: %s', mps_path,
                           (result.stderr or result.stdout).strip()[:300])
            return mps_path  # let runtime produce its own error
        if not os.path.isfile(mpx_path):
            logger.warning('mplc returned 0 but %s not produced', mpx_path)
            return mps_path
        logger.info('Compiled %s -> %s', mps_path, mpx_path)
        return mpx_path
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning('mplc invocation failed: %s', exc)
        return mps_path


def _build_command(game, node_number, bbs_name='ANetBBS', user=None,
                   token_ctx=None, bridge_port=None, temp_files_out=None):
    """
    Build the command list for launching a door game.
    Returns a (cmd_list, cwd) tuple. Raises descriptive errors on
    misconfiguration so the user sees what's wrong instead of a blank fail.

    `temp_files_out`: an optional list the caller provides. If the game
    type creates temp files that must persist on disk for the life of
    the game process (currently: the Synchronet Node.js compat path's
    two generated .js files), their paths are appended here so the
    caller can delete them once the session ends. Without this, every
    Synchronet-JS door launch orphaned two files in /tmp forever —
    confirmed live: hundreds had accumulated on a real install over
    the project's lifetime, contributing to a disk-full incident.

    `user`: the BBS user launching the game. Threaded through so the
    Synchronet compat script can populate user.alias / user.name correctly
    instead of defaulting to "Guest".

    `token_ctx`: a substitution map from `node_paths.build_token_context()`.
    If supplied, every Synchronet/Mystic %-token in `executable_path`,
    `working_directory`, and `command_line_args` is expanded before use.
    Sysops can write `working_directory=%P` for "this node's temp dir"
    or `command_line_args=%f` to feed the drop-file path on the command
    line, the same way Synchronet's xtrn.cnf works.
    """
    def _xp(value):
        """Expand BBS tokens in a string value if a context is provided."""
        if not value or token_ctx is None:
            return value
        return expand_tokens(value, token_ctx)

    # Pick a sensible cwd default based on game type (each type uses different
    # path fields). All paths get resolved to absolute against the install dir.
    if game.game_type in ('door_dos', 'door_dosemu', 'door_native'):
        anchor = game.executable_path or game.working_directory
    elif game.game_type in ('door_mystic', 'door_mystic_mps'):
        anchor = game.mystic_script_path or game.working_directory
    elif game.game_type == 'door_synchronet':
        anchor = (game.synchronet_exec_dir or game.synchronet_script_path
                  or game.working_directory)
    else:
        anchor = game.working_directory or '/tmp'  # nosec B108 -- subprocess cwd scratch default

    # Expand tokens BEFORE path resolution so `%P` (per-node temp dir)
    # works as both the working_directory AND inside executable_path /
    # command_line_args.
    anchor = _xp(anchor)
    wd_raw = _xp(game.working_directory or '')
    cwd_raw = (wd_raw or
               (os.path.dirname(_resolve_path(anchor)) if anchor else '/tmp'))  # nosec B108 -- subprocess cwd scratch default
    cwd = _resolve_path(cwd_raw) or '/tmp'  # nosec B108 -- subprocess cwd scratch default

    if game.game_type == 'door_dos':
        return _build_dos_command(game, node_number, cwd, token_ctx=token_ctx,
                                   bridge_port=bridge_port)

    if game.game_type == 'door_dosemu':
        import shutil
        dosemu = (shutil.which('dosemu2') or shutil.which('dosemu')
                  or shutil.which('dosemu2-bin') or shutil.which('dosemu.bin'))
        if not dosemu:
            raise FileNotFoundError(
                f'dosemu2 not found on this server. {DOSEMU2_INSTALL_HINT}'
            )
        return _build_dosemu_command(game, node_number, dosemu,
                                     token_ctx=token_ctx)

    if game.game_type == 'door_native':
        exe_raw = _xp(game.executable_path or '')
        exe = _resolve_path(exe_raw)
        if not exe or not os.path.isfile(exe):
            raise FileNotFoundError(
                f'Native door executable not found: {exe!r} '
                f'(was {exe_raw!r}). Set Game.executable_path to a real binary.'
            )
        if not os.access(exe, os.X_OK):
            raise PermissionError(f'Native door not executable (chmod +x?): {exe}')
        cmd = [exe]
        extra = _xp(game.command_line_args or '')
        if extra:
            # `{node}` legacy placeholder kept for back-compat with games
            # configured before the %-token vocabulary landed.
            cmd += [a.replace('{node}', str(node_number)) for a in extra.split()]
        return cmd, cwd if wd_raw else os.path.dirname(exe)

    if game.game_type == 'door_mystic':
        return _build_mystic_python_command(game, cwd)

    if game.game_type == 'door_mystic_mps':
        mystic = os.environ.get('MYSTIC_BBS_PATH', '/usr/local/bin/mystic')
        if not os.path.isfile(mystic):
            raise FileNotFoundError(
                f'Mystic BBS runtime not found at {mystic!r}. '
                'Install Mystic BBS (install.sh has an opt-in step) or set '
                'MYSTIC_BBS_PATH env var.'
            )
        script_raw = _xp(game.mystic_script_path or '')
        script = script_raw
        if not script or not os.path.isfile(script):
            raise FileNotFoundError(f'Mystic .mps script not found: {script!r}')

        # Auto-compile .mps -> .mpx if mplc is available and the source is
        # newer than the bytecode. It's normal for sysops to drop a raw
        # .mps source they got from a door pack. We compile silently so
        # they don't have to remember the build step. If mplc isn't on
        # the host, fall back to the existing path (which assumes the
        # script is already compiled, i.e., a .mpx file the sysop
        # produced manually).
        compiled = _ensure_mps_compiled(script, cwd)
        if compiled:
            script = compiled

        # Real bug found live (first-ever end-to-end test of this door
        # type): `-x` is not a real Mystic BBS command-line flag -- it's
        # not documented anywhere in Mystic's own install guide or
        # changelog, and empirically the binary just ignores it and
        # falls through to a full interactive local-login session
        # (matching -l's documented behavior), not the door script at
        # all. The real flag to run a compiled MPL script standalone,
        # under a specific user's identity, is `-y<script>` (`-Y` is a
        # case-insensitive alias) combined with `-u<username>
        # -p<password>` for the account it should run under -- Mystic
        # has no concept of running a script anonymously.
        #
        # command_line_args reused here exactly like door_rlogin already
        # does for its own remote-login credentials: "USER_TEMPLATE
        # PASSWORD", where USER_TEMPLATE may contain @USER@ to substitute
        # the real ANetBBS caller's username. Unlike rlogin (a remote
        # system that accepts an arbitrary client-supplied identity
        # string), Mystic requires a REAL, already-existing local user
        # account matching -u -- @USER@ only works if the sysop has
        # actually created a matching Mystic account for that ANetBBS
        # username ahead of time. A single shared account (e.g. a
        # dedicated "anetbbs" service user, template written as a fixed
        # name with no @USER@ token) is the simpler, more realistic
        # setup for most doors that don't need real per-caller Mystic
        # identity.
        raw_args = (game.command_line_args or '').strip()
        parts = raw_args.split(None, 1)
        if len(parts) < 2:
            raise ValueError(
                'door_mystic_mps: command_line_args must be '
                '"USERNAME_OR_@USER@ PASSWORD" -- Mystic requires a real, '
                'already-created local user account to run a script under '
                '(see the Working Directory field\'s help text). '
                'e.g. "@USER@ mypassword" or "anetbbs mypassword" for a '
                'single shared account.')
        user_template, mystic_password = parts[0], parts[1]
        if isinstance(user, dict):
            caller_username = (user.get('username') or 'guest').strip()
        else:
            caller_username = (getattr(user, 'username', None) or 'guest').strip()
        mystic_username = user_template.replace('@USER@', caller_username)

        return [mystic, f'-u{mystic_username}', f'-p{mystic_password}',
                f'-y{script}'], cwd

    if game.game_type == 'door_synchronet':
        script_raw = game.synchronet_script_path or ''
        script = _resolve_path(script_raw)
        if not script or not os.path.isfile(script):
            raise FileNotFoundError(f'Synchronet script not found: {script!r}')

        # Applies to BOTH the jsexec and compat-shim paths below -- real
        # Synchronet's own config convention, not shim-specific. No-op
        # when Game.msgbase_area_id isn't set (the common case).
        _write_msgbase_ini_override(
            game, game.synchronet_exec_dir or os.path.dirname(script))

        # Prefer real Synchronet jsexec when it's available on the host —
        # stock Synchronet doors expect the real runtime (msg_area, bbs.exec,
        # File.iniGetValue, etc.). Our Node shim covers a useful subset but
        # not everything. Look in standard locations OR honor an env override.
        jsexec = (os.environ.get('SBBS_JSEXEC')
                  or _find_jsexec(game))
        if jsexec:
            # Native path: jsexec runs the script directly with full
            # Synchronet API. We pass the SBBS* env vars in the child fork.
            cwd = (game.synchronet_exec_dir
                   or os.path.dirname(jsexec)
                   or os.path.dirname(script))
            return [jsexec, script], cwd

        # Fallback: Node.js + compat shim
        node_path = os.environ.get('NODEJS_PATH', '/usr/bin/node')
        if not os.path.isfile(node_path):
            raise FileNotFoundError(
                f'Neither jsexec (set SBBS_JSEXEC env or install Synchronet) '
                f'nor Node.js found at {node_path!r}. '
                'Install nodejs (apt install nodejs) or set NODEJS_PATH env var.'
            )
        from .synchronet_compat import write_compat_script
        compat_path = write_compat_script(game, user, node_number, bbs_name)
        # Concatenate compat + user script into ONE file so top-level `var`
        # declarations from the compat (like `var _path = require('path');`)
        # are visible inside functions defined in either half. Two separate
        # eval() calls don't share their top-level scope in node -e mode.
        import json
        import tempfile
        try:
            with open(compat_path, 'r') as f:
                compat_src = f.read()
            if not os.path.isfile(script):
                raise FileNotFoundError(f'Synchronet script not found: {script!r}')
        except OSError as exc:
            raise FileNotFoundError(f'Synchronet script read error: {exc}')

        # Strip the compat's trailing "// === Execute the actual game ===" block.
        # That block does load(process.argv[last]) which when we concat into one
        # file points at the COMBINED file — recursive eval (each re-eval resets
        # _load_cache) → "Maximum call stack size exceeded".
        marker = '// === Execute the actual game ==='
        if marker in compat_src:
            compat_src = compat_src.split(marker)[0]
        # The door's own top-level script is run via the compat shim's own
        # load() (NOT concatenated in as literal trailing statements) —
        # load() runs code via vm.runInThisContext, so any bare top-level
        # `var`/`function` the door declares (e.g. Bubble Boggle's
        # boggle.js: `var root = js.exec_dir; ... load(root +
        # "game.js");`) becomes a REAL globalThis property, visible to
        # whatever the door itself subsequently load()s — exactly the
        # same scoping load()'d files already get from each other. A
        # single-file door (Chicken Delivery, LORD) behaves identically
        # either way; a multi-file door where an entry script's own vars
        # need to be visible to a file IT loads (Bubble Boggle) only
        # works via this path — literal concatenation put the door's
        # vars in the combined file's Node module-wrapper scope, which
        # vm.runInThisContext-executed code (anything reached via
        # load()) can't see at all. load() also applies
        # _polyfillE4XForEach to the door's own source here, same as any
        # other file it loads (needed for Bubble Boggle's game.js).
        # Wrap in a try/catch that:
        #   1. writes the error to stdout AND stderr (PTY-visible in both)
        #   2. emits start/end markers to stderr so silent-exit failures (game
        #      runs to completion drawing nothing) are diagnosable
        #   3. waits for any keystroke before exiting on error (so the user can
        #      read the error before the BBS clears the screen on game-end)
        combined = (
            "// === ANetBBS Synchronet compat (auto-prepended) ===\n"
            + compat_src + "\n"
            "// === User script: " + script + " ===\n"
            "// Synchronet doors commonly end with:\n"
            "//   if (typeof module === 'undefined' || !module.exports) { main(); }\n"
            "// Code run via load() (vm.runInThisContext) never sees Node's\n"
            "// own `module` at all, so `typeof module` is naturally\n"
            "// 'undefined' there and this idiom fires correctly with no\n"
            "// special-casing needed.\n"
            "try {\n"
            "  load(" + json.dumps(script) + ");\n"
            "} catch (e) {\n"
            "  var trace = (e && e.stack) ? e.stack : String(e);\n"
            "  var msg = '\\r\\n\\r\\n--- Door script error ---\\r\\n'\n"
            "          + trace\n"
            "          + '\\r\\n\\r\\nPress any key to return to BBS...';\n"
            "  process.stdout.write(msg);\n"
            "  // The compat shim's own process.stdout.write is patched to\n"
            "  // buffer writes and flush on the next event-loop tick (see\n"
            "  // synchronet_compat.py's _flushStdoutNow doc comment) so\n"
            "  // bursts of small console writes coalesce into one real\n"
            "  // write(). The readSync() call below is a blocking syscall\n"
            "  // that does NOT yield to the event loop at all -- without an\n"
            "  // explicit flush here, this error message (and its 'Press\n"
            "  // any key' prompt) never reaches the terminal before the\n"
            "  // process blocks waiting for a keystroke nobody was ever\n"
            "  // shown a reason to press, silently hanging instead of\n"
            "  // showing the crash. Found live: Good Time Trivia's own\n"
            "  // msg_area.sub gap (a real, separate shim bug) triggered an\n"
            "  // uncaught exception that produced zero visible output and\n"
            "  // an indefinitely blocked process -- this flush gap is what\n"
            "  // hid it, and would have hidden the error message for ANY\n"
            "  // door crash, not just this one.\n"
            "  if (typeof _flushStdoutNow === 'function') { try { _flushStdoutNow(); } catch(_) {} }\n"
            "  // Also write the trace to logs/door-errors.log on the server\n"
            "  // so the sysop can diagnose without depending on the user to\n"
            "  // copy-paste. ANETBBS_DOOR_ERROR_LOG is set by door_runner.\n"
            "  try {\n"
            "    var elog = process.env.ANETBBS_DOOR_ERROR_LOG;\n"
            "    if (elog) {\n"
            "      var stamp = new Date().toISOString();\n"
            "      var slug = process.env.ANETBBS_DOOR_SLUG || '?';\n"
            "      var who  = process.env.BBS_USERNAME || '?';\n"
            "      var line = '\\n[' + stamp + '] door=' + slug\n"
            "               + ' user=' + who + '\\n' + trace + '\\n';\n"
            "      _fs.appendFileSync(elog, line);\n"
            "    }\n"
            "  } catch(_) {}\n"
            "  try { var b = Buffer.alloc(1); _fs.readSync(0, b, 0, 1); } catch(_) {}\n"
            "  process.exit(1);\n"
            "}\n"
        )
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='_synchronet_run.js',
                                          prefix='anetbbs_', delete=False)
        tmp.write(combined)
        tmp.close()
        if temp_files_out is not None:
            temp_files_out.append(compat_path)
            temp_files_out.append(tmp.name)
        sn_cwd = os.path.dirname(script)

        # Server-side door-crash log. The embedded catch handler appends
        # to this file on any uncaught Synchronet-compat door error, so
        # sysops don't have to wait for users to report breakage. Path
        # under logs/ so it gets rotated alongside the rest.
        install_root_for_log = os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
        door_err_log = os.path.join(install_root_for_log, 'logs',
                                    'door-errors.log')
        try:
            os.makedirs(os.path.dirname(door_err_log), exist_ok=True)
        except OSError:
            pass
        os.environ['ANETBBS_DOOR_ERROR_LOG'] = door_err_log
        os.environ['ANETBBS_DOOR_SLUG'] = (getattr(game, 'slug', '') or '').strip()

        # anetbbs/games/sbbs_stubs/json-client.js (the drop-in replacement
        # for Synchronet's real json-client.js -- see that file's own
        # docstring) shells out to jsonrpc_client.py for the actual wire
        # protocol, since there's no synchronous TCP primitive available
        # inside the Node compat shim. It needs to know which Python
        # interpreter to run (the same venv ANetBBS itself runs under, not
        # whatever "python3" happens to resolve to on the host) and where
        # jsonrpc_client.py actually lives -- both resolved once here
        # rather than guessed from inside the JS environment.
        import sys as _sys
        os.environ['ANETBBS_JSONRPC_CLI_PYTHON'] = _sys.executable
        os.environ['ANETBBS_JSONRPC_CLI_PATH'] = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'jsonrpc_client.py')
        # Same reasoning, same pattern, for anetbbs/games/sbbs_stubs/http.js
        # (the drop-in replacement for Synchronet's real http.js) shelling
        # out to http_client.py -- no synchronous TCP primitive for it to
        # build a real HTTPRequest/Socket on top of either. Reuses the same
        # interpreter env var (selecting which python3 to run is not
        # script-specific) with its own CLI path.
        os.environ['ANETBBS_HTTP_CLI_PATH'] = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'http_client.py')

        # Per-door runtime state: doors that persist state (TW2's json-client,
        # etc.) must NOT write into the source tree — the source dir is owned
        # by the install-account (the sysop's own login) and the runtime user
        # (anetbbs uid 998) can't create files there. Route writes to
        # {INSTALL}/data/sbbs_doors/<slug>/ which the runtime user owns.
        # The child reads ANETBBS_TW2_DB_DIR (and any other future
        # ANETBBS_<SLUG>_DB_DIR) to find its writable home; existing doors
        # ignore the env var and keep working.
        slug = (getattr(game, 'slug', '') or '').strip()
        if slug == 'tw2':
            # Resolve install root: this file is anetbbs/games/door_runner.py
            install_root = os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))))
            tw2_db = os.path.join(install_root, 'data', 'sbbs_doors',
                                  'tw2', 'db')
            try:
                os.makedirs(tw2_db, exist_ok=True)
            except OSError as exc:
                logger.warning('tw2: cannot create db dir %s: %s',
                               tw2_db, exc)
            os.environ['ANETBBS_TW2_DB_DIR'] = tw2_db

        return [node_path, tmp.name], sn_cwd

    if game.game_type == 'builtin_python':
        import sys as _sys
        _runner = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               'builtin_runner.py')
        _module = getattr(game, 'web_game_module', '') or ''
        if not _module:
            raise ValueError('builtin_python game has no web_game_module set')
        return [_sys.executable, _runner, _module], '/tmp'  # nosec B108 -- subprocess cwd scratch default

    raise ValueError(f'Unsupported door game_type: {game.game_type!r}')


def _build_dos_command(game, node_number, cwd, token_ctx=None,
                       bridge_port=None):
    """Build a DOSBox invocation using TCP nullmodem (works with VANILLA DOSBox).

    Architecture (adapted from binkterm-php's dosbox-bridge):
    - DOSBox config has `serial1=nullmodem server:127.0.0.1 port:NNNN`
    - A DosBridge listens on that port; DOSBox dials in
    - BBS session reads/writes flow through TCP <-> PTY <-> session
    - DOS door uses BNU.COM FOSSIL driver to talk to "COM1" (= our TCP)

    This pattern works with vanilla DOSBox 0.74-3+ on any CPU — no SIGILL,
    no snap, no special build. dosbox-staging / dosbox-x also work and are
    preferred because they're more modern, but vanilla is the fallback.

    `token_ctx`: substitution map from `node_paths.build_token_context()`.
    When set, Synchronet/Mystic %-tokens in `executable_path`,
    `working_directory`, and `command_line_args` are expanded before use.
    """
    def _xp(value):
        if not value or token_ctx is None:
            return value
        return expand_tokens(value, token_ctx)

    import tempfile
    from shutil import which

    dosbox_staging = which('dosbox-staging')
    dosbox_x = which('dosbox-x')
    vanilla = os.environ.get('DOSBOX_PATH') or which('dosbox')

    # Snap detector — snap-packaged binaries route through `snap-confine`
    # which demands `cap_dac_override` from the calling process. Our systemd
    # unit deliberately runs with only CAP_NET_BIND_SERVICE (privilege of
    # least), so snap dosbox/dosbox-staging exits with the cryptic
    # "snap-confine is packaged without necessary permissions / required
    # permitted capability cap_dac_override not found" error. We REJECT
    # snap candidates up front so the sysop sees a clear "this won't work"
    # message instead of having to debug the cap message on first launch.
    def _is_snap(path):
        if not path:
            return False
        try:
            real = os.path.realpath(path)
        except OSError:
            return False
        return real.startswith('/snap/') or '/snap/bin/' in (path or '')

    snap_rejected = []
    for label, p in (('dosbox-staging', dosbox_staging),
                     ('dosbox-x', dosbox_x),
                     ('dosbox', vanilla)):
        if _is_snap(p):
            snap_rejected.append((label, p, os.path.realpath(p) if p else None))
    if snap_rejected:
        for label, p, real in snap_rejected:
            logger.warning('Skipping snap-packaged %s at %s -> %s '
                           '(snap-confine needs cap_dac_override which our '
                           'service unit does not grant)',
                           label, p, real)
        if _is_snap(dosbox_staging): dosbox_staging = None
        if _is_snap(dosbox_x):       dosbox_x       = None
        if _is_snap(vanilla):        vanilla        = None

    # Prefer dosbox-staging or dosbox-x (modern, more features) but FALL BACK
    # to vanilla DOSBox — it supports `serial1=nullmodem` and is enough for
    # BBS doors via TCP-bridge. We must also VERIFY each candidate actually
    # runs (--version succeeds): an install can leave a broken binary on PATH
    # (wrong arch, snap-stub for newer CPUs that SIGILLs, etc.) and execvp()
    # would die with Errno 8 "Exec format error" before we got useful output.
    def _runnable(path):
        # Just verify the file exists and is executable. A subprocess --version
        # probe sounds appealing for arch-mismatch detection, but dosbox-x
        # (and some dosbox-staging builds) hang on SDL/audio init when run
        # headlessly — even with SDL_VIDEODRIVER=dummy — causing the check to
        # time out and falsely report the binary as unusable. If apt installed
        # it, the arch is correct; if someone put a wrong-arch binary on PATH
        # they'll get a clear exec-format error when the door actually launches.
        return bool(path and os.path.isfile(path) and os.access(path, os.X_OK))

    dosbox = None
    _tried = []
    for cand in (dosbox_staging, dosbox_x, vanilla):
        if _runnable(cand):
            dosbox = cand
            break
        if cand:
            _tried.append(cand)
    if not dosbox:
        msg_lines = ['No usable DOSBox found.']
        if _tried:
            msg_lines.append(f'Tried but failed: {", ".join(_tried)}')
        if snap_rejected:
            msg_lines.append(
                'Detected snap-packaged binaries — those don\'t work because '
                'snap-confine requires capabilities our service unit refuses '
                'to grant. Remove them and install a real binary:'
            )
        msg_lines.extend([
            '  sudo snap remove dosbox dosbox-staging dosbox-x  # if any are snap-installed',
            '  sudo apt install dosbox-x                        # preferred (more compatible)',
            '  sudo apt install dosbox                          # vanilla — also works for BBS doors via TCP nullmodem',
            '  # OR install dosbox-staging from GitHub release tarball into /opt/dosbox-staging',
            f'  # OR set game type to door_dosemu if you have dosemu2 ({DOSEMU2_INSTALL_HINT})',
        ])
        raise FileNotFoundError('\n'.join(msg_lines))

    exe_path_raw = _xp(game.executable_path or '')
    if not exe_path_raw:
        raise ValueError(
            f'Game {game.slug!r} has no executable_path. Set it in /admin/games/.'
        )
    exe_path = _resolve_path(exe_path_raw)

    extra = _xp(game.command_line_args or '').replace('{node}', str(node_number)).strip()
    wd_raw = _xp(game.working_directory or '')
    wd = _resolve_path(wd_raw) if wd_raw else None

    # If exe_path_raw is relative and doesn't resolve at the install root,
    # try resolving it against working_directory. Sysops naturally expect
    # "executable_path=TW2002.EXE + working_directory=/path/to/TW2002" to work.
    if (not os.path.isabs(exe_path_raw) and wd
            and not os.path.isfile(exe_path) and not os.path.isdir(exe_path)):
        candidate = os.path.normpath(os.path.join(wd, exe_path_raw))
        if os.path.isfile(candidate) or os.path.isdir(candidate):
            exe_path = candidate

    if os.path.isdir(exe_path):
        # Convention B: executable_path is the game dir; first token of args is the exe
        game_dir = exe_path
        if not extra:
            raise ValueError(
                f'Game {game.slug!r}: executable_path {exe_path!r} is a directory '
                'but command_line_args is empty. Set command_line_args to '
                '"GAME.EXE [args...]" or set executable_path to the .exe instead.'
            )
        parts = extra.split()
        exe_name = parts[0]
        extra = ' '.join(parts[1:])
    elif os.path.isfile(exe_path):
        # Convention A: executable_path is the .exe
        game_dir = wd or os.path.dirname(exe_path)
        exe_name = os.path.basename(exe_path)
    else:
        raise FileNotFoundError(
            f'executable_path does not exist: {exe_path!r} '
            f'(was {exe_path_raw!r} before resolving relative paths).'
        )

    if not os.path.isdir(game_dir):
        raise FileNotFoundError(
            f'Game directory does not exist: {game_dir!r}.'
        )

    # Use dosbox-staging's stdio support for now. The TCP-nullmodem approach
    # (better, works on vanilla DOSBox) is staged as infrastructure in
    # dos_bridge.py + dos_runtime/FOSSIL/ but needs a fork() refactor before
    # we can wire it up properly. Until then, dosbox-staging built for the
    # local CPU is the path that works.
    fossil_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'dos_runtime')

    # Per-node scratch dir — auto-mounted as E: so doors can read/write a
    # node-specific drop file without colliding with other concurrent
    # players. Sysop configures LORDCFG / TWCFG / etc. with `E:\` as the
    # dropfile path; the BBS writes the drop file into the host's
    # <install>/data/temp/nodeN/ which DOSBox sees as E:\.
    try:
        from .node_paths import node_dir as _node_dir
        per_node_host = _node_dir(node_number).rstrip(os.sep) or None
    except Exception:
        per_node_host = None
    mount_e_line = (f'mount e "{per_node_host}"\n' if per_node_host else '')

    # Both vanilla DOSBox and DOSBox-staging support `serial1=nullmodem`.
    # serial1=stdio was removed from staging around 0.80; we rely on the
    # nullmodem path which is stable across forks. The TCP listener is
    # set up by `dos_bridge.DosBridge` BEFORE this function gets called
    # for actual launches; `bridge_port` is passed in. Validation-only
    # callers (admin "preview command", terminal pre-launch sanity
    # check) call this with bridge_port=None — we just emit a placeholder
    # port so the config still validates and the cmd list shape is real.
    if bridge_port is None:
        bridge_port = 0

    nullmodem_line = (f'serial1=nullmodem server:127.0.0.1 port:{bridge_port} '
                      f'transparent:1 telnet:0\n')
    serial_section = (
        "[serial]\n"
        + nullmodem_line
        + "serial2=disabled\n"
    )
    autoexec = (
        "[autoexec]\n"
        "@echo off\n"
        f'mount c "{game_dir}"\n'
        f'mount d "{fossil_dir}"\n'
        + mount_e_line +
        "set PATH=D:\\FOSSIL;%PATH%\n"
        "BNU /P1\n"   # load FOSSIL on COM1 (= our TCP bridge)
        # Always copy the freshly-generated drop file from E:\ (per-node scratch,
        # written by write_drop_file) into C:\ (game dir) so the door reads
        # current session data.  Without this, a stale C:\DOOR.SYS from a
        # previous session (possibly with wrong line 15 format) silently poisons
        # games like LORD that look for DOOR.SYS in their own directory.
        "IF EXIST E:\\DOOR.SYS COPY E:\\DOOR.SYS C:\\DOOR.SYS >NUL\n"
        "IF EXIST E:\\DOOR32.SYS COPY E:\\DOOR32.SYS C:\\DOOR32.SYS >NUL\n"
        "IF EXIST E:\\DORINFO1.DEF COPY E:\\DORINFO1.DEF C:\\DORINFO1.DEF >NUL\n"
        "c:\n"
        f"{exe_name} {extra}\n"
        "exit\n"
    )

    headless = not os.environ.get('DISPLAY')

    if dosbox in (dosbox_staging, dosbox_x):
        # output=texture for headless operation via xvfb-run.
        # dosbox-staging 0.82+ maps the deprecated 'surface' to 'opengl', which
        # aborts when SDL uses X11 without a GLX-capable context (xvfb has no
        # OpenGL).  'texture' uses SDL2's software texture renderer which works
        # on xvfb's X11 display without requiring OpenGL.  dosbox-x also accepts
        # 'texture'.  'auto' would fall back to opengl as well, so we pin it.
        sdl_output = 'output=texture\n' if headless else ''
        conf = (
            "[sdl]\n"
            + sdl_output +
            "fullscreen=false\n"
            "\n"
            + serial_section +
            "\n"
            "[dos]\n"
            "xms=true\n"
            "ems=true\n"
            "\n"
            + autoexec
        )
    else:
        # Vanilla DOSBox 0.74-3 — same nullmodem config works.
        conf = (
            "[sdl]\n"
            "output=surface\n"
            "fullscreen=false\n"
            "\n"
            + serial_section +
            "\n"
            + autoexec
        )
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='_dosbox.conf',
                                      prefix='anetbbs_dos_', delete=False)
    tmp.write(conf)
    tmp.close()
    logger.info('DOS door config written to %s (dosbox=%s, headless=%s)',
                tmp.name, os.path.basename(dosbox), headless)
    base_cmd = [dosbox, '-conf', tmp.name, '-noconsole', '-exit']
    if headless:
        from shutil import which
        xvfb = which('xvfb-run')
        if xvfb:
            base_cmd = [xvfb, '-a'] + base_cmd
    # Run DOSBox FROM the game directory so any relative file paths inside
    # the autoexec resolve as expected.
    return base_cmd, game_dir


def _build_dosemu_command(game, node_number, dosemu_path, token_ctx=None):
    """dosemu2 invocation for BBS doors (FOSSIL/SHARE-based doors like TW2002).

    Why dosemu instead of DOSBox? dosemu2 runs FDPP (real FreeDOS) which
    includes SHARE.EXE. DOSBox's SHARE emulation is a stub that doesn't
    actually support file locking — TradeWars 2002 and similar doors check
    for SHARE on startup and will fail or hang without it.

    Architecture: ``$_com1 = "pts /tmp/..."`` has dosemu2 create an internal
    PTY pair, hold the master end, and symlink the slave at the given path.
    COM1 TX (door game output) flows: DOS program → UART 0x3F8 → dosemu2
    com_write() → tty_write(pts_master) → pts slave → ANetBBS reads slave →
    browser.  COM1 RX (user keystrokes) flows: ANetBBS writes slave → pts
    master → dosemu2 COM1 RX buffer → DOS program reads.  DCD=1 is guaranteed
    by dosemu2's ``pseudo=TRUE`` flag set automatically in pts mode.

    The outer PTY (master_fd/slave_fd) is kept only for terminal resize
    (TIOCSWINSZ) and dosemu2 process-exit detection.

    Returns ``(cmd, game_dir)`` — standard 2-tuple.  pts_path and conf_path
    are derived from node_number and os.getpid() and embedded in cmd via
    the ``-f`` option; launch_door_game re-derives them by the same formula.

    Drive layout (dosemu2 with FDPP assigns C:–F: to internal FreeDOS drives;
    user ``-d`` mounts start at G:):

      ``G:`` = FOSSIL bundle (``anetbbs/games/dos_runtime/``)
      ``H:`` = game dir (``executable_path``'s dir, or ``working_directory``)
      ``I:`` = per-node scratch dir (``data/temp/nodeN/``) — optional

    In the door's config point the drop-file path at ``I:\\`` (e.g.,
    ``I:\\DOOR32.SYS`` or ``I:\\DOOR.SYS``).
    """
    def _xp(value):
        if not value or token_ctx is None:
            return value
        return expand_tokens(value, token_ctx)

    exe_path = _resolve_path(_xp(game.executable_path or ''))
    if not exe_path:
        raise ValueError(f'Game {game.slug!r}: executable_path is empty.')

    extra = _xp(game.command_line_args or '').strip()
    # `{node}` legacy placeholder kept for back-compat.
    extra = extra.replace('{node}', str(node_number))

    if os.path.isdir(exe_path):
        game_dir = exe_path
        if not extra:
            raise ValueError(
                f'Game {game.slug!r}: executable_path is a dir but '
                'command_line_args is empty.'
            )
        parts = extra.split()
        exe_name, extra = parts[0], ' '.join(parts[1:])
    elif os.path.isfile(exe_path):
        wd_raw = _xp(game.working_directory or '')
        game_dir = (_resolve_path(wd_raw) if wd_raw
                    else os.path.dirname(exe_path))
        exe_name = os.path.basename(exe_path)
    else:
        raise FileNotFoundError(f'executable_path not found: {exe_path!r}')

    # G: = dos_runtime/ (available for future use; FOSSIL drivers not used in Standard mode)
    fossil_base = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               'dos_runtime')

    # I: = per-node scratch dir (drop file lands here, multi-node safe)
    try:
        from .node_paths import node_dir as _node_dir
        per_node_host = _node_dir(node_number).rstrip(os.sep) or None
    except Exception:
        per_node_host = None

    _slug_lc = (game.slug or '').lower()
    _is_tw2002 = 'tw2002' in _slug_lc or exe_name.lower().startswith('tw2002')

    # Load FOSSIL driver only when the game admin explicitly opted in via the
    # "Requires FOSSIL Driver" checkbox.  Auto-detection was removed because
    # dosemu2 virtual COM1 (used by TW2002) conflicts with FOSSIL drivers.
    _fossil_names = {'bnu.com', 'fossil.com', 'x00.com', 'fosdrv.com', 'bnu2.com'}
    _fossil_load = ''
    if getattr(game, 'needs_fossil_driver', False):
        try:
            for _fn in os.listdir(game_dir):
                if _fn.lower() in _fossil_names:
                    _fossil_load = f'{_fn}\r\n'
                    break
        except OSError:
            pass

    # %P in command_line_args expands to a Linux per-node path via _xp(), but
    # inside a dosemu2 bat I: is the per-node scratch drive. Fix it up here.
    if per_node_host and extra:
        _pn_slash = per_node_host.rstrip(os.sep) + os.sep
        extra = extra.replace(_pn_slash, 'I:\\').replace(per_node_host.rstrip(os.sep), 'I:\\')

    cmd_str = (exe_name + (' ' + extra if extra else '')).strip()
    bat_path = os.path.join(game_dir, '_ANET.BAT')

    # TW2002-specific setup: TWNODE env var selects the node config entry in
    # TWNODE.DAT, and node 1 maps its DOOR.SYS path to I:\NODE2\DOOR.SYS.
    # Other dosemu2 games don't need any of this — only inject it for TW2002.
    tw2002_lines = ''
    if _is_tw2002:
        tw2002_lines = (
            # Node 0 in TWNODE.DAT is LOCL (local — ignores DOOR.SYS and
            # serial); node 1 is DOOR mode.  Set TWNODE=1 before launch.
            'SET TWNODE=1\r\n'
            # TW2002 node 1 stores its drop-file path as I:\NODE2\DOOR.SYS.
            # Create the subdir and copy so TW2002 reads the GR (ANSI) flag.
            'MKDIR I:\\NODE2 2>NUL\r\n'
            'IF EXIST I:\\DOOR.SYS COPY I:\\DOOR.SYS I:\\NODE2\\DOOR.SYS >NUL\r\n'
        )

    bat_body = (
        '@echo off\r\n'
        + _fossil_load
        + tw2002_lines +
        # Generic: copy drop file from per-node scratch (I:\) into game dir
        # (H:\) so the door finds it in its own working directory.
        'IF EXIST I:\\DOOR.SYS COPY I:\\DOOR.SYS H:\\DOOR.SYS >NUL\r\n'
        'IF EXIST I:\\DOOR32.SYS COPY I:\\DOOR32.SYS H:\\DOOR32.SYS >NUL\r\n'
        'IF EXIST I:\\DORINFO1.DEF COPY I:\\DORINFO1.DEF H:\\DORINFO1.DEF >NUL\r\n'
        'H:\r\n'                        # switch to game drive
        'CD \\\r\n'                     # root of game drive
        + cmd_str + '\r\n'
        'EXIT\r\n'
    )
    try:
        os.makedirs(game_dir, exist_ok=True)
        with open(bat_path, 'w', newline='') as f:
            f.write(bat_body)
    except OSError as exc:
        raise OSError('Cannot write {}: {}'.format(bat_path, exc))

    logger.info('dosemu2 bat written to %s (game=%s, node=%d)',
                bat_path, game.slug, node_number)

    # pts COM1 config: dosemu2 creates an internal PTY pair, holds the master,
    # and symlinks the slave at pts_path.  ANetBBS polls for the symlink then
    # opens it as com1_slave_fd.  DCD=1 is automatic (pseudo=TRUE).
    from .node_paths import temp_root as _temp_root
    _tr = _temp_root()
    os.makedirs(_tr, exist_ok=True)
    pts_path = os.path.join(_tr, f'anetbbs_com1_n{node_number}_{os.getpid()}')
    conf_path = os.path.join(_tr, f'anetbbs_dosemu_n{node_number}_{os.getpid()}.conf')
    try:
        with open(conf_path, 'w') as _cf:
            _cf.write(f'$_com1 = "pts {pts_path}"\n')
            _cf.write('$_layout = "us"\n')
            _cf.write('$_loglevel = (0)\n')
    except OSError as exc:
        raise OSError(f'Cannot write dosemu2 conf {conf_path}: {exc}')

    # Use the raw binary directly — never the wrapper (/usr/bin/dosemu).
    # The Debian/Ubuntu wrapper converts -dumb → "-td -ks"; -ks starts a
    # keyboard scanner on fd 0 that conflicts with pts COM1 RX.
    _raw_bin = '/usr/libexec/dosemu2/dosemu2.bin'
    if os.path.isfile(_raw_bin):
        _exe = _raw_bin
        try:
            _dosemu_dir = os.path.join(os.path.expanduser('~'), '.dosemu')
            os.makedirs(_dosemu_dir, exist_ok=True)
            _boot_log = os.path.join(_dosemu_dir, 'boot.log')
        except OSError:
            _boot_log = '/tmp/dosemu_boot.log'  # nosec B108 -- transient debug log, safe to lose
        logger.info('dosemu2: using raw binary %s', _raw_bin)
    else:
        _exe = dosemu_path
        _boot_log = None
        logger.info('dosemu2: using wrapper %s', dosemu_path)

    cmd = [_exe]
    if _boot_log:
        cmd += ['-o', _boot_log]
    cmd += [
        # -td routes INT 10h text to stdout (outer PTY).  Not used for game
        # I/O (that flows via COM1 pts slave), but kept so FreeDOS boot
        # messages appear on the outer PTY for diagnostics.
        '-td',
        '-f', conf_path,               # pts COM1 config
        '-d', fossil_base,             # G: = FOSSIL bundle
        '-d', game_dir,                # H: = game dir
    ]
    if per_node_host:
        cmd.extend(['-d', per_node_host])  # I: = per-node scratch
    cmd.extend(['-E', 'H:\\_ANET.BAT'])
    # Return 2-tuple (cmd, game_dir).  pts_path and conf_path are derivable
    # by any caller that knows node_number and os.getpid() via the same formula
    # used above.  launch_door_game re-derives them after the fork.
    return cmd, game_dir


def _build_mystic_python_command(game, cwd):
    """For door_mystic Python scripts: run them via a wrapper that injects
    mystic_compat module-level helpers so `write/writeln/getstr/...` work
    without explicit imports (matching Mystic's bbs_io global behavior).

    Auto-detects Python 2 vs 3 — many existing Mystic scripts are Py2 since
    Mystic's embedded interpreter has been Python 2 historically. If the
    script doesn't parse with Python 3, we fall back to Python 2.
    """
    import tempfile
    from shutil import which

    script_raw = game.mystic_script_path or ''
    script = _resolve_path(script_raw)
    if not script or not os.path.isfile(script):
        raise FileNotFoundError(f'Mystic Python script not found: {script!r}')
    cwd = os.path.dirname(script)

    # Read source + try to compile with Py3; if SyntaxError, prefer Py2
    try:
        with open(script, 'r', errors='replace') as f:
            src = f.read()
    except OSError as exc:
        raise FileNotFoundError(f'Cannot read Mystic script: {exc}')

    py3 = os.environ.get('MYSTIC_PYTHON_PATH', '') or which('python3') or 'python3'
    py2 = which('python2') or which('python2.7') or ''

    is_py2 = False
    try:
        compile(src, script, 'exec')
    except SyntaxError as e:
        if py2 and ("Missing parentheses in call to 'print'" in str(e)
                    or "invalid syntax" in str(e)):
            is_py2 = True
        else:
            raise SyntaxError(
                f'{script}: Python 3 syntax error and no python2 fallback '
                f'available.\n  {e}\n'
                'Either: (a) install python2 (apt install python2.7), or '
                '(b) port the script to Python 3 (use 2to3 -w {script}).'
            )

    python = py2 if is_py2 else py3
    pkg_dir = os.path.dirname(os.path.abspath(__file__))

    # Wrapper: install a fake `mystic_bbs` module pointing at our compat helpers
    # (real Mystic ships embedded mystic_bbs; scripts often `import mystic_bbs`).
    # Then exec the user's script in our wrapper's globals so the helpers are
    # available as bare names too. Also disables PTY echo so typed chars
    # appear once (kernel ECHO + our manual echo would double-print otherwise).
    # We DON'T set cbreak/raw mode globally here — Mystic Python scripts often
    # use stdin in line mode (ICANON) for getstr() and rely on the terminal
    # buffering "20\r" before any read returns. Setting cbreak here breaks
    # multi-digit menu input. Instead, mystic_compat.ReadKey() temporarily
    # toggles raw mode for one-key reads, and getstr() uses line-buffered reads.
    # Use runpy.run_path() so the script runs in a REAL __main__ module
    # (creates sys.modules['__main__'] properly). This fixes pickle.dump()
    # of script-defined classes — pickle uses sys.modules to find the class
    # at unpickle time, and "in-script exec()" doesn't create a real module.
    wrapper = (
        "import sys, os, types, runpy\n"
        f"sys.path.insert(0, {pkg_dir!r})\n"
        "# Fake 'mystic_bbs' module pointing at mystic_compat helpers\n"
        "import mystic_compat as _mc\n"
        "_fake_mb = types.ModuleType('mystic_bbs')\n"
        "for _name in dir(_mc):\n"
        "    if not _name.startswith('_'):\n"
        "        setattr(_fake_mb, _name, getattr(_mc, _name))\n"
        "sys.modules['mystic_bbs'] = _fake_mb\n"
        "sys.modules['bbs_io'] = _fake_mb\n"
        "sys.modules['bbs'] = _fake_mb\n"
        "# Pre-inject the bare-name compat helpers into the script's globals\n"
        "_init_g = {}\n"
        "for _n in ('write','writeln','pwrite','pwriteln','rwrite','rwriteln',\n"
        "           'showfile','getstr','onekey','pause','gotoxy','wherex','wherey',\n"
        "           'clrscr','textcolor','textbackground','getuser','Input','ReadKey',\n"
        "           'WriteXY','PadRt','PadLt','PadCt','Int2Str','StrComma'):\n"
        "    _init_g[_n] = getattr(_mc, _n)\n"
        "# Add the script's dir to sys.path so its sibling .py files are importable\n"
        f"sys.path.insert(0, os.path.dirname({script!r}))\n"
        "# run_path creates a REAL __main__ module (fixes pickle of script classes)\n"
        "# AND sets __file__ to the script's path (fixes script_dir lookups for ANSI).\n"
        f"runpy.run_path({script!r}, init_globals=_init_g, run_name='__main__')\n"
    )
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='_mystic_runner.py',
                                      prefix='anetbbs_', delete=False)
    tmp.write(wrapper)
    tmp.close()
    return [python, tmp.name], cwd


def launch_door_game(game, user, socketio_emit_fn, bbs_name='ANetBBS',
                     minutes_remaining=60):
    """
    Allocate a node, write drop file, fork PTY child, and start reader thread.

    Args:
        game: Game model instance
        user: User model instance
        socketio_emit_fn: Callable(output_bytes) that emits to the client
        bbs_name: BBS name string
        minutes_remaining: Session time budget

    Returns:
        GameSession.id on success, or None on failure (e.g., all nodes full)
    """
    node = allocate_node(game.id, game.max_nodes or 1, -1)
    if node is None:
        logger.warning('No free nodes for game %s', game.slug)
        return None

    # Create DB session record. user can be a model (web) or dict (telnet/SSH).
    _uid = (user.get('id') if isinstance(user, dict) else getattr(user, 'id', None)) or 0
    gs = GameSession(
        game_id=game.id,
        user_id=_uid,
        node_number=node,
        status='active',
    )
    db.session.add(gs)
    db.session.commit()

    # Update node manager with real session id
    from .node_manager import _active, _lock
    with _lock:
        _active[(game.id, node)] = gs.id

    # Build the substitution context once. `drop_file_path` gets filled
    # in after we actually write the drop file (so `%f` can resolve to
    # the post-substitution path the game will read).
    sysop = os.environ.get('SYSOP_NAME', 'Sysop')
    token_ctx = build_token_context(
        user=user, node_number=node,
        minutes_left=minutes_remaining, bbs_name=bbs_name,
        sysop_name=sysop,
    )

    # Write drop file (drop_file_path itself gets %-token expanded).
    drop_path = None
    try:
        drop_path = write_drop_file(user, game, node, minutes_remaining,
                                     bbs_name, token_ctx=token_ctx)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning('Drop file error for game %s: %s', game.slug, exc)

    # Now that we know the actual drop file path, plug it into the
    # context so `%f` resolves correctly inside command_line_args.
    if drop_path:
        token_ctx['%f'] = drop_path

    # For door_dos: spin up the TCP nullmodem bridge BEFORE building the
    # command — DOSBox's config needs the port written into it.
    dos_bridge = None
    bridge_port = None
    if game.game_type == 'door_dos':
        try:
            from .dos_bridge import DosBridge
            dos_bridge = DosBridge()
            bridge_port = dos_bridge.start()
            dos_bridge.accept_async()
        except Exception as exc:  # pylint: disable=broad-except
            logger.error('Failed to start DosBridge for game %s: %s',
                         game.slug, exc)
            if dos_bridge:
                try: dos_bridge.stop()
                except Exception: pass
            gs.status = 'crashed'
            gs.ended_at = datetime.utcnow()
            db.session.commit()
            release_node(game.id, node)
            return None

    # Fork PTY
    # For door_dosemu: pts_path and conf_path use the same formula as
    # _build_dosemu_command so we can retrieve them after _build_command
    # returns its standard 2-tuple.
    if game.game_type == 'door_dosemu':
        from .node_paths import temp_root as _temp_root
        _tr = _temp_root()
        _dosemu_pts_path = os.path.join(_tr, f'anetbbs_com1_n{node}_{os.getpid()}')
        _dosemu_conf_path = os.path.join(_tr, f'anetbbs_dosemu_n{node}_{os.getpid()}.conf')
    else:
        _dosemu_pts_path = None
        _dosemu_conf_path = None

    _door_temp_files = []
    try:
        cmd, cwd = _build_command(game, node, bbs_name, user=user,
                                  token_ctx=token_ctx,
                                  bridge_port=bridge_port,
                                  temp_files_out=_door_temp_files)
        logger.info('Launching game %s (type=%s, node=%d): %s',
                    game.slug, game.game_type, node,
                    ' '.join(str(c) for c in cmd))
    except Exception as exc:  # pylint: disable=broad-except
        logger.error('Failed to build command for game %s: %s', game.slug, exc)
        for _tf in _door_temp_files:
            try: os.unlink(_tf)
            except OSError: pass
        if dos_bridge:
            try: dos_bridge.stop()
            except Exception: pass
        gs.status = 'crashed'
        gs.ended_at = datetime.utcnow()
        db.session.commit()
        release_node(game.id, node)
        return None

    # door_dosemu: always write DOOR.SYS directly into the game directory
    # (H:\) in addition to whatever drop_file_path is configured.  The BAT
    # file copies I:\DOOR.SYS → H:\DOOR.SYS, but if the per-node scratch
    # dir (I:\) is not mounted or drop_file_path is unconfigured, TW2002
    # finds no DOOR.SYS and defaults to 0 minutes → "BBS Time Expired".
    # Writing it here guarantees the file exists in H:\ before dosemu2 forks.
    if game.game_type == 'door_dosemu':
        try:
            from .dropfile import generate_door_sys
            _dsys_path = os.path.join(cwd, 'DOOR.SYS')
            generate_door_sys(user, node, minutes_remaining, bbs_name,
                              _dsys_path, tw2002_compat=True)
            logger.info('door_dosemu: DOOR.SYS → %s (drop_path=%s, min=%d)',
                        _dsys_path, drop_path, minutes_remaining)
        except Exception as _dsys_exc:
            logger.warning('door_dosemu: DOOR.SYS write to game dir failed: %s',
                           _dsys_exc)

    com1_rx_r = com1_rx_w = -1  # kept for code-path compatibility

    try:
        master_fd, slave_fd = pty.openpty()
        # Set initial winsize to 80x25 — the DOS-terminal contract every
        # BBS door is written for. Synchronet's dorkit + LORD assume 25
        # rows; without an explicit TIOCSWINSZ the slave PTY defaults
        # to (0, 0) on some kernels (e.g. Debian 12), making
        # `console.screen_rows` come out 0 inside the door and breaking
        # gotoxy positioning. The browser's xterm.js is also pinned at
        # 80x25 so we match here.
        try:
            winsize = struct.pack('HHHH', 25, 80, 0, 0)
            fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)
        except OSError as exc:
            logger.warning('PTY initial winsize set failed: %s', exc)
    except OSError as exc:
        logger.error('Failed to open PTY for game %s: %s', game.slug, exc)
        for _tf in _door_temp_files:
            try: os.unlink(_tf)
            except OSError: pass
        gs.status = 'crashed'
        gs.ended_at = datetime.utcnow()
        db.session.commit()
        release_node(game.id, node)
        return None

    try:
        pid = os.fork()
    except OSError as exc:
        logger.error('Failed to fork process for game %s: %s', game.slug, exc)
        os.close(master_fd)
        os.close(slave_fd)
        for _tf in _door_temp_files:
            try: os.unlink(_tf)
            except OSError: pass
        if dos_bridge:
            try: dos_bridge.stop()
            except Exception: pass
        gs.status = 'crashed'
        gs.ended_at = datetime.utcnow()
        db.session.commit()
        release_node(game.id, node)
        return None

    if pid == 0:
        # Child process
        os.close(master_fd)
        # For door_dosemu: COM1 RX comes from the pipe, not the PTY slave.
        # Close the write end — parent keeps it. Read end becomes fd 0 below.
        if com1_rx_w >= 0:
            try: os.close(com1_rx_w)
            except OSError: pass
        os.setsid()
        fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
        # All game types: fd 0/1/2 = PTY slave.
        # door_dosemu: COM1 uses /dev/tty (= slave_fd, the controlling terminal).
        os.dup2(slave_fd, 0)
        os.dup2(slave_fd, 1)
        os.dup2(slave_fd, 2)
        os.close(slave_fd)
        os.chdir(cwd)
        # Pass user/session info to the child via env vars so doors can read them.
        # Mystic Python compat helpers + sbbs.js stub both look at these.
        try:
            # user can be a SQLAlchemy model OR a plain dict (telnet/SSH/rlogin
            # sessions use a dict). getattr() returns the default for dicts
            # (silently empty), so check both forms.
            def _ufield(field, default=''):
                if isinstance(user, dict):
                    return user.get(field, default)
                return getattr(user, field, default)
            os.environ['BBS_USERNAME'] = str(_ufield('username') or '')
            os.environ['BBS_REAL_NAME'] = str(_ufield('display_name') or
                                              _ufield('username') or '')
            os.environ['BBS_EMAIL'] = str(_ufield('email') or '')
            os.environ['BBS_SECURITY'] = '255' if _ufield('is_admin', False) else '50'
            os.environ['BBS_TIME_LEFT'] = str(minutes_remaining)
            os.environ['BBS_LOGIN_COUNT'] = str(_ufield('login_count', 0) or 0)
            os.environ['BBS_NODE_NUMBER'] = str(node)
            os.environ['BBS_NAME'] = bbs_name
            # Sysop name for Synchronet @SYSOP@ expansion. Pull from app
            # config when we have a Flask context; otherwise fall back to
            # whatever the spawning process inherited.
            try:
                from flask import current_app as _ca
                _sysop = (_ca.config.get('SYSOP_NAME', '')
                          if _ca else '') or os.environ.get('SYSOP_NAME', '')
            except Exception:
                _sysop = os.environ.get('SYSOP_NAME', '')
            if _sysop:
                os.environ['BBS_SYSOP_NAME'] = _sysop
            os.environ['TERM'] = os.environ.get('TERM', 'ansi')
            os.environ['COLUMNS'] = '80'
            os.environ['LINES'] = '25'

            # Synchronet-specific env vars — needed by both jsexec (real
            # runtime) and stock JS doors that read them. Inferred from
            # the per-game synchronet_exec_dir or the global SBBSEXEC env
            # the sysop set; falls back to the conventional /sbbs layout.
            sbbs_exec_dir = (
                getattr(game, 'synchronet_exec_dir', None)
                or os.environ.get('SBBSEXEC')
                or '/sbbs/exec'
            )
            sbbs_root = os.path.dirname(sbbs_exec_dir.rstrip('/')) or '/sbbs'
            sbbs_ctrl = os.environ.get('SBBSCTRL') or os.path.join(sbbs_root, 'ctrl')
            sbbs_data = os.environ.get('SBBSDATA') or os.path.join(sbbs_root, 'data')
            sbbs_node = os.environ.get('SBBSNODE') or os.path.join(
                sbbs_root, f'node{node}')
            os.environ.setdefault('SBBSEXEC', sbbs_exec_dir)
            os.environ.setdefault('SBBSCTRL', sbbs_ctrl)
            os.environ.setdefault('SBBSDATA', sbbs_data)
            os.environ.setdefault('SBBSNODE', sbbs_node)
            os.environ.setdefault('SBBS_NODE_NUM', str(node))
        except Exception:
            pass
        # For DOS doors: suppress SDL audio probes (no speakers needed for BBS
        # doors). Do NOT set SDL_VIDEODRIVER=dummy — dosbox-staging 0.82+ probes
        # for OpenGL even in headless mode and aborts with "SDL: Could not
        # initialize video" when the dummy driver can't satisfy it.  xvfb-run
        # (added to the command in _build_dos_command) provides a real X11
        # virtual display, so SDL uses x11 and dosbox works correctly.
        if game.game_type == 'door_dos':
            os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')
        # For DOS doors: redirect dosbox stdout+stderr to a per-game log file.
        # The dos_bridge uses the TCP nullmodem for actual door I/O; dosbox's
        # stdio goes nowhere useful (PTY watcher discards it with a no-op
        # lambda). Redirecting to a log lets sysops see SDL init failures,
        # BNU errors, or any other dosbox crash output that would otherwise
        # be silently lost. Check logs/dosbox_<slug>_nodeN.log after a failure.
        if game.game_type == 'door_dos':
            try:
                _install_root = os.path.dirname(os.path.dirname(
                    os.path.dirname(os.path.abspath(__file__))))
                _log_dir = os.path.join(_install_root, 'logs')
                os.makedirs(_log_dir, exist_ok=True)
                _slug = (getattr(game, 'slug', None) or 'door').strip() or 'door'
                _dlog = os.path.join(_log_dir,
                                     f'dosbox_{_slug}_node{node}.log')
                _lfd = os.open(_dlog,
                               os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
                os.dup2(_lfd, 1)   # dosbox stdout → log
                os.dup2(_lfd, 2)   # dosbox stderr → log
                os.close(_lfd)
            except OSError:
                pass
        # For dosemu2 doors, fd layout after child setup:
        #   fd 0 → PTY slave   (outer PTY; dosemu2 reads for -td keyboard if -ks)
        #   fd 1 → PTY slave   (-td INT 10h video → master_fd, used for diagnostics)
        #   fd 2 → log file    (dosemu2 startup/crash output)
        # COM1 I/O goes through the pts pair (config file), NOT the outer PTY.
        # setraw on fd 1 so -td INT 10h video passes through without ONLCR.
        if game.game_type == 'door_dosemu':
            try:
                import tty as _tty
                _tty.setraw(1)  # raw mode on PTY slave (fd 1)
            except Exception:
                pass
            try:
                _install_root = os.path.dirname(os.path.dirname(
                    os.path.dirname(os.path.abspath(__file__))))
                _log_dir = os.path.join(_install_root, 'logs')
                os.makedirs(_log_dir, exist_ok=True)
                _slug = (getattr(game, 'slug', None) or 'door').strip() or 'door'
                _dlog = os.path.join(_log_dir,
                                     f'dosemu_{_slug}_node{node}.log')
                _lfd = os.open(_dlog,
                               os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
                os.dup2(_lfd, 2)   # dosemu2 stderr/errors → log
                os.close(_lfd)
            except OSError:
                pass
        os.execvp(cmd[0], cmd)
        os._exit(1)

    # Parent
    os.close(slave_fd)
    # door_dosemu: child owns the read end; we keep only the write end.
    if com1_rx_r >= 0:
        try: os.close(com1_rx_r)
        except OSError: pass
    gs.pid = pid
    db.session.commit()

    door_session = DoorSession(gs.id, master_fd, pid)
    door_session.dos_bridge = dos_bridge   # None for non-door_dos sessions
    door_session.temp_files = _door_temp_files

    # door_dosemu pts COM1: poll for the symlink dosemu2 creates at startup,
    # then open the slave fd.  dosemu2 creates it before FreeDOS boots, so
    # it typically appears within 50-200 ms.
    _com1_slave_fd = None
    if game.game_type == 'door_dosemu' and _dosemu_pts_path:
        import time as _t
        _t0 = _t.time()
        while _t.time() - _t0 < 6.0:
            if os.path.exists(_dosemu_pts_path):
                try:
                    _com1_slave_fd = os.open(_dosemu_pts_path,
                                             os.O_RDWR | os.O_NOCTTY)
                    # PTY line discipline ECHO would mirror every byte dosemu2
                    # writes (COM1 TX) back to the master as COM1 RX, so
                    # TW2002 would receive its own ESC[6n probe echoed back
                    # instead of our ESC[24;80R response → ASCII fallback.
                    # Raw mode disables ECHO, ICANON, ONLCR, and all other
                    # line discipline processing for clean bidirectional I/O.
                    try:
                        import tty as _tty
                        _tty.setraw(_com1_slave_fd)
                    except Exception as _re:
                        logger.warning('door_dosemu: COM1 pts slave setraw '
                                       'failed: %s', _re)
                    logger.info('door_dosemu: COM1 pts slave opened fd=%d '
                                '(%s) (session %d)',
                                _com1_slave_fd, _dosemu_pts_path, gs.id)
                except OSError as _e:
                    logger.warning('door_dosemu: COM1 pts slave open failed: '
                                   '%s (session %d)', _e, gs.id)
                break
            _t.sleep(0.05)
        if _com1_slave_fd is None:
            logger.error('door_dosemu: COM1 pts symlink never appeared at %s '
                         '(session %d) — game will show no output',
                         _dosemu_pts_path, gs.id)
        else:
            door_session.com1_w = _com1_slave_fd
        door_session.pts_path = _dosemu_pts_path

    _ansi_probed_flag = [False] if game.game_type == 'door_dosemu' else None

    with _sessions_lock:
        _sessions[gs.id] = door_session

    # Capture the live Flask app for the reader thread so cleanup can
    # touch the DB without the caller's request context (the thread
    # outlives it). _get_current_object resolves the proxy to the real app.
    try:
        from flask import current_app
        reader_app = current_app._get_current_object()
    except Exception:
        reader_app = None

    # Watchdog: independent of PTY EOF, detect when the door subprocess
    # actually exits via waitpid(). Needed because xvfb-run wrappers
    # leave Xvfb running after the door exits — Xvfb inherits the PTY
    # slave_fd, which keeps master_fd from ever returning EOF. Without
    # this watcher, the BBS session would never know the door ended,
    # the wait-loop in the terminal launcher would poll forever, and
    # the user's session would appear to "hang on game exit". This
    # watcher reaps the child and forces _cleanup_session to run.
    def _waitpid_watcher():
        try:
            os.waitpid(pid, 0)
        except (ChildProcessError, OSError):
            pass
        # Also kill any leftover Xvfb / dbus-daemon that survived past
        # the door exit. They're in the door's process group (we set
        # setsid in the child), so killpg sweeps them up.
        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass
        # Run cleanup. Idempotent — _cleanup_session checks _sessions
        # before doing anything, so it's safe even if the PTY watcher
        # already ran the cleanup path.
        try:
            _cleanup_session_safe(gs.id, reader_app)
        except Exception:  # pylint: disable=broad-except
            logger.exception('waitpid watcher cleanup failed for session %d',
                             gs.id)

    waitpid_thread = threading.Thread(
        target=_waitpid_watcher, daemon=True,
        name=f'waitpid-watcher-{gs.id}')
    waitpid_thread.start()

    if dos_bridge is not None:
        # door_dos: I/O flows through the TCP nullmodem bridge, NOT through
        # the PTY's stdio. DOSBox's stdin/stdout are unused (output goes to
        # its window, which we hide via xvfb-run; stdin is ignored). The
        # bridge pumps DOSBox COM1 -> emit_fn for output, and the BBS user's
        # keystrokes route via DoorSession.write() -> bridge.write() -> TCP.
        # When DOSBox closes the TCP socket (= door exited), bridge fires
        # on_close → we trigger _cleanup_session immediately. This is more
        # reliable than waiting for PTY EOF, because xvfb-run keeps Xvfb
        # alive past the door exit and Xvfb holds slave_fd open.
        def _on_bridge_close():
            # Kill the door's process group up-front. SIGTERM gives DOSBox
            # / xvfb-run / dbus a moment to clean up; SIGKILL chases any
            # holdout 2s later. Necessary because the idle-timeout case
            # means the door is stuck — we can't just close the bridge
            # and hope DOSBox notices.
            try:
                os.killpg(pid, signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
            def _force_kill():
                import time as _t
                _t.sleep(2)
                try:
                    os.killpg(pid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
            threading.Thread(target=_force_kill, daemon=True,
                              name=f'force-kill-{gs.id}').start()
            # Run the standard cleanup path.
            try:
                _cleanup_session_safe(gs.id, reader_app)
            except Exception:  # pylint: disable=broad-except
                logger.exception('bridge close cleanup failed for session %d',
                                 gs.id)
        # Idle timeout — DOOR_IDLE_TIMEOUT env var, default 60s. After
        # that many seconds with zero bytes flowing in either direction,
        # the bridge force-closes itself, fires _on_bridge_close, which
        # SIGTERMs the door process group. Catches stuck doors (LORD's
        # exit-loop, infinite "press any key" prompts on hidden screens)
        # so the user always gets back to the BBS within the timeout.
        try:
            idle_timeout = int(os.environ.get('DOOR_IDLE_TIMEOUT', '60'))
        except ValueError:
            idle_timeout = 60
        dos_bridge.bind_emit(socketio_emit_fn,
                              on_close=_on_bridge_close,
                              idle_timeout=idle_timeout)
        # Start a small watcher thread so we still detect when DOSBox exits
        # and clean up the session — the bridge alone doesn't notice the
        # subprocess ending. The PTY reader handles this when it gets EOF
        # on master_fd, which happens when the child exits and slave_fd
        # closes.
        t = threading.Thread(
            target=_pty_reader,
            args=(gs.id, master_fd, lambda _data: None, reader_app),
            daemon=True,
            name=f'pty-watcher-{gs.id}',
        )
        t.start()
    elif game.game_type == 'door_dosemu' and _com1_slave_fd is not None:
        # door_dosemu pts path: read game output from the COM1 pts slave fd.
        # master_fd is kept alive only for TIOCSWINSZ and EOF detection.
        # Two threads: one reads COM1 (game output → browser), one watches
        # master_fd for EOF (dosemu2 exit → cleanup).
        t = threading.Thread(
            target=_pty_reader,
            args=(gs.id, _com1_slave_fd, socketio_emit_fn, reader_app),
            kwargs={'ansi_probed_ref': _ansi_probed_flag,
                    'com1_inject_fd': _com1_slave_fd},
            daemon=True,
            name=f'com1-reader-{gs.id}',
        )
        t.start()
        # Watcher: monitors master_fd for EOF (dosemu2 exit) and triggers
        # cleanup.  No emit — game output comes from com1-reader above.
        tw = threading.Thread(
            target=_pty_reader,
            args=(gs.id, master_fd, lambda _data: None, reader_app),
            daemon=True,
            name=f'pty-watcher-{gs.id}',
        )
        tw.start()
    else:
        # Standard PTY path: door's stdio is the user's terminal.
        t = threading.Thread(
            target=_pty_reader,
            args=(gs.id, master_fd, socketio_emit_fn, reader_app),
            daemon=True,
            name=f'pty-reader-{gs.id}',
        )
        t.start()

    # door_dosemu: clean up temp conf file after dosemu2 has had time to read
    # it (it's read at init, before FreeDOS boots, so 3s is more than enough).
    if _dosemu_conf_path:
        _conf_to_del = _dosemu_conf_path
        def _del_conf():
            import time as _t
            _t.sleep(3)
            try:
                os.unlink(_conf_to_del)
            except OSError:
                pass
        threading.Thread(target=_del_conf, daemon=True,
                         name=f'conf-cleanup-{gs.id}').start()

    return gs.id


def _pty_reader(session_id, read_fd, emit_fn, app=None,
                ansi_probed_ref=None, com1_inject_fd=None):
    """Background thread: read from read_fd and call emit_fn(bytes).

    For door_dosemu pts mode: read_fd is the COM1 pts slave fd (game output).
    com1_inject_fd is the same slave fd used to write COM1 RX (replies).

    ESC[6n interception: TW2002 sends this ANSI cursor-position query after
    it confirms ANSI mode.  We strip it from the output stream and reply
    ESC[24;80R on com1_inject_fd so TW2002 knows the terminal is 80x24.
    ansi_probed_ref[0] is set True when we see the probe.

    For all other game types: read_fd is master_fd (PTY), com1_inject_fd
    is None.
    """
    logger.warning('_pty_reader started (session=%d read_fd=%d com1_inject=%s)',
                   session_id, read_fd, com1_inject_fd)

    _ansi_probed = False

    while True:
        try:
            data = os.read(read_fd, 4096)
            if not data:
                break

            # ESC[6n interception for door_dosemu: TW2002 sends this to probe
            # ANSI support.  Reply with ESC[24;80R (cursor at row 24, col 80)
            # via COM1 RX so TW2002 confirms ANSI and shows the title screen.
            if com1_inject_fd is not None and not _ansi_probed and b'\x1b[6n' in data:
                _ansi_probed = True
                if ansi_probed_ref is not None:
                    ansi_probed_ref[0] = True
                try:
                    os.write(com1_inject_fd, b'\x1b[24;80R')
                    logger.info('_pty_reader: ESC[6n intercepted, replied '
                                'ESC[24;80R (session %d)', session_id)
                except OSError as _e:
                    logger.error('_pty_reader: ESC[6n reply FAILED: %s '
                                 '(session %d)', _e, session_id)
                data = data.replace(b'\x1b[6n', b'')
                if not data:
                    continue

            emit_fn(data)
        except OSError:
            break

    # Session ended
    _cleanup_session_safe(session_id, app)


def _cleanup_session_safe(session_id, app=None):
    """Run _cleanup_session() with a guaranteed valid app context.

    Real bug found live (door_mystic_mps, first real test of that door
    type): every call site here used the same `if app is not None: with
    app.app_context(): ... else: _cleanup_session(...)` pattern -- when
    the earlier `current_app._get_current_object()` capture failed (or
    simply wasn't reached for this game type's particular launch path)
    and `app` ended up None, cleanup ran with NO app context at all and
    _cleanup_session's own DB access (GameSession.query, db.session.commit)
    raised "Working outside of application context", caught by its broad
    except and merely logged -- meaning the session was silently never
    marked completed/released. Building a fresh throwaway app here
    (same _app()-style pattern used elsewhere in this codebase for
    terminal-side code with no ambient Flask context) guarantees cleanup
    always has a real context to work with, regardless of why the
    caller's own capture came back empty.
    """
    built_fresh_app = app is None
    if built_fresh_app:
        try:
            from flask import Flask
            from anetbbs.config import get_config
            app = Flask(__name__)
            app.config.from_object(get_config(os.environ.get('FLASK_ENV', 'production')))
            db.init_app(app)
        except Exception:
            logger.exception('Could not build a fallback app context for '
                             'session %d cleanup', session_id)
            _cleanup_session(session_id)
            return
    if built_fresh_app:
        from ..features.db_scope import transient_app_context
        with transient_app_context(app):
            _cleanup_session(session_id)
    else:
        with app.app_context():
            _cleanup_session(session_id)


def _cleanup_session(session_id):
    """Mark session as completed and release resources."""
    with _sessions_lock:
        door_session = _sessions.pop(session_id, None)

    if door_session:
        door_session.close()

    try:
        gs = GameSession.query.get(session_id)
        if gs and gs.status == 'active':
            gs.status = 'completed'
            gs.ended_at = datetime.utcnow()
            # Increment game play count
            gs.game.play_count = (gs.game.play_count or 0) + 1
            db.session.commit()
            release_node(gs.game_id, gs.node_number)
    except Exception:
        logger.exception('Cleanup error for session %d', session_id)


def send_input(session_id, data):
    """Forward user input to a running door game session."""
    with _sessions_lock:
        door_session = _sessions.get(session_id)
    if door_session:
        door_session.write(data)


def resize_terminal(session_id, rows, cols):
    """Resize terminal window for a running door game session."""
    with _sessions_lock:
        door_session = _sessions.get(session_id)
    if door_session:
        door_session.resize(rows, cols)


def terminate_session(session_id):
    """Forcibly terminate a door game session."""
    with _sessions_lock:
        door_session = _sessions.get(session_id)
    if door_session:
        door_session.close()
    _cleanup_session(session_id)


async def _drain_stale_session_input(session, timeout=0.05):
    """Discard whatever is ALREADY sitting in session.reader's buffer,
    without waiting for genuinely new bytes.

    Real bug found live bundling Minesweeper: every play_*() bridge below
    starts forwarding session.reader bytes to a freshly-launched door/
    remote-socket IMMEDIATELY, with nothing draining whatever was already
    queued from the user's OWN menu navigation (the Enter keypress that
    selected this game, a fast double-tap, terminal-echo artifacts,
    etc). Those stale bytes get relayed into the door before it's even
    finished starting up, and get consumed as its very first "keypress"
    the instant it's ready to read. Minesweeper's own real, intentional
    design (matching real Synchronet) treats a bare Q as an UNCONFIRMED
    instant quit before the player's first move (confirmation only kicks
    in once game.start is set), so a single stray byte that happened to
    land on 'Q' silently ended the session before the player ever saw it
    happen. Confirmed live: intermittent -- sometimes several real turns
    were possible, sometimes the door exited within a fraction of a
    second, exactly the signature of a race against however much was
    queued at launch time, not a deterministic game-logic bug.

    Not Minesweeper-specific -- any door/remote game with a consequential
    unconfirmed single-keystroke action at its own startup is equally
    exposed, so this is called from every bridge that hands session.reader
    off to something new, not patched into any one door. Drains in a
    short loop (not a single read) since a real leftover sequence (e.g.
    an arrow key's multi-byte CSI code) can be more than one byte; stops
    the moment nothing new arrives within `timeout`, matching the same
    short-timeout drain pattern BBSSession.init_session already uses for
    telnet negotiation leftovers."""
    import asyncio
    try:
        while True:
            pending = await asyncio.wait_for(session.reader.read(4096), timeout=timeout)
            if not pending:
                break
    except (asyncio.TimeoutError, Exception):
        pass


# ---------------------------------------------------------------------------
# Telnet/SSH/rlogin door game runner — bridges PTY <-> BBSSession reader/writer
# ---------------------------------------------------------------------------

async def play_door_game_telnet(game, user, session, bbs_name='ANetBBS',
                                minutes_remaining=60):
    """
    Launch a door game and bridge its PTY to a BBSSession (telnet/SSH/rlogin).

    Returns True if the game ran to completion, False if it failed to start.
    """
    import asyncio
    import os

    loop = asyncio.get_event_loop()
    # We'll buffer PTY output here and the writer task drains it
    out_queue = asyncio.Queue()

    def _emit_to_queue(data):
        # Called from the PTY reader thread — push to the asyncio queue safely
        try:
            loop.call_soon_threadsafe(out_queue.put_nowait, data)
        except Exception:
            pass

    # Need an app context for the SQLAlchemy ops in launch_door_game.
    # We're outside Flask, so push one ourselves.
    from flask import Flask
    from anetbbs.config import get_config
    from ..features.db_scope import transient_app_context
    app = Flask(__name__)
    app.config.from_object(get_config(os.environ.get('FLASK_ENV', 'production')))
    db.init_app(app)

    with transient_app_context(app):
        # Re-fetch the user/game in this app context (the caller's instances
        # may be from a different SQLAlchemy session and won't be attached here)
        from ..models import User as DbUser, Game as DbGame
        live_user = DbUser.query.get(user['id']) if isinstance(user, dict) else user
        live_game = DbGame.query.get(game.id) if hasattr(game, 'id') else None
        if live_game is None:
            await session.write("\r\nGame not found.\r\n")
            return False

        # Validate the command BEFORE launching so we can show a clear error.
        # This is a dry run purely to surface config problems early -- the
        # built command/cwd are never actually used to launch anything here
        # (the real launch below rebuilds its own via launch_door_game).
        # Any temp files this creates (Synchronet Node.js compat path) are
        # therefore immediately orphaned unless cleaned up right here --
        # previously they weren't, doubling the leak rate for every
        # Synchronet-JS game launch attempt from a terminal session.
        _validate_temp_files = []
        try:
            _build_command(live_game, 1, bbs_name, user=user,
                           temp_files_out=_validate_temp_files)
        except Exception as exc:
            await session.write(f"\r\nLaunch failed:\r\n  {exc}\r\n")
            await session.read_line("\r\nPress Enter...")
            return False
        finally:
            for _tf in _validate_temp_files:
                try:
                    os.unlink(_tf)
                except OSError:
                    pass

        sid = launch_door_game(live_game, live_user, _emit_to_queue,
                               bbs_name=bbs_name,
                               minutes_remaining=minutes_remaining)
        if sid is None:
            await session.write("\r\nCould not start the game — no free nodes "
                                "(check Game.max_nodes in /admin/games/) or "
                                "launch error (see /var/log via journalctl -u anetbbs-telnet).\r\n")
            await session.read_line("\r\nPress Enter...")
            return False

    try:
        _idle_t = int(os.environ.get('DOOR_IDLE_TIMEOUT', '60'))
    except ValueError:
        _idle_t = 60
    await session.write(
        f"\r\nLaunching {game.name}...\r\n"
        f"  - Press Ctrl+] then 'q' to abort\r\n"
        f"  - {_idle_t}s of zero activity will auto-abort the door\r\n\r\n")

    # See _drain_stale_session_input()'s own docstring -- real bug found
    # live bundling Minesweeper (intermittent instant-quit race).
    await _drain_stale_session_input(session)

    # Pump PTY output -> session writer
    async def _output_pump():
        while True:
            try:
                data = await out_queue.get()
            except asyncio.CancelledError:
                break
            if not data:
                break
            try:
                if isinstance(data, bytes):
                    # Decode as cp437 to preserve box-drawing / pipe codes
                    text = data.decode(session.encoding, errors='replace')
                else:
                    text = data
                await session.write(text)
            except Exception:
                break

    # Sentinel that the user pressed the abort sequence (Ctrl+] then 'q').
    # We wait for it across two reads to be safe — one byte is `\x1d` (the
    # Ctrl+]), the next must be 'q' or 'Q' to confirm. Anything else after
    # Ctrl+] is forwarded so the door doesn't lose a keystroke.
    abort_event = asyncio.Event()

    async def _input_pump():
        seen_escape = False
        while True:
            try:
                ch = await session.reader.read(1)
            except Exception:
                break
            if not ch:
                break
            if seen_escape:
                seen_escape = False
                if ch in (b'q', b'Q'):
                    logger.info('Door %s session %d aborted by Ctrl+]q', game.slug, sid)
                    abort_event.set()
                    break
                # Not an abort — forward the original Ctrl+] AND this byte
                # to the door so it doesn't lose data.
                send_input(sid, b'\x1d')
                send_input(sid, ch)
                continue
            if ch == b'\x1d':                       # Ctrl+] — wait for 'q'
                seen_escape = True
                continue
            send_input(sid, ch)

    out_task = asyncio.ensure_future(_output_pump())
    in_task = asyncio.ensure_future(_input_pump())

    # Wait for either: (a) the door process to exit (session removed from
    # `_sessions` by _cleanup_session via PTY EOF, bridge close, or
    # waitpid watcher), (b) the user pressed Ctrl+]q to abort, or (c) the
    # user's connection itself dropped. We poll every 1s instead of 2s for
    # snappier abort response.
    #
    # Real leak found live: (c) was missing entirely. _input_pump() already
    # correctly detects a dropped connection (session.reader.read(1) raises
    # or returns empty, breaking its own loop) -- but nothing here checked
    # for that. Without door_dos's TCP bridge (which has its own idle-
    # timeout tied to data flow), a Synchronet-JS door's Node process has
    # no way to learn the user is gone, so it just keeps running -- PTY EOF
    # (the only other exit for this loop) never fires, terminate_session()
    # in the finally block below never runs, and the door's temp compat
    # script (write_compat_script(), suffix _synchronet_compat.js) is never
    # unlinked. Confirmed via a batch of exactly this file pattern found
    # orphaned in /tmp on a real install. in_task.done() is the direct,
    # immediate signal for this -- no need to wait out an idle timer, the
    # input pump already knows the instant the connection is gone.
    try:
        while True:
            try:
                await asyncio.wait_for(abort_event.wait(), timeout=1.0)
                break  # user aborted
            except asyncio.TimeoutError:
                pass
            if in_task.done():
                break  # user's connection dropped
            with _sessions_lock:
                still_active = sid in _sessions
            if not still_active:
                break
    finally:
        for t in (out_task, in_task):
            if not t.done():
                t.cancel()
        # CRITICAL: wait for cancellation to actually finish. If we leave
        # in_task pending on `session.reader.read(1)`, its waiter slot in
        # the StreamReader collides with the post-game readline() below
        # and the reader ends up returning b'' forever — which then trips
        # the game menu's `if not choice` loop. This was the "game menu
        # loops after exiting LORD" bug.
        try:
            await asyncio.gather(out_task, in_task, return_exceptions=True)
        except Exception:
            pass
        # If user aborted, `terminate_session` will SIGTERM the door
        # subprocess and stop the bridge. If the door already exited
        # cleanly, this is a no-op (session already removed from
        # `_sessions`).
        terminate_session(sid)

    if abort_event.is_set():
        await session.write("\r\n\r\n[Door aborted by user — Ctrl+]q]\r\n")
    else:
        # Use the wrapped read_line so telnet IAC bytes left in the buffer
        # by the door get stripped and either \r or \n terminates the prompt.
        try:
            await session.read_line("\r\n\r\nGame ended. Press Enter to continue...")
        except Exception:
            pass
    return True


# ---------------------------------------------------------------------------
# Remote rlogin / telnet doors (Synchronet xtrn game servers, DoorParty,
# TWGS, etc.)
# ---------------------------------------------------------------------------

class _ExternalDoorSession:
    """DoorSession-shaped wrapper around an outbound connection object
    (:class:`rlogin_bridge.RloginConnection` or
    :class:`telnet_bridge.TelnetConnection` — both expose the same
    ``write()``/``stop()`` API as DosBridge).

    DoorSession.write expects a ``dos_bridge`` slot with a ``write()``
    method, so we can reuse the existing session-management code
    (send_input, terminate_session, _cleanup_session) without forking
    it for every remote-connection type.
    """

    def __init__(self, session_id, conn):
        self.session_id = session_id
        self.master_fd = -1   # no PTY for remote sessions
        self.pid = 0          # no subprocess for remote sessions
        self.dos_bridge = conn   # the slot is generic
        self.started_at = datetime.utcnow()

    def write(self, data):
        if isinstance(data, str):
            data = data.encode('utf-8', errors='replace')
        if self.dos_bridge is not None:
            self.dos_bridge.write(data)

    def resize(self, rows, cols):
        # Neither rlogin nor telnet carries SIGWINCH through this bridge.
        # No-op.
        pass

    def close(self):
        if self.dos_bridge is not None:
            try:
                self.dos_bridge.stop()
            except Exception:  # pylint: disable=broad-except
                logger.exception('external door stop failed')
            self.dos_bridge = None


def launch_rlogin_session(game, user, emit_fn, bbs_name='ANetBBS'):
    """Launch a remote rlogin door session for ``game``.

    Same return contract as :func:`launch_door_game` — returns the
    GameSession.id on success, or None if a node couldn't be allocated.
    Raises ``ValueError`` with a clear message on misconfiguration.

    Game configuration (in standard Game fields):

      * ``game.executable_path``  = ``HOST:PORT``
      * ``game.command_line_args`` = ``USER_TEMPLATE PASSWORD [TERMINAL]``
      * ``game.rlogin_bbs_tag``   = optional BBS tag (e.g. ``ANET``) —
        sent as ``username-TAG`` (hyphen-joined) in the client-user
        field. Kept in its own field rather than typed into
        command_line_args' USER_TEMPLATE text purely for admin-form
        clarity, not because the wire format requires it.
    """
    from .rlogin_bridge import RloginConnection, build_client_user

    server_addr = (game.executable_path or '').strip()
    if not server_addr:
        raise ValueError('door_rlogin: executable_path must be HOST:PORT '
                         '(e.g. game.a-net-online.lol:513)')
    if ':' in server_addr:
        host, port_str = server_addr.rsplit(':', 1)
        try:
            port = int(port_str)
        except ValueError:
            port = 513
    else:
        host, port = server_addr, 513

    raw_args = (game.command_line_args or '').strip()
    parts = raw_args.split(None, 2)
    if len(parts) < 2:
        raise ValueError('door_rlogin: command_line_args must be '
                         '"USER_TEMPLATE PASSWORD [TERMINAL]" '
                         '(e.g. "@USER@ mySharedPassword")')
    user_template, password = parts[0], parts[1]
    terminal = parts[2] if len(parts) > 2 else 'xterm/57600'

    # Resolve identity
    if isinstance(user, dict):
        username = (user.get('username') or 'guest').strip()
        alias = (user.get('display_name') or username).strip()
    else:
        username = (getattr(user, 'username', None) or 'guest').strip()
        alias = (getattr(user, 'display_name', None) or username).strip()
    client_user = build_client_user(
        user_template, username, alias, getattr(game, 'rlogin_bbs_tag', None))

    # Allocate node
    node = allocate_node(game.id, game.max_nodes or 1, -1)
    if node is None:
        logger.warning('No free nodes for game %s', game.slug)
        return None

    _uid = (user.get('id') if isinstance(user, dict)
            else getattr(user, 'id', None)) or 0
    gs = GameSession(game_id=game.id, user_id=_uid, node_number=node,
                     status='active')
    db.session.add(gs)
    db.session.commit()

    from .node_manager import _active, _lock as _node_lock
    with _node_lock:
        _active[(game.id, node)] = gs.id

    # Open rlogin TCP + send handshake
    conn = RloginConnection(host, port, client_user, password, terminal)
    try:
        conn.connect()
    except Exception as exc:
        logger.error('rlogin connect to %s:%d failed: %s', host, port, exc)
        gs.status = 'crashed'
        gs.ended_at = datetime.utcnow()
        db.session.commit()
        release_node(game.id, node)
        raise ValueError(f'Could not connect to {host}:{port} — {exc}') from exc

    # Wrap as a DoorSession-shaped object so send_input / terminate_session
    # / _cleanup_session all work without changes.
    door_session = _ExternalDoorSession(gs.id, conn)
    with _sessions_lock:
        _sessions[gs.id] = door_session

    try:
        from flask import current_app
        reader_app = current_app._get_current_object()
    except Exception:
        reader_app = None

    def _on_close():
        try:
            _cleanup_session_safe(gs.id, reader_app)
        except Exception:  # pylint: disable=broad-except
            logger.exception('rlogin close cleanup failed for session %d',
                             gs.id)

    try:
        idle_timeout = int(os.environ.get('DOOR_IDLE_TIMEOUT', '300'))
    except ValueError:
        idle_timeout = 300
    conn.bind_emit(emit_fn, on_close=_on_close, idle_timeout=idle_timeout)
    return gs.id


async def play_rlogin_telnet(game, user, session, bbs_name='ANetBBS',
                              minutes_remaining=60):
    """Bridge a BBS terminal session to a remote rlogin door game server.

    Synchronet, Mystic, and similar BBS software accept rlogin as a way to
    pre-authenticate a user from another BBS straight into a door menu (or
    a specific door). The handshake is a small protocol — four NUL-terminated
    strings:

        \\0
        client-user-name\\0
        server-user-name\\0    (Synchronet uses this as the password slot)
        terminal/speed\\0

    Configuration (in standard Game fields, plus one door_rlogin-only
    field):

      executable_path    = ``HOST:PORT``
                           e.g., ``game.a-net-online.lol:513``
      command_line_args  = ``USER_TEMPLATE PASSWORD [TERMINAL]``
                           e.g., ``@USER@ 8hf30n^!``
                           or with direct-to-door: ``@USER@ 8hf30n^! xtrn=LORD408``
      rlogin_bbs_tag     = optional BBS tag (e.g. ``ANET``), sent as
                           ``username-TAG`` (hyphen-joined) in the
                           client-user field. A separate field (not part
                           of command_line_args) purely for admin-form
                           clarity, not because the wire format needs it.

    Token expansion in USER_TEMPLATE:

      ``@USER@``  → BBS user's username
      ``@ALIAS@`` → BBS user's display_name (falls back to username)
      ``%u``      → same as ``@USER@`` (Synchronet-style)
      ``%U``      → same as ``@USER@`` (Mystic-style)
    """
    import asyncio

    # Parse executable_path = HOST:PORT
    server_addr = (game.executable_path or '').strip()
    if not server_addr:
        await session.write("\r\nrlogin door is misconfigured: no server address.\r\n")
        await session.read_line("Press Enter...")
        return False
    if ':' in server_addr:
        host, port_str = server_addr.rsplit(':', 1)
        try:
            port = int(port_str)
        except ValueError:
            port = 513
    else:
        host, port = server_addr, 513

    # Parse command_line_args = USER_TEMPLATE PASSWORD [TERMINAL]
    raw_args = (game.command_line_args or '').strip()
    parts = raw_args.split(None, 2)   # max 3 fields
    if len(parts) < 2:
        await session.write(
            "\r\nrlogin door is misconfigured: command_line_args needs "
            "at least USER_TEMPLATE and PASSWORD.\r\n"
            "Example: @USER@ mySharedPassword\r\n")
        await session.read_line("Press Enter...")
        return False
    user_template = parts[0]
    password = parts[1]
    terminal = parts[2] if len(parts) > 2 else 'xterm/57600'

    # Resolve BBS user identity
    if isinstance(user, dict):
        username = (user.get('username') or 'guest').strip()
        alias = (user.get('display_name') or username).strip()
    else:
        username = (getattr(user, 'username', None) or 'guest').strip()
        alias = (getattr(user, 'display_name', None) or username).strip()

    from .rlogin_bridge import build_client_user
    client_user = build_client_user(
        user_template, username, alias, getattr(game, 'rlogin_bbs_tag', None))

    await session.write(
        f"\r\nConnecting to {host}:{port}...\r\n"
        f"  - Press Ctrl+] then 'q' to abort\r\n\r\n")

    # Open TCP socket to the rlogin server
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=15)
    except (OSError, asyncio.TimeoutError) as exc:
        await session.write(f"\r\nConnection failed: {exc}\r\n")
        await session.read_line("Press Enter...")
        return False

    # Send rlogin handshake. Synchronet BBS-style ordering:
    #   \0 + password\0 + username\0 + terminal/speed\0
    # Field 1 = AUTH (password), Field 2 = BBS USERNAME to log in as.
    # See rlogin_bridge.py docstring for why this differs from RFC 1282.
    handshake = (b'\x00'
                 + password.encode('utf-8', errors='replace') + b'\x00'
                 + client_user.encode('utf-8', errors='replace') + b'\x00'
                 + terminal.encode('utf-8', errors='replace') + b'\x00')
    try:
        writer.write(handshake)
        await writer.drain()
    except Exception as exc:  # pylint: disable=broad-except
        await session.write(f"\r\nrlogin handshake failed: {exc}\r\n")
        try: writer.close()
        except Exception: pass
        await session.read_line("Press Enter...")
        return False

    # See _drain_stale_session_input()'s own docstring -- real bug found
    # live bundling Minesweeper (intermittent instant-quit race), same
    # exposure here: whatever's left in session.reader from menu
    # navigation would otherwise get relayed to the just-connected
    # remote rlogin server as its first bytes.
    await _drain_stale_session_input(session)

    abort_event = asyncio.Event()

    # Pump bytes from the rlogin socket -> the BBS user's terminal
    async def _output_pump():
        while not abort_event.is_set():
            try:
                data = await reader.read(4096)
            except Exception:
                break
            if not data:
                break
            try:
                text = data.decode(session.encoding, errors='replace') \
                       if hasattr(session, 'encoding') else \
                       data.decode('cp437', errors='replace')
                await session.write(text)
            except Exception:
                break

    # Pump BBS user's keystrokes -> the rlogin socket. Watch for Ctrl+]q
    # abort sequence so the user can always escape a stuck session.
    async def _input_pump():
        seen_escape = False
        while not abort_event.is_set():
            try:
                ch = await session.reader.read(1)
            except Exception:
                break
            if not ch:
                break
            if seen_escape:
                seen_escape = False
                if ch in (b'q', b'Q'):
                    abort_event.set()
                    break
                # Forward both bytes — false alarm
                try:
                    writer.write(b'\x1d')
                    writer.write(ch)
                    await writer.drain()
                except Exception:
                    break
                continue
            if ch == b'\x1d':
                seen_escape = True
                continue
            try:
                writer.write(ch)
                await writer.drain()
            except Exception:
                break

    out_task = asyncio.ensure_future(_output_pump())
    in_task = asyncio.ensure_future(_input_pump())

    # Wait for either pump to finish (remote disconnects, user aborts,
    # or socket errors out)
    try:
        done, pending = await asyncio.wait(
            [out_task, in_task],
            return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        # Drain cancellations — see comment in play_door_game_telnet.
        # Without this, in_task's pending session.reader.read(1) collides
        # with the post-game prompt and leaves the StreamReader at EOF.
        if pending:
            try:
                await asyncio.gather(*pending, return_exceptions=True)
            except Exception:
                pass
    except Exception:
        pass

    try:
        writer.close()
    except Exception:
        pass

    if abort_event.is_set():
        await session.write("\r\n\r\n[Session aborted by user — Ctrl+]q]\r\n")
    else:
        try:
            await session.read_line(
                "\r\n\r\nRemote disconnected. Press Enter to continue...")
        except Exception:
            pass
    return True


def launch_telnet_session(game, user, emit_fn, bbs_name='ANetBBS'):
    """Launch a remote TELNET door session for ``game`` (e.g. TWGS —
    Trade Wars Game Server — or any other telnet-only external game
    server).

    Same return contract as :func:`launch_door_game`. Unlike rlogin,
    plain telnet has no pre-authentication handshake — the user logs in
    interactively on the remote side exactly as they would connecting
    with any telnet client directly. This is a raw byte pipe with
    minimal Telnet IAC (RFC 854) option-negotiation handling so the
    remote server doesn't hang waiting for a reply it'll never get
    otherwise (see telnet_bridge.TelnetIACFilter).

    Game configuration (in standard Game fields — no schema changes):

      * ``game.executable_path``   = ``HOST:PORT`` (e.g. ``twgs.example.com:23``)
      * ``game.command_line_args`` = unused — telnet has no login fields to fill
    """
    from .telnet_bridge import TelnetConnection

    server_addr = (game.executable_path or '').strip()
    if not server_addr:
        raise ValueError('door_telnet: executable_path must be HOST:PORT '
                         '(e.g. twgs.example.com:23)')
    if ':' in server_addr:
        host, port_str = server_addr.rsplit(':', 1)
        try:
            port = int(port_str)
        except ValueError:
            port = 23
    else:
        host, port = server_addr, 23

    # Allocate node
    node = allocate_node(game.id, game.max_nodes or 1, -1)
    if node is None:
        logger.warning('No free nodes for game %s', game.slug)
        return None

    _uid = (user.get('id') if isinstance(user, dict)
            else getattr(user, 'id', None)) or 0
    gs = GameSession(game_id=game.id, user_id=_uid, node_number=node,
                     status='active')
    db.session.add(gs)
    db.session.commit()

    from .node_manager import _active, _lock as _node_lock
    with _node_lock:
        _active[(game.id, node)] = gs.id

    # Open telnet TCP — no handshake to send.
    conn = TelnetConnection(host, port)
    try:
        conn.connect()
    except Exception as exc:
        logger.error('telnet connect to %s:%d failed: %s', host, port, exc)
        gs.status = 'crashed'
        gs.ended_at = datetime.utcnow()
        db.session.commit()
        release_node(game.id, node)
        raise ValueError(f'Could not connect to {host}:{port} — {exc}') from exc

    # Wrap as a DoorSession-shaped object so send_input / terminate_session
    # / _cleanup_session all work without changes.
    door_session = _ExternalDoorSession(gs.id, conn)
    with _sessions_lock:
        _sessions[gs.id] = door_session

    try:
        from flask import current_app
        reader_app = current_app._get_current_object()
    except Exception:
        reader_app = None

    def _on_close():
        try:
            _cleanup_session_safe(gs.id, reader_app)
        except Exception:  # pylint: disable=broad-except
            logger.exception('telnet close cleanup failed for session %d',
                             gs.id)

    try:
        idle_timeout = int(os.environ.get('DOOR_IDLE_TIMEOUT', '300'))
    except ValueError:
        idle_timeout = 300
    conn.bind_emit(emit_fn, on_close=_on_close, idle_timeout=idle_timeout)
    return gs.id


async def play_telnet_terminal(game, user, session, bbs_name='ANetBBS',
                               minutes_remaining=60):
    """Bridge a BBS terminal session to a remote TELNET door game server
    (e.g. TWGS — Trade Wars Game Server). Unlike rlogin, plain telnet has
    no pre-authentication handshake — the user logs in interactively on
    the remote side exactly as they would connecting with any telnet
    client directly; it's just a raw byte pipe with minimal Telnet IAC
    (RFC 854) option-negotiation handling so the remote server doesn't
    hang waiting for a reply it'll never get otherwise.

    Configuration (in standard Game fields — no schema changes):

      executable_path    = ``HOST:PORT`` (e.g. ``twgs.example.com:23``)
      command_line_args  = unused
    """
    import asyncio
    from .telnet_bridge import TelnetIACFilter

    # Parse executable_path = HOST:PORT
    server_addr = (game.executable_path or '').strip()
    if not server_addr:
        await session.write("\r\ntelnet door is misconfigured: no server address.\r\n")
        await session.read_line("Press Enter...")
        return False
    if ':' in server_addr:
        host, port_str = server_addr.rsplit(':', 1)
        try:
            port = int(port_str)
        except ValueError:
            port = 23
    else:
        host, port = server_addr, 23

    await session.write(
        f"\r\nConnecting to {host}:{port}...\r\n"
        f"  - Press Ctrl+] then 'q' to abort\r\n\r\n")

    # Open TCP socket to the telnet server — no handshake to send.
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=15)
    except (OSError, asyncio.TimeoutError) as exc:
        await session.write(f"\r\nConnection failed: {exc}\r\n")
        await session.read_line("Press Enter...")
        return False

    # See _drain_stale_session_input()'s own docstring -- real bug found
    # live bundling Minesweeper (intermittent instant-quit race), same
    # exposure here: whatever's left in session.reader from menu
    # navigation would otherwise get relayed to the just-connected
    # remote telnet server as its first bytes.
    await _drain_stale_session_input(session)

    iac = TelnetIACFilter()
    abort_event = asyncio.Event()

    # Pump bytes from the telnet socket -> the BBS user's terminal.
    # Option-negotiation sequences are filtered out of what the user
    # sees and answered (refused) directly on the socket.
    async def _output_pump():
        while not abort_event.is_set():
            try:
                data = await reader.read(4096)
            except Exception:
                break
            if not data:
                break
            display, reply = iac.process(data)
            if reply:
                try:
                    writer.write(reply)
                    await writer.drain()
                except Exception:
                    break
            if not display:
                continue
            try:
                text = display.decode(session.encoding, errors='replace') \
                       if hasattr(session, 'encoding') else \
                       display.decode('cp437', errors='replace')
                await session.write(text)
            except Exception:
                break

    # Pump BBS user's keystrokes -> the telnet socket. Watch for Ctrl+]q
    # abort sequence so the user can always escape a stuck session.
    async def _input_pump():
        seen_escape = False
        while not abort_event.is_set():
            try:
                ch = await session.reader.read(1)
            except Exception:
                break
            if not ch:
                break
            if seen_escape:
                seen_escape = False
                if ch in (b'q', b'Q'):
                    abort_event.set()
                    break
                # Forward both bytes — false alarm
                try:
                    writer.write(b'\x1d')
                    writer.write(ch)
                    await writer.drain()
                except Exception:
                    break
                continue
            if ch == b'\x1d':
                seen_escape = True
                continue
            try:
                writer.write(ch)
                await writer.drain()
            except Exception:
                break

    out_task = asyncio.ensure_future(_output_pump())
    in_task = asyncio.ensure_future(_input_pump())

    try:
        done, pending = await asyncio.wait(
            [out_task, in_task],
            return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        if pending:
            try:
                await asyncio.gather(*pending, return_exceptions=True)
            except Exception:
                pass
    except Exception:
        pass

    try:
        writer.close()
    except Exception:
        pass

    if abort_event.is_set():
        await session.write("\r\n\r\n[Session aborted by user — Ctrl+]q]\r\n")
    else:
        try:
            await session.read_line(
                "\r\n\r\nRemote disconnected. Press Enter to continue...")
        except Exception:
            pass
    return True


# ---------------------------------------------------------------------------
# DOS doors via TCP nullmodem (works with vanilla DOSBox 0.74-3+)
# ---------------------------------------------------------------------------

async def play_dos_game_telnet(game, user, session, bbs_name='ANetBBS',
                               minutes_remaining=60):
    """Launch a DOS door game and bridge its serial-over-TCP to the BBS session.

    Architecture:
      1. Allocate a free TCP port (5000-5100)
      2. Generate DOSBox config with `serial1=nullmodem server:127.0.0.1 port:N`
      3. DosBridge listens on the port; spawn DOSBox in background
      4. DOSBox dials in, FOSSIL driver (BNU.COM) makes COM1 the gateway
      5. Pump bytes between bridge socket and BBS session reader/writer
      6. Wait for DOSBox to exit, cleanup
    """
    import asyncio
    import subprocess
    import tempfile
    from shutil import which
    from .dos_bridge import DosBridge

    # ----- 1. Locate DOSBox -----
    dosbox = (os.environ.get('DOSBOX_PATH')
              or which('dosbox-staging') or which('dosbox-x') or which('dosbox'))
    if not dosbox or not os.path.isfile(dosbox):
        await session.write("\r\nNo DOSBox installed. sudo apt install dosbox\r\n")
        await session.read_line("Press Enter...")
        return False

    # ----- 2. Resolve game paths (same conventions as _build_dos_command) -----
    exe_raw = game.executable_path or ''
    exe_path = _resolve_path(exe_raw)
    extra = (game.command_line_args or '').replace('{node}', '1').strip()

    if os.path.isdir(exe_path):
        game_dir = exe_path
        if not extra:
            await session.write("\r\nGame config error: executable_path is a "
                                "directory but command_line_args is empty.\r\n")
            await session.read_line("Press Enter...")
            return False
        parts = extra.split()
        exe_name = parts[0]
        extra = ' '.join(parts[1:])
    elif os.path.isfile(exe_path):
        # Resolve to absolute — DOSBox `mount c "..."` requires an absolute
        # path; relative paths produce a mount that points nowhere.
        game_dir = _resolve_path(game.working_directory) if game.working_directory \
                   else os.path.dirname(exe_path)
        exe_name = os.path.basename(exe_path)
    else:
        await session.write(f"\r\nexecutable_path not found: {exe_path}\r\n")
        await session.read_line("Press Enter...")
        return False

    # Final safety net — make sure game_dir is absolute regardless of which
    # branch above produced it.
    if not os.path.isabs(game_dir):
        game_dir = _resolve_path(game_dir)

    fossil_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'dos_runtime')

    # ----- 3. Write drop file (DOOR.SYS) so the door knows the user -----
    try:
        from .dropfile import write_drop_file
        write_drop_file(user, game, 1, minutes_remaining, bbs_name)
    except Exception as exc:
        logger.warning('Drop file write failed for %s: %s', game.slug, exc)

    # ----- 4. Allocate TCP port + bridge -----
    bridge = DosBridge()
    nullmodem_port = bridge.start()
    bridge.accept_async()

    # ----- 5. Build DOSBox config (works on vanilla, staging, dosbox-x) -----
    conf_lines = [
        '[sdl]',
        'fullscreen=false',
        'autolock=false',
        'output=surface',
        '',
        '[dosbox]',
        'machine=svga_s3',
        'memsize=16',
        '',
        '[serial]',
        f'serial1=nullmodem server:127.0.0.1 port:{nullmodem_port} '
            'transparent:1 rxdelay:0 txdelay:0',
        'serial2=disabled',
        '',
        '[dos]',
        'xms=true',
        'ems=true',
        '',
        '[autoexec]',
        '@echo off',
        f'mount c "{game_dir}"',
        f'mount d "{fossil_dir}"',
        'set PATH=D:\\FOSSIL;%PATH%',
        'BNU /P1',
        'c:',
        f'{exe_name} {extra}',
        'exit',
    ]
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='_dos.conf',
                                      prefix='anetbbs_', delete=False)
    tmp.write('\n'.join(conf_lines) + '\n')
    tmp.close()

    # ----- 6. Spawn DOSBox in background -----
    cmd = [dosbox, '-conf', tmp.name, '-noconsole', '-exit']
    if not os.environ.get('DISPLAY'):
        xvfb = which('xvfb-run')
        if xvfb:
            cmd = [xvfb, '-a'] + cmd
    log_path = '/tmp/anetbbs_dos_dosbox.log'  # nosec B108 -- transient debug log, safe to lose
    try:
        proc = subprocess.Popen(
            cmd, cwd=game_dir,
            stdout=open(log_path, 'a'),
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
    except Exception as exc:
        bridge.stop()
        await session.write(f"\r\nFailed to launch DOSBox: {exc}\r\n")
        await session.read_line("Press Enter...")
        return False

    await session.write(
        f"\r\nLaunching {game.name} (DOSBox PID {proc.pid}, bridge port {nullmodem_port}).\r\n"
        "Press Ctrl+] then 'q' to abort.\r\n\r\n"
    )

    # ----- 7. Pump TCP bridge <-> BBS session -----
    loop = asyncio.get_event_loop()

    async def _wait_dos_connect():
        for _ in range(120):  # up to 60s
            if bridge._dos_sock or bridge._stop_event.is_set():
                return bridge._dos_sock is not None
            await asyncio.sleep(0.5)
        return False

    if not await _wait_dos_connect():
        try: proc.terminate()
        except Exception: pass
        bridge.stop()
        await session.write("\r\nDOSBox didn't connect to bridge — see log\r\n")
        await session.read_line("Press Enter...")
        return False

    sock = bridge._dos_sock

    # See _drain_stale_session_input()'s own docstring -- real bug found
    # live bundling Minesweeper (intermittent instant-quit race). Lower
    # exposure here (DOSBox's own boot sequence naturally absorbs stray
    # keystrokes during its long startup), but cheap and consistent to
    # apply the same defense at the same point every other bridge does.
    await _drain_stale_session_input(session)

    async def _output_pump():
        # Bridge socket -> session writer
        while True:
            try:
                data = await loop.sock_recv(sock, 4096)
            except Exception:
                break
            if not data:
                break
            try:
                text = data.decode(session.encoding, errors='replace')
                await session.write(text)
            except Exception:
                break

    async def _input_pump():
        while True:
            try:
                ch = await session.reader.read(1)
            except Exception:
                break
            if not ch:
                break
            try:
                await loop.sock_sendall(sock, ch)
            except Exception:
                break

    sock.setblocking(False)
    out_task = asyncio.ensure_future(_output_pump())
    in_task = asyncio.ensure_future(_input_pump())

    # Wait for DOSBox to exit
    import time
    start_time = time.time()
    try:
        while proc.poll() is None:
            await asyncio.sleep(2)
    finally:
        for t in (out_task, in_task):
            if not t.done():
                t.cancel()
        # Drain cancellations — see comment in play_door_game_telnet.
        try:
            await asyncio.gather(out_task, in_task, return_exceptions=True)
        except Exception:
            pass
        try: proc.terminate()
        except Exception: pass
        bridge.stop()

    elapsed = time.time() - start_time
    exit_code = proc.returncode

    # If DOSBox died fast, surface the log so the user can see what went wrong
    if elapsed < 5:
        await session.write(f"\r\n\r\nDOSBox exited in {elapsed:.1f}s "
                            f"(code {exit_code}). Tail of log:\r\n")
        try:
            with open(log_path, 'r') as f:
                lines = f.readlines()[-30:]
            for line in lines:
                await session.write(line.rstrip() + "\r\n")
        except Exception as exc:
            await session.write(f"  (could not read log: {exc})\r\n")
    else:
        await session.write(f"\r\n\r\nGame ended (exit {exit_code}).")

    try:
        await session.read_line("\r\nPress Enter to continue...")
    except Exception:
        pass
    return True
