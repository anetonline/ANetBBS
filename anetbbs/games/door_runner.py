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
        # Optional TCP bridge for DOSBox sessions. Set by launch_door_game
        # right after construction for door_dos game type. None otherwise.
        self.dos_bridge = None

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
        try:
            os.write(self.master_fd, data)
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
            cwd=cwd or os.path.dirname(mps_path) or '/tmp',
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
                   token_ctx=None, bridge_port=None):
    """
    Build the command list for launching a door game.
    Returns a (cmd_list, cwd) tuple. Raises descriptive errors on
    misconfiguration so the user sees what's wrong instead of a blank fail.

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
    if game.game_type in ('door_dos', 'door_native'):
        anchor = game.executable_path or game.working_directory
    elif game.game_type in ('door_mystic', 'door_mystic_mps'):
        anchor = game.mystic_script_path or game.working_directory
    elif game.game_type == 'door_synchronet':
        anchor = (game.synchronet_exec_dir or game.synchronet_script_path
                  or game.working_directory)
    else:
        anchor = game.working_directory or '/tmp'

    # Expand tokens BEFORE path resolution so `%P` (per-node temp dir)
    # works as both the working_directory AND inside executable_path /
    # command_line_args.
    anchor = _xp(anchor)
    wd_raw = _xp(game.working_directory or '')
    cwd_raw = (wd_raw or
               (os.path.dirname(_resolve_path(anchor)) if anchor else '/tmp'))
    cwd = _resolve_path(cwd_raw) or '/tmp'

    if game.game_type == 'door_dos':
        return _build_dos_command(game, node_number, cwd, token_ctx=token_ctx,
                                   bridge_port=bridge_port)

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
        # newer than the bytecode. Mystic's runtime takes the .mpx (bytecode)
        # via `-x`, but it's normal for sysops to drop a raw .mps source
        # they got from a door pack. We compile silently so they don't have
        # to remember the build step. If mplc isn't on the host, fall back
        # to the existing path (which assumes the script is already compiled,
        # i.e., a .mpx file the sysop produced manually).
        compiled = _ensure_mps_compiled(script, cwd)
        if compiled:
            script = compiled
        return [mystic, '-x', script], cwd

    if game.game_type == 'door_synchronet':
        script_raw = game.synchronet_script_path or ''
        script = _resolve_path(script_raw)
        if not script or not os.path.isfile(script):
            raise FileNotFoundError(f'Synchronet script not found: {script!r}')

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
        import tempfile
        try:
            with open(compat_path, 'r') as f:
                compat_src = f.read()
            with open(script, 'r') as f:
                user_src = f.read()
        except OSError as exc:
            raise FileNotFoundError(f'Synchronet script read error: {exc}')

        # Strip the compat's trailing "// === Execute the actual game ===" block.
        # That block does load(process.argv[last]) which when we concat into one
        # file points at the COMBINED file — recursive eval (each re-eval resets
        # _load_cache) → "Maximum call stack size exceeded".
        marker = '// === Execute the actual game ==='
        if marker in compat_src:
            compat_src = compat_src.split(marker)[0]
        # Wrap user script in a try/catch that:
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
            "// In Synchronet's JS runtime `module` is undefined so this fires.\n"
            "// In Node, `module` exists AND `module.exports` defaults to {} (truthy),\n"
            "// so the check is false and main() silently skips. Clear it so the\n"
            "// Synchronet idiom works correctly here.\n"
            "try { module.exports = null; } catch(e) {}\n"
            "try {\n"
            + user_src + "\n"
            "} catch (e) {\n"
            "  var trace = (e && e.stack) ? e.stack : String(e);\n"
            "  var msg = '\\r\\n\\r\\n--- Door script error ---\\r\\n'\n"
            "          + trace\n"
            "          + '\\r\\n\\r\\nPress any key to return to BBS...';\n"
            "  process.stdout.write(msg);\n"
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

        # Per-door runtime state: doors that persist state (TW2's json-client,
        # etc.) must NOT write into the source tree — the source dir is owned
        # by the install-account (stingray) and the runtime user (anetbbs uid
        # 998) can't create files there. Route writes to
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
            '  # OR set game type to door_dosemu if you have dosemu2 (apt install dosemu2)',
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
        "c:\n"
        f"{exe_name} {extra}\n"
        "exit\n"
    )

    headless = not os.environ.get('DISPLAY')

    if dosbox in (dosbox_staging, dosbox_x):
        # output=surface is required for headless operation. Without an explicit
        # output directive, dosbox-x on a headless server (SDL_VIDEODRIVER=dummy,
        # no DISPLAY) may fail SDL init silently and exit before the autoexec
        # runs — which is why DOSBox never connects to the nullmodem bridge.
        # output=surface works with SDL_VIDEODRIVER=dummy on both dosbox-x and
        # dosbox-staging and adds no overhead since we hide the window anyway.
        sdl_output = 'output=surface\n' if headless else ''
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
    """dosemu / dosemu2 invocation for BBS doors.

    Why dosemu instead of DOSBox? dosemu2 has native ``-dumb`` stdio mode —
    DOS app I/O streams directly through stdin/stdout, no FOSSIL juggling
    over a TCP nullmodem. That's a better fit for telnet/SSH doors.

    Drive layout (matches the DOSBox path so docs are consistent):

      ``D:`` = the FOSSIL bundle (``anetbbs/games/dos_runtime/`` — read-only)
      ``E:`` = the game dir (``Game.working_directory`` or dir of the .exe)
      ``F:`` = the per-node scratch dir (``<install>/data/temp/nodeN/``)

    LORDCFG / TWCFG etc. should be configured with the dropfile path =
    ``F:\\``. The autoexec loads BNU on COM1 just like DOSBox so doors
    that demand FOSSIL still work, even though dosemu2's stdio is what's
    actually under the hood.
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
        game_dir = wd_raw or os.path.dirname(exe_path)
        exe_name = os.path.basename(exe_path)
    else:
        raise FileNotFoundError(f'executable_path not found: {exe_path!r}')

    # FOSSIL bundle (BNU.COM) — same dir we use for DOSBox.
    fossil_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'dos_runtime')

    # Per-node scratch — drop file + per-session writes go here so
    # multi-node games don't collide.
    try:
        from .node_paths import node_dir as _node_dir
        per_node_host = _node_dir(node_number).rstrip(os.sep) or None
    except Exception:
        per_node_host = None

    # Build a tiny autoexec.bat that:
    #   1. Switches to E: (game dir)
    #   2. Adds D:\FOSSIL to PATH and loads BNU on COM1
    #   3. Runs the game
    cmd_str = (exe_name + (' ' + extra if extra else '')).strip()
    bat_path = os.path.join(game_dir, '_anet.bat')
    bat_body = (
        '@echo off\r\n'
        'PATH=D:\\FOSSIL;%PATH%\r\n'
        'BNU /P1\r\n'
        'E:\r\n'
        'CD \\\r\n'
        + cmd_str + '\r\n'
        'EXIT\r\n'
    )
    try:
        with open(bat_path, 'w', newline='') as f:
            f.write(bat_body)
    except OSError as exc:
        raise OSError('Cannot write {}: {}'.format(bat_path, exc))

    # dosemu2 -d flags assign drives in order, starting at C: AFTER the
    # FreeDOS boot drives. In practice with FDPP the user-mounted drives
    # land at D, E, F... — but the actual letter depends on dosemu's
    # config. To make the autoexec robust we use `-K` to set the boot
    # drive, then -I to alias drives.
    cmd = [
        dosemu_path,
        '-dumb',                                     # terminal stdio
        '-K', fossil_dir,                            # D: = FOSSIL
        '-d', game_dir,                              # E: = game
    ]
    if per_node_host:
        cmd.extend(['-d', per_node_host])            # F: = per-node temp
    cmd.extend(['-E', 'E:\\_ANET.BAT'])              # run the autoexec
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
    # command — DOSBox's config needs the port written into it. Other game
    # types use direct PTY stdio so no bridge.
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
    try:
        cmd, cwd = _build_command(game, node, bbs_name, user=user,
                                   token_ctx=token_ctx,
                                   bridge_port=bridge_port)
        logger.info('Launching game %s (type=%s, node=%d): %s',
                    game.slug, game.game_type, node,
                    ' '.join(str(c) for c in cmd))
    except Exception as exc:  # pylint: disable=broad-except
        logger.error('Failed to build command for game %s: %s', game.slug, exc)
        if dos_bridge:
            try: dos_bridge.stop()
            except Exception: pass
        gs.status = 'crashed'
        gs.ended_at = datetime.utcnow()
        db.session.commit()
        release_node(game.id, node)
        return None

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
        gs.status = 'crashed'
        gs.ended_at = datetime.utcnow()
        db.session.commit()
        release_node(game.id, node)
        return None

    if pid == 0:
        # Child process
        os.close(master_fd)
        os.setsid()
        fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
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
        # For DOS doors: suppress SDL display/audio probes so dosbox-x starts
        # cleanly on headless servers and in containers without X11 or audio hw.
        if game.game_type == 'door_dos' and not os.environ.get('DISPLAY'):
            os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
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
        os.execvp(cmd[0], cmd)
        os._exit(1)

    # Parent
    os.close(slave_fd)
    gs.pid = pid
    db.session.commit()

    door_session = DoorSession(gs.id, master_fd, pid)
    door_session.dos_bridge = dos_bridge   # None for non-door_dos sessions
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
            if reader_app is not None:
                with reader_app.app_context():
                    _cleanup_session(gs.id)
            else:
                _cleanup_session(gs.id)
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
                if reader_app is not None:
                    with reader_app.app_context():
                        _cleanup_session(gs.id)
                else:
                    _cleanup_session(gs.id)
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
    else:
        # Standard PTY path: door's stdio is the user's terminal.
        t = threading.Thread(
            target=_pty_reader,
            args=(gs.id, master_fd, socketio_emit_fn, reader_app),
            daemon=True,
            name=f'pty-reader-{gs.id}',
        )
        t.start()

    return gs.id


def _pty_reader(session_id, master_fd, emit_fn, app=None):
    """Background thread: read PTY output and call emit_fn(bytes)."""
    while True:
        try:
            data = os.read(master_fd, 4096)
            if not data:
                break
            emit_fn(data)
        except OSError:
            break

    # Session ended
    if app is not None:
        with app.app_context():
            _cleanup_session(session_id)
    else:
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
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning('Cleanup error for session %d: %s', session_id, exc)


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
    app = Flask(__name__)
    app.config.from_object(get_config(os.environ.get('FLASK_ENV', 'production')))
    db.init_app(app)

    with app.app_context():
        # Re-fetch the user/game in this app context (the caller's instances
        # may be from a different SQLAlchemy session and won't be attached here)
        from ..models import User as DbUser, Game as DbGame
        live_user = DbUser.query.get(user['id']) if isinstance(user, dict) else user
        live_game = DbGame.query.get(game.id) if hasattr(game, 'id') else None
        if live_game is None:
            await session.write("\r\nGame not found.\r\n")
            return False

        # Validate the command BEFORE launching so we can show a clear error.
        try:
            cmd, cwd = _build_command(live_game, 1, bbs_name, user=user)
            await session.write(
                f"\r\nLaunching {game.name}\r\n"
                f"  cmd: {' '.join(cmd)}\r\n"
                f"  cwd: {cwd}\r\n\r\n"
            )
        except Exception as exc:
            await session.write(f"\r\nLaunch failed:\r\n  {exc}\r\n")
            await session.read_line("\r\nPress Enter...")
            return False

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
    # waitpid watcher), or (b) the user pressed Ctrl+]q to abort. We poll
    # every 1s instead of 2s for snappier abort response.
    try:
        while True:
            try:
                await asyncio.wait_for(abort_event.wait(), timeout=1.0)
                break  # user aborted
            except asyncio.TimeoutError:
                pass
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
# Remote rlogin doors (Synchronet xtrn game servers, DoorParty, etc.)
# ---------------------------------------------------------------------------

class _RloginDoorSession:
    """DoorSession-shaped wrapper around an :class:`RloginConnection`.

    DoorSession.write expects a ``dos_bridge`` slot with a ``write()``
    method; RloginConnection has the same write/stop API as DosBridge,
    so we can reuse the existing session-management code (send_input,
    terminate_session, _cleanup_session) without forking it.
    """

    def __init__(self, session_id, rlogin_conn):
        self.session_id = session_id
        self.master_fd = -1   # no PTY for rlogin sessions
        self.pid = 0          # no subprocess for rlogin sessions
        self.dos_bridge = rlogin_conn   # the slot is generic
        self.started_at = datetime.utcnow()

    def write(self, data):
        if isinstance(data, str):
            data = data.encode('utf-8', errors='replace')
        if self.dos_bridge is not None:
            self.dos_bridge.write(data)

    def resize(self, rows, cols):
        # rlogin doesn't carry SIGWINCH; the remote BBS uses whatever
        # was in the terminal/speed handshake field. No-op.
        pass

    def close(self):
        if self.dos_bridge is not None:
            try:
                self.dos_bridge.stop()
            except Exception:  # pylint: disable=broad-except
                logger.exception('rlogin stop failed')
            self.dos_bridge = None


def launch_rlogin_session(game, user, emit_fn, bbs_name='ANetBBS'):
    """Launch a remote rlogin door session for ``game``.

    Same return contract as :func:`launch_door_game` — returns the
    GameSession.id on success, or None if a node couldn't be allocated.
    Raises ``ValueError`` with a clear message on misconfiguration.

    Game configuration (in standard Game fields):

      * ``game.executable_path``  = ``HOST:PORT``
      * ``game.command_line_args`` = ``USER_TEMPLATE PASSWORD [TERMINAL]``
    """
    from .rlogin_bridge import RloginConnection, expand_user_template

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
                         '(e.g. "@USER@-ANET mySharedPassword")')
    user_template, password = parts[0], parts[1]
    terminal = parts[2] if len(parts) > 2 else 'xterm/57600'

    # Resolve identity
    if isinstance(user, dict):
        username = (user.get('username') or 'guest').strip()
        alias = (user.get('display_name') or username).strip()
    else:
        username = (getattr(user, 'username', None) or 'guest').strip()
        alias = (getattr(user, 'display_name', None) or username).strip()
    client_user = expand_user_template(user_template, username, alias)

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
    door_session = _RloginDoorSession(gs.id, conn)
    with _sessions_lock:
        _sessions[gs.id] = door_session

    try:
        from flask import current_app
        reader_app = current_app._get_current_object()
    except Exception:
        reader_app = None

    def _on_close():
        try:
            if reader_app is not None:
                with reader_app.app_context():
                    _cleanup_session(gs.id)
            else:
                _cleanup_session(gs.id)
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

    Configuration (all in standard Game fields — no schema changes):

      executable_path    = ``HOST:PORT``
                           e.g., ``game.a-net-online.lol:513``
      command_line_args  = ``USER_TEMPLATE PASSWORD [TERMINAL]``
                           e.g., ``@USER@-ANET 8hf30n^!``
                           or with direct-to-door: ``@USER@-ANET 8hf30n^! xtrn=LORD408``

    Token expansion in USER_TEMPLATE:

      ``@USER@``  → BBS user's username
      ``@ALIAS@`` → BBS user's display_name (falls back to username)
      ``%u``      → same as ``@USER@`` (Synchronet-style)
      ``%U``      → same as ``@USER@`` (Mystic-style)

    Sysop tag suffixes (e.g., ``-ANET``) are conventional — the remote
    Synchronet uses them to namespace users by source BBS.
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
            "Example: @USER@-BBS mySharedPassword\r\n")
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

    client_user = (user_template
                   .replace('@USER@', username)
                   .replace('@ALIAS@', alias)
                   .replace('%U', username)
                   .replace('%u', username))

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
    log_path = '/tmp/anetbbs_dos_dosbox.log'
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
