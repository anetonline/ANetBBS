# anetbbs/games/synchronet_compat.py
"""
Synchronet JS Compatibility Layer for ANetBBS

Generates a Node.js wrapper script that provides Synchronet's JS API objects
so that Synchronet .js door games can run on ANetBBS via PTY + xterm.js.
"""
import os
import tempfile

# Template for the compatibility wrapper script injected before the game JS.
_COMPAT_TEMPLATE = r"""
// synchronet_compat.js — Synchronet JS API compatibility wrapper for ANetBBS
// Auto-generated — do not edit.

// IMPORTANT: `function require()` later in this file shadows Node's builtin
// `require` due to JS function hoisting. So we MUST grab Node's original via
// `module.require` (which is per-module and always points to Node's actual
// require function regardless of any local `require` declaration).
var _node_require = module.require.bind(module);
var _fs = _node_require('fs');
var _path = _node_require('path');
var _readline = _node_require('readline');
var _vm = _node_require('vm');

// PTY slave starts in canonical / line-discipline mode by default. That breaks
// Synchronet doors: pressing "6\r" buffers until Enter; readSync(1) returns
// "6" the first time, then the leftover "\r" the next loop iteration -> the
// menu's `if (k === '6')` doesn't match -> "Invalid choice" between every
// keystroke.
//
// Use POSIX `stty -icanon -echo min 1 time 0` instead of process.stdin
// .setRawMode(true). setRawMode also makes stdin NON-BLOCKING on Linux,
// so readSync returns 0 bytes immediately and the menu loop spins. stty
// with min=1 time=0 keeps blocking semantics (read blocks until 1 byte).
// (Diagnostic dlog removed — was used to debug DSR sixel image rendering
// which never worked through the gunicorn-spawned PTY chain. DSR's
// navigation+lightbar still work; image-view path is unsupported.)

try {
    var _cp_init = _node_require('child_process');
    // -icanon -echo  : raw mode (no line buffering, no local echo)
    // -icrnl         : DON'T translate CR→NL so Enter delivers \r as the
    //                  lightbar menu's `key === '\r'` select-test expects
    // -inlcr -igncr  : leave NL alone, don't drop CR
    // -ixon          : Ctrl-S / Ctrl-Q go through as data, not flow ctrl
    // min 1 time 0   : blocking read returns when >=1 byte arrives;
    //                  read(N) returns up to N bytes that are already
    //                  buffered, so arrow CSI sequences (ESC [ A) come
    //                  back in one read instead of three.
    _cp_init.execSync('stty -icanon -echo -icrnl -inlcr -igncr -ixon min 1 time 0 < /dev/tty', {stdio: 'ignore'});
} catch (e) {
    process.stderr.write('[BBS] stty (set raw mode) failed: ' +
                         (e && e.message ? e.message : e) + '\n');
}

// === Standard I/O helpers ===
// Doors emit CP437 bytes (box-drawing, shading, accented glyphs). Real
// Synchronet writes those bytes verbatim to the wire; the connecting
// terminal (SyncTERM, NetRunner, the web bridge's CP437→UTF-8 decoder)
// expects to see raw bytes. Node's default `process.stdout.write(str)`
// re-encodes the string as UTF-8, doubling up high bytes and producing
// mojibake (0xC9 → "É" → C3 89 etc.).
//
// Monkey-patch stdout.write so EVERY string written is treated as latin1
// (each char = one byte 0-255) and emitted as raw bytes. This catches all
// call sites — console.print, console.write, putmsg, gotoxy, the various
// ANSI escapes, plus anything in load()'d helpers that writes directly.
(function _patchStdoutForCP437() {
    var _origWrite = process.stdout.write.bind(process.stdout);
    process.stdout.write = function(chunk, encoding, cb) {
        if (typeof chunk === 'string') {
            // Translate Synchronet Ctrl-A colour codes here so callers that
            // bypass console.* (dd_lightbar_menu, printfile, raw load()'d
            // helpers) still get colours instead of leaking ␁W glyphs.
            if (chunk.indexOf('\x01') >= 0 && typeof _translateCtrlA === 'function') {
                chunk = _translateCtrlA(chunk);
            }
            // CP437 high bytes → binary buffer (each char = 1 byte 0-255).
            for (var i = 0; i < chunk.length; i++) {
                if (chunk.charCodeAt(i) > 127) {
                    return _origWrite(Buffer.from(chunk, 'binary'),
                                      typeof encoding === 'function' ? encoding : cb);
                }
            }
        } else if (Buffer.isBuffer(chunk)) {
            // bbs.menu reads files as 'binary' (string) but other helpers
            // may pass Buffer directly. Translate Ctrl-A in raw bytes too.
            if (chunk.indexOf(0x01) >= 0 && typeof _translateCtrlA === 'function') {
                var s = chunk.toString('binary');
                s = _translateCtrlA(s);
                return _origWrite(Buffer.from(s, 'binary'),
                                  typeof encoding === 'function' ? encoding : cb);
            }
        }
        return _origWrite(chunk, encoding, cb);
    };
})();

function _emit(s) {
    if (s === undefined || s === null) return;
    process.stdout.write(Buffer.from(String(s), 'binary'));
}
function _readKey(timeoutMs) {
    // Return ONE byte per call. mouse_getkey loops calling getkey/inkey
    // and reassembles ANSI CSI sequences from per-byte reads.
    //
    // If timeoutMs > 0, switch the TTY to non-blocking with that timeout
    // (rounded up to the nearest 100ms) and return '' on timeout. inkey
    // depends on this — without it, mouse_getkey hangs after ESC waiting
    // for the next byte that may never arrive (bare ESC press).
    try {
        var buf = Buffer.alloc(1);
        if (timeoutMs && timeoutMs > 0) {
            var cp = _node_require('child_process');
            var ds = Math.max(1, Math.round(timeoutMs / 100));
            try { cp.execSync('stty min 0 time ' + ds + ' < /dev/tty', {stdio:'ignore'}); }
            catch(_){}
            var n = 0;
            try { n = _fs.readSync(0, buf, 0, 1); } catch(_) { n = 0; }
            try { cp.execSync('stty min 1 time 0 < /dev/tty', {stdio:'ignore'}); }
            catch(_){}
            if (n <= 0) return '';
            return buf.toString('binary');
        }
        _fs.readSync(0, buf, 0, 1);
        return buf.toString('binary');
    } catch(e) { return ''; }
}

function _readLine(maxlen) {
    var buf = '';
    while (buf.length < (maxlen || 80)) {
        try {
            var b = Buffer.alloc(1);
            _fs.readSync(0, b, 0, 1);
            var ch = b.toString('utf8');
            if (ch === '\r' || ch === '\n') {
                process.stdout.write('\r\n');
                break;
            } else if (ch === '\x08' || ch === '\x7f') {
                if (buf.length > 0) { buf = buf.slice(0,-1); process.stdout.write('\x08 \x08'); }
            } else {
                buf += ch;
                process.stdout.write(ch);
            }
        } catch(e) { break; }
    }
    return buf;
}

// === Synchronet Ctrl-A colour codes -> ANSI translator ===
// Synchronet doors print text containing \x01-prefixed codes (e.g. \x01y for
// bright yellow, \x01n for normal). The real Synchronet console object
// translates these on the way out. Without this, terminals show them as
// literal `␁y` glyphs and the door looks like it's printing garbage.
var _CTRLA_MAP = {
    'N':'\x1b[0m','H':'\x1b[1m','I':'\x1b[5m',
    'K':'\x1b[30m','R':'\x1b[31m','G':'\x1b[32m','Y':'\x1b[33m',
    'B':'\x1b[34m','M':'\x1b[35m','C':'\x1b[36m','W':'\x1b[37m',
    '0':'\x1b[40m','1':'\x1b[44m','2':'\x1b[42m','3':'\x1b[46m',
    '4':'\x1b[41m','5':'\x1b[45m','6':'\x1b[43m','7':'\x1b[47m',
    'L':'\x1b[2J\x1b[H','>':'\x1b[K','<':'\b \b',
    '[':'\r',']':'\n','P':'',  // pause -- not interactively waited; just suppress
};
function _translateCtrlA(s) {
    s = String(s == null ? '' : s);
    if (s.indexOf('\x01') < 0) return s;
    return s.replace(/\x01(.)/g, function(_, c) {
        var u = c.toUpperCase();
        return _CTRLA_MAP.hasOwnProperty(u) ? _CTRLA_MAP[u] : '';
    });
}

// === Synchronet @CODE@ substitution ===
// A useful subset; pulls live values from BBS_* env vars exported by
// door_runner.py before launch. Unknown codes are left visible so the
// sysop can spot them and ask for support.
function _atcode_value(name) {
    var env = process.env || {};
    var now = new Date();
    function pad(n) { return n < 10 ? '0' + n : '' + n; }
    var t = pad(now.getHours()) + ':' + pad(now.getMinutes());
    var d = now.getFullYear() + '-' + pad(now.getMonth() + 1)
            + '-' + pad(now.getDate());
    switch (name) {
        case 'BBS':       return env.BBS_NAME || '';
        case 'USER':
        case 'ALIAS':
        case 'HANDLE':    return env.BBS_USERNAME || '';
        case 'NAME':
        case 'REAL':      return env.BBS_REAL_NAME || env.BBS_USERNAME || '';
        case 'FIRST':     return (env.BBS_REAL_NAME || env.BBS_USERNAME || '')
                                  .split(/\s+/)[0] || '';
        case 'EMAIL':     return env.BBS_EMAIL || '';
        case 'SYSOP':     return env.BBS_SYSOP_NAME || env.SYSOP_NAME || '';
        case 'NODE':      return env.BBS_NODE_NUMBER || '1';
        case 'SECURITY':  return env.BBS_SECURITY || '50';
        case 'CALLS':     return env.BBS_LOGIN_COUNT || '0';
        case 'TIME':      return t;
        case 'DATE':      return d;
        case 'TIMELEFT':  return env.BBS_TIME_LEFT || '';
        default:          return null;   // null => leave token in place
    }
}
function _expand_atcodes_buf(buf) {
    // Scan a Buffer byte-by-byte for @TOKEN@ sequences (uppercase ASCII
    // 0x41-0x5A and digits/underscore between two @s). Expand into a
    // new Buffer. Token max length: 16 chars.
    var out = [];
    var i = 0;
    var len = buf.length;
    while (i < len) {
        if (buf[i] === 0x40 /* @ */) {
            var j = i + 1;
            var max = Math.min(len, i + 1 + 16);
            // Find closing @
            while (j < max && buf[j] !== 0x40) {
                var c = buf[j];
                var ok = (c >= 0x41 && c <= 0x5A)    // A-Z
                      || (c >= 0x30 && c <= 0x39)    // 0-9
                      || c === 0x5F;                 // _
                if (!ok) break;
                j++;
            }
            if (j > i + 1 && j < len && buf[j] === 0x40) {
                var token = buf.slice(i + 1, j).toString('ascii');
                var val = _atcode_value(token);
                if (val !== null) {
                    out.push(Buffer.from(val, 'binary'));
                    i = j + 1;
                    continue;
                }
            }
        }
        out.push(buf.slice(i, i + 1));
        i++;
    }
    return Buffer.concat(out.map(function (x) {
        return Buffer.isBuffer(x) ? x : Buffer.from(x, 'binary');
    }));
}

// === console object ===
var console = {
    print:      function(str) { process.stdout.write(_translateCtrlA(str)); },
    writeln:    function(str) { process.stdout.write(_translateCtrlA(str === undefined ? '' : str) + '\r\n'); },
    write:      function(str) { process.stdout.write(_translateCtrlA(str)); },
    putmsg:     function(str, mode) { process.stdout.write(_translateCtrlA(str)); },
    center:     function(str) {
        var s = String(str);
        var pad = Math.max(0, Math.floor((80 - s.length) / 2));
        process.stdout.write(' '.repeat(pad) + s + '\r\n');
    },
    clear:      function(attr) { process.stdout.write('\033[2J\033[H'); },
    cleartoeol: function() { process.stdout.write('\033[K'); },
    gotoxy:     function(x,y) { process.stdout.write('\033[' + y + ';' + x + 'H'); },
    home:       function() { process.stdout.write('\033[H'); },
    getkey:     function(mode, timeout) {
        var k = _readKey(timeout || 0);
        if (k !== '\x1b') return k;
        var b = _readKey(50);
        if (!b) return k;       // bare ESC
        // Synchronet maps terminal CSI / SS3 sequences to single-byte
        // control codes (Ctrl-^=Up, Ctrl-J=Down, etc.) — see key_defs.js.
        // dd_lightbar compares lastUserInput against those single-byte
        // KEY_* constants, not the raw \x1b[A wire bytes. Translate here
        // so KEY_UP / KEY_DOWN / KEY_HOME etc. all match.
        var prefix = k + b;
        var c = (b === '[' || b === 'O') ? _readKey(50) : '';
        if (b === 'O' || b === '[') {
            if (c === 'A') return '\x1e';   // KEY_UP
            if (c === 'B') return '\x0a';   // KEY_DOWN
            if (c === 'C') return '\x06';   // KEY_RIGHT
            if (c === 'D') return '\x1d';   // KEY_LEFT
            if (c === 'H') return '\x02';   // KEY_HOME
            if (c === 'F') return '\x05';   // KEY_END
            // Tilde sequences for Home/End/PgUp/PgDn/Ins/Del
            if (c >= '0' && c <= '9') {
                var seq = c;
                while (seq.length < 6) {
                    var d = _readKey(50);
                    if (!d) break;
                    seq += d;
                    if (d === '~' || (d >= 'A' && d <= 'Z')) break;
                }
                if (seq === '1~' || seq === '7~') return '\x02';  // HOME
                if (seq === '2~') return '\x16';                  // INSERT
                if (seq === '3~') return '\x7f';                  // DEL
                if (seq === '4~' || seq === '8~') return '\x05';  // END
                if (seq === '5~') return '\x10';                  // PGUP
                if (seq === '6~') return '\x0e';                  // PGDN
                return prefix + seq;   // unrecognised — pass through
            }
            return prefix + (c || '');
        }
        return prefix;
    },
    // Synchronet's signature: console.getstr(maxlen, mode) — first arg is the
    // max length (a number), NOT a prompt. Old version wrote arg1 to stdout
    // which crashed when callers passed a number (Bot Wars: console.getstr(2)).
    getstr:     function(maxlen, mode) {
        if (typeof maxlen !== 'number') maxlen = 80;
        return _readLine(maxlen);
    },
    yesno:      function(prompt) {
        process.stdout.write(String(prompt) + ' (Y/N)? ');
        var k = _readKey(0).toUpperCase();
        process.stdout.write(k + '\r\n');
        return k === 'Y';
    },
    // Synchronet semantics: noyes() returns TRUE for "No" (the default),
    // FALSE for "Yes". DSR's init() does `if (console.noyes("Run anyway"))
    // exit();` — a user who answers Y (yes, run) should NOT exit. The
    // previous return-on-Y was inverted and made answering Y abort DSR.
    noyes:      function(prompt) {
        process.stdout.write(String(prompt) + ' (N/Y)? ');
        var k = _readKey(0).toUpperCase();
        process.stdout.write(k + '\r\n');
        return k !== 'Y';
    },
    // Synchronet `console.term_supports(flag)` — true iff the user's
    // terminal claims to support `flag` (ANSI, COLOR, ICE, RIP, etc.).
    // dd_lightbar_menu.js calls this for ANSI detection; assume modern
    // terminals support everything.
    term_supports: function(flag) { return true; },
    // Synchronet records SyncTERM's cterm version via the CTERM detection
    // sequence. We can't ask the wire, so claim a recent SyncTERM (>=1189)
    // — that suppresses DSR's "old terminal" warning for SyncTERM users.
    cterm_version: 1190,
    inkey:      function(mode, timeout) { return _readKey(timeout || 0); },
    // strlen — count visible characters (strip ANSI escape sequences)
    strlen:     function(str) {
        var s = String(str || '');
        // Strip CSI sequences (\x1b[...m, \x1b[...H, etc) and bare ESC sequences
        s = s.replace(/\x1b\[[0-9;]*[A-Za-z]/g, '').replace(/\x1b./g, '');
        return s.length;
    },
    // printfile — read a file, expand Synchronet @CODE@ tokens, print it.
    // Read as Buffer (NOT utf8) so CP437 ANSI art with high-bit bytes
    // (0x80-0xFF) passes through unmangled. We expand @-codes byte-wise:
    // @ is 0x40 ASCII so it's safe to scan even in CP437 content, and
    // tokens like @BBS@ are pure ASCII.
    printfile: function(path, mode) {
        try {
            var data = _fs.readFileSync(path);
            var expanded = _expand_atcodes_buf(data);
            process.stdout.write(expanded);
        } catch (e) {
            process.stdout.write('\r\n[printfile: ' + path + ' not found]\r\n');
        }
    },
    // atcode(name) — Synchronet API. Returns the substitution for a
    // single @CODE@ token (no surrounding @s in the arg).
    atcode: function(name) {
        return _atcode_value(String(name || '').toUpperCase());
    },
    // mnemonics — Synchronet's "(~Y)es / (~N)o" hot-letter renderer
    mnemonics: function(str) {
        var s = String(str || '').replace(/~(.)/g, '\x1b[1;33m$1\x1b[0m');
        process.stdout.write(s);
    },
    // crlf
    crlf: function() { process.stdout.write('\r\n'); },
    // ungetstr — push input back (we just write to stdout for visual feedback)
    ungetstr: function(str) { /* no-op */ },
    // line_count — accept what's set, ignore
    aborted: false,
    line_count: 0,
    abortable: true,
    // (cterm_version is set above to 1190 — this duplicate key was making
    // it silently revert to 0, causing DSR's SIXEL warning to fire even
    // after the bump. Object literal last-key-wins. Removed.)
    sound: false,
    handle_ctrlkey: function(key, mode) { return false; },
    backspace: function() { process.stdout.write('\b \b'); },
    pushxy: function() { process.stdout.write('\x1b[s'); },
    popxy: function() { process.stdout.write('\x1b[u'); },
    cursor_up: function(n) { process.stdout.write('\x1b[' + (n||1) + 'A'); },
    cursor_down: function(n) { process.stdout.write('\x1b[' + (n||1) + 'B'); },
    cursor_right: function(n) { process.stdout.write('\x1b[' + (n||1) + 'C'); },
    cursor_left: function(n) { process.stdout.write('\x1b[' + (n||1) + 'D'); },
    // 80x25 — the DOS terminal contract every BBS door is written for.
    // LORD's gotoxy commands target rows 0–24; doors that fit "the
    // bottom line for status" assume 25, not 24.
    screen_columns: 80,
    screen_rows: 25,
    autoterm: 0x1E,        // ANSI + COLOR + RIP + PETSCII bits — claim everything
    attributes: 7,
    line_counter: 0,
    // Synchronet directional aliases (sbbs_console.js calls these names,
    // not cursor_*). Right/left/up/down with optional N — default 1.
    right: function (n) { process.stdout.write('\x1b[' + (n || 1) + 'C'); },
    left:  function (n) { process.stdout.write('\x1b[' + (n || 1) + 'D'); },
    up:    function (n) { process.stdout.write('\x1b[' + (n || 1) + 'A'); },
    down:  function (n) { process.stdout.write('\x1b[' + (n || 1) + 'B'); },
    // ctrlkey_passthru — bitmask of Ctrl-keys NOT to intercept. Doors set
    // it to 0x7fffffff (let everything through). We just remember the value.
    ctrlkey_passthru: 0,
    getlines_remaining: 24,
    question: '',
    pause: function() { this.writeln('\r\n[Press any key to continue]'); this.getkey(); },
    ansi: function(attr) {
        var fg = attr & 0x0F;
        var bg = (attr >> 4) & 0x07;
        var bright = (attr & 0x08) ? 1 : 0;
        var fgMap = [30,34,32,36,31,35,33,37];
        var bgMap = [40,44,42,46,41,45,43,47];
        return '\033[' + bright + ';' + fgMap[fg & 7] + ';' + bgMap[bg] + 'm';
    },
    // attr(n) — set current colour attribute. Synchronet doors call this
    // frequently (e.g. console.attr(LIGHTGRAY)). Emit the equivalent ANSI.
    attr: function(n) {
        var ansi = this.ansi(typeof n === 'number' ? n : 7);
        process.stdout.write(ansi);
        this.attributes = (typeof n === 'number') ? n : 7;
    },
    // (strlen, mnemonics, printfile defined earlier — DO NOT redeclare here:
    //  duplicate keys in an object literal silently shadow the originals.)
    list: function(items) {
        (items || []).forEach(function(item, i) {
            process.stdout.write((i+1) + '. ' + String(item) + '\r\n');
        });
    },
    uselect: function(prompt, items) {
        this.list(items);
        process.stdout.write(String(prompt || 'Select: '));
        var s = _readLine(4);
        return parseInt(s, 10) - 1;
    },
};

// === user object ===
// Beefed up to cover what sbbs_console.js reaches for: security.password,
// stats.bytes_uploaded/downloaded, laston_date, etc. ALL Synchronet users
// have those — doors expect to read them at startup. Real values would
// come from the running BBS, here we ship reasonable static values.
var _logon_unix = Math.floor(Date.now() / 1000);
var user = {
    number:  {USER_ID},
    alias:   '{USERNAME}',
    name:    '{DISPLAY_NAME}',
    handle:  '{USERNAME}',
    location: '{USER_LOCATION}',
    note:    '',
    comment: '',
    netmail: '',
    phone:   '',
    address: '',
    zipcode: '',
    birthdate: '1990-01-01',
    sex:     '?',
    rest:    0,
    security: {
        level: {SECURITY_LEVEL},
        password: '',
        flags1: 0xffffffff, flags2: 0xffffffff,
        flags3: 0xffffffff, flags4: 0xffffffff,
        exempt: 0xffffffff,
        restrictions: 0,
    },
    stats: {
        total_logons: {LOGIN_COUNT},
        total_posts: 0,
        total_emails: 0,
        files_uploaded: 0,
        files_downloaded: 0,
        bytes_uploaded: 0,
        bytes_downloaded: 0,
        total_files: 0,
        total_chats: 0,
        timeon: 0,
        ttoday: 0,
    },
    settings: 0x1E,
    flags1: 0, flags2: 0, flags3: 0, flags4: 0,
    laston_date: _logon_unix,
    firston_date: _logon_unix,
    pwmod_date: _logon_unix,
    expiration_date: _logon_unix + 86400 * 365,
    new_file_time: _logon_unix,
    download_protocol: 'Z',
    qwk_settings: 0,
    chat_settings: 0,
    editor: '',
    shell: '',
    xedit: '',
    tmpext: 'ZIP',
    rows: 24, cols: 80,
};

// === server object — Synchronet's host metadata ===
// dorkit picks sbbs mode iff bbs+server+client+user+console are all
// defined. Without `server`, dorkit falls through to (effectively) no
// mode and never loads a console driver — that's why LORD draws nothing
// under our shim. Stub server with enough surface that doors don't
// crash if they sample it.
var server = {
    version:       'ANetBBS-compat',
    version_detail:'ANetBBS-compat-1.0',
    revision:      0,
    revision_detail:'0',
    full_version:  'ANetBBS-compat',
    socket_descriptor: 0,
    interface_ip_address: '127.0.0.1',
    host_name:     'anetbbs',
    name:          'ANetBBS',
};

// === client object — the user's connection details ===
var client = {
    protocol:      'Telnet',     // 'Telnet' | 'SSH' | 'RLogin' | …
    user:          '{USERNAME}',
    host_name:     'anetbbs',
    ip_address:    '127.0.0.1',
    port:          23,
    time:          _logon_unix,
    usernum:       {USER_ID},
    socket: {
        descriptor: 0,
        local_port: 23,
        remote_port: 0,
    },
};

// === bbs object ===
var bbs = {
    sys_name:    '{BBS_NAME}',
    sys_operator: 'Sysop',
    node_num:    {NODE_NUMBER},
    online:      2,                    // ON_REMOTE=2 in sbbsdefs
    time_left:   7200,
    logon_time:  _logon_unix,
    start_time:  _logon_unix,
    timeleft:    7200,
    exec_dir:    '{EXEC_DIR}',
    text_dir:    '{TEXT_DIR}',
    data_dir:    '{DATA_DIR}',
    mods_dir:    '{MODS_DIR}',
    node_dir:    '{EXEC_DIR}',
    cmd_str:     '',
    command_str: '',
    sys_status:  0,
    sys_misc:    0,
    sys_psname:  'ANetBBS',
    get_time_left: function () {
        // Time left in seconds. Synchronet's doors call this to drive
        // the in-door time clock. Return a generous default.
        return 7200;
    },
    nodesync:    function () { /* check node state — no-op */ },
    replace_text: function () { /* override text.dat strings — no-op */ },
    menu:        function (name) {
        var fname = _path.join(js.exec_dir, String(name) + '.msg');
        if (_fs.existsSync(fname)) process.stdout.write(_fs.readFileSync(fname, 'binary'));
    },
    log_str:     function (msg) { process.stderr.write('[BBS LOG] ' + String(msg) + '\n'); },
    log_key:     function () {},
    exec:        function (cmdline) {
        var cp = _node_require('child_process');
        try { cp.execSync(String(cmdline), {stdio: 'inherit'}); } catch (e) {}
    },
    send_file:   function (path, prot, desc) {
        // LORD uses bbs.send_file for file downloads. Stub by writing a
        // brief notice — the actual ZMODEM machinery is out of scope.
        process.stderr.write('[BBS] send_file(' + path + ') — not supported in compat mode\n');
        return false;
    },
    // Used by mouse_getkey.js, ansiterm_lib.js, etc. as a scratchpad.
    mods: {},
};

// === system object ===
var system = {
    name:       '{BBS_NAME}',
    operator:   'Sysop',
    nodes:      4,
    platform:   'Unix',
    version:    'ANetBBS Compat',
    node_list:  [],
    // sbbs_console.js reads these — fill with sensible paths so doors that
    // try to read or write under data_dir/node_dir don't blow up.
    node_dir:   '{EXEC_DIR}/',
    data_dir:   '{DATA_DIR}/',
    text_dir:   '{TEXT_DIR}/',
    ctrl_dir:   '{EXEC_DIR}/',
    exec_dir:   '{EXEC_DIR}/',
    mods_dir:   '{MODS_DIR}/',
    temp_dir:   '/tmp/',
    qwk_id:     'ANETBBS',
    os_version: 'Linux',
    psname:     'ANetBBS',
    socket_options: {},
    matchuser: function (s) { return 0; },
    matchuserdata: function () { return 0; },
    username: function (n) { return '{USERNAME}'; },
    // Synchronet exposes system.exec(cmd) for shell invocations. DSR uses
    // it to convert images to sixel via ImageMagick. Capture stderr (don't
    // inherit) so warnings don't bleed onto the user's terminal mid-image.
    // Log to gunicorn-error.log so we can see what command ran and why it
    // failed if no sixel file appears.
    exec: function(cmd) {
        // Use spawnSync with explicit argv (no shell parsing) for direct
        // commands; fall back to /bin/sh -c for redirects or pipes.
        var cp = _node_require('child_process');
        var s = String(cmd);
        function parseArgv(s) {
            var argv = [], cur = '', q = false;
            for (var i = 0; i < s.length; i++) {
                var c = s.charAt(i);
                if (c === '"') { q = !q; continue; }
                if (!q && c === ' ') {
                    if (cur.length) { argv.push(cur); cur = ''; }
                    continue;
                }
                cur += c;
            }
            if (cur.length) argv.push(cur);
            return argv;
        }
        var useShell = (s.indexOf('>') >= 0 || s.indexOf('|') >= 0 ||
                        s.indexOf('&&') >= 0 || s.indexOf('||') >= 0 ||
                        s.indexOf(';') >= 0);
        try {
            if (useShell) {
                cp.execSync(s, { stdio: ['ignore', 'inherit', 'inherit'] });
                return 0;
            }
            var argv = parseArgv(s);
            var bin = argv.shift();
            var r = cp.spawnSync(bin, argv,
                                 { stdio: ['ignore', 'inherit', 'inherit'] });
            return (r.status === null) ? -1 : r.status;
        } catch (e) {
            return (e && typeof e.status === 'number') ? e.status : -1;
        }
    },
    // system.popen(cmd) — returns the process stdout as an array of lines.
    // (DSR doesn't use this but other doors do.)
    popen: function(cmd) {
        try {
            var cp = _node_require('child_process');
            var out = cp.execSync(String(cmd), { encoding: 'utf8' });
            return String(out).split(/\r?\n/);
        } catch (e) { return []; }
    },
};

// === js object ===
// In real Synchronet, js.exec_dir is the directory containing the running .js
// file — NOT the global SBBS exec dir. Self-contained doors (Bot Wars, etc.)
// `silentLoad(js.exec_dir + "utils.js")` to load their sibling files. Default
// it to the game's own directory; fall back to the configured exec_dir only
// if the user explicitly set Game.synchronet_exec_dir to override.
// LORD (and any other proper Synchronet door) also expects:
//   js.load_path_list   — Array used by require(); doors `.unshift()` paths
//   js.on_exit(stringOfCode)   — defer this code until process exit
//   js.exec(path,mode,scope,...args) — load + run another .js file
//   js.gc(), js.global, js.auto_terminate, js.terminate_signaled
// Provide all of them so doors don't TypeError on .unshift / .on_exit etc.
var _exit_hooks = [];
var js = {
    exec_dir:     '{GAME_DIR}/',
    startup_dir:  '{GAME_DIR}/',
    stubs_dir:    '{STUBS_DIR}/',
    sbbs_exec:    '{EXEC_DIR}/',
    terminated:   false,
    branch_limit: 100000,
    auto_terminate: false,
    terminate_signaled: false,
    // Mutable list of dirs require() should search BEFORE its built-in
    // fall-back chain. Doors like LORD push '<exec_dir>/dorkit/' onto this
    // so their helper libs resolve.
    load_path_list: [],
    global: (typeof globalThis !== 'undefined') ? globalThis : (function(){return this;})(),
    gc: function () { /* node has its own GC; no-op */ },
    // Register a string of JS code (Synchronet's flavor) OR a function to
    // run at process exit. Doors typically pass a string of code to undo
    // their setup (e.g. `js.on_exit("js.load_path_list.shift()")`).
    on_exit: function (code) { _exit_hooks.push(code); },
    // Synchronously load + eval another .js file. The real Synchronet
    // takes (filename, mode, scope, ...args); for our purposes we treat
    // them all as "load this file as a script", honoring load_path_list.
    exec: function (filename /* , mode, scope, args... */) {
        try {
            load(filename);
            return 0;
        } catch (e) {
            try { process.stderr.write('[BBS] js.exec(' + filename + ') failed: ' + e + '\n'); } catch (e2) {}
            return -1;
        }
    },
};
// Wire exit hooks
try {
    process.on('exit', function () {
        for (var i = 0; i < _exit_hooks.length; i++) {
            var h = _exit_hooks[i];
            try {
                if (typeof h === 'function') { h(); }
                else if (typeof h === 'string') { _vm.runInThisContext(h); }
            } catch (e) {
                // Doors register exit hooks that reference variables which
                // may already be out of scope by exit (e.g. LORD's hook
                // referring to `player`). The hook's purpose was undo-on-
                // exit cleanup; if the variable's already gone the
                // cleanup is moot. Silently swallow so the user doesn't
                // see a confusing stderr line under their "Quit to fields"
                // message. Set BBS_DEBUG_EXIT_HOOKS=1 to re-enable.
                if (process.env && process.env.BBS_DEBUG_EXIT_HOOKS) {
                    try { process.stderr.write('[BBS] exit hook failed: ' + e + '\n'); } catch (e2) {}
                }
            }
        }
    });
} catch (e) {}

// === Synchronet exposes argv/argc as top-level globals containing args
// passed after the script name. We don't pass any door args (the BBS gives
// the door its context via drop files / js.exec_dir), so an empty array
// suffices. DSR and other doors do `if (argv.length > 0)` style checks.
var argv = [];
var argc = 0;

// === Stub areas ===
var file_area = { dir: {} };
var msg_area  = { grp: {} };

// === Queue — Synchronet inter-script FIFO ===
// In real Synchronet, Queues are named IPC channels: two `new Queue("foo")`
// calls in different scripts bind to the SAME channel. We don't have IPC
// in our single-process Node shim, but we MUST preserve the same-name-
// same-channel semantics or things break subtly:
//   dorkit.js does `dk.console.input_queue = new Queue("dorkit_input"+N)`
//   ansi_input.js does `ai.input_queue   = new Queue("dorkit_input"+N)`
// These NEED to be the same Queue or processed keystrokes never reach
// the polling consumer.  Use a cache keyed on name.
var _queue_cache = {};
function Queue(name) {
    var n = String(name == null ? '' : name);
    if (_queue_cache.hasOwnProperty(n)) {
        return _queue_cache[n];
    }
    if (!(this instanceof Queue)) { return new Queue(n); }
    this._name = n;
    this._items = [];
    _queue_cache[n] = this;
}
Object.defineProperty(Queue.prototype, 'name', {
    get: function() { return this._name; },
    enumerable: true,
});
Queue.prototype.write = function (v) {
    this._items.push(v);
    return true;
};
Queue.prototype.peek = function () {
    return this._items.length > 0 ? this._items[0] : undefined;
};
Queue.prototype.read = function () {
    if (this._items.length === 0) {
        // Last-ditch — synchronously read one byte from stdin so callers
        // that go read()-without-poll don't hang the whole game on EOF.
        try {
            var b = Buffer.alloc(1);
            var n = _fs.readSync(0, b, 0, 1, null);
            if (n > 0) {
                return String.fromCharCode(b[0]);
            }
        } catch (e) { /* EAGAIN / no tty / EOF — fall through */ }
        return undefined;
    }
    return this._items.shift();
};
// Synchronet poll(timeoutMs) — wait up to timeoutMs for data, return
// truthy if available, false on timeout. We do the stdin read here so
// dorkit's waitkey() → poll() → read() flow has bytes to return.
Queue.prototype.poll = function (timeoutMs) {
    // Stdin reading is driven by sbbs_input.js's input_queue_callback
    // (registered on dk.console.input_queue_callback). That callback
    // fires from dorkit's waitkey() loop on every iteration, does a
    // single non-blocking readSync, and forwards bytes through
    // ansi_input.js's `ai.add(byte)` which in turn writes processed
    // keystrokes into THIS queue. Poll just reports whether anything
    // has landed.
    return this._items.length > 0;
};
Queue.prototype.toString = function () {
    return '[Queue ' + this._name + ' len=' + this._items.length + ']';
};

// === Top-level file helpers (Synchronet built-ins) ===
function file_exists(p) {
    try { return _fs.existsSync(p); } catch (e) { return false; }
}
function file_isdir(p) {
    try { return _fs.statSync(p).isDirectory(); } catch (e) { return false; }
}
function file_isfile(p) {
    try { return _fs.statSync(p).isFile(); } catch (e) { return false; }
}
function file_size(p) {
    try { return _fs.statSync(p).size; } catch (e) { return -1; }
}
function file_date(p) {
    try { return Math.floor(_fs.statSync(p).mtime.getTime() / 1000); }
    catch (e) { return 0; }
}
function file_remove(p) {
    try { _fs.unlinkSync(p); return true; } catch (e) { return false; }
}
// Synchronet `file_mutex(filename, contents="", max_age_seconds=0, hostname="", pid=0)`
// — atomic single-writer lock file. If `contents` is given, the lock file
// is created carrying that text so other nodes can see who's holding it
// (LORD reuses it as a small write-once data file: war reports, mail
// drops, fairy logs). Real Synchronet refuses if the file exists AND
// max_age hasn't expired AND the owner identity in the file isn't ours;
// for single-node ANetBBS we don't have peer-node contention, so the
// stub always succeeds — overwriting any stale lock left behind by a
// previous run. LORD's wrapper `fmutex()` loops 15 s on failure so the
// always-grant behaviour is also resilient to genuine file-system hiccups.
function file_mutex(filename, contents, max_age_seconds, hostname, pid) {
    try {
        if (contents !== undefined && contents !== null) {
            _fs.writeFileSync(String(filename), String(contents));
        } else {
            // Just touch — create empty file if missing.
            try {
                var _fd = _fs.openSync(String(filename), 'a');
                _fs.closeSync(_fd);
            } catch (_) {}
        }
        return true;
    } catch (e) {
        try {
            process.stderr.write('[BBS] file_mutex(' + filename + ') failed: '
                                 + (e && e.message ? e.message : e) + '\n');
        } catch (_) {}
        return false;
    }
}
function file_rename(from, to) {
    try { _fs.renameSync(from, to); return true; } catch (e) { return false; }
}
function file_copy(from, to) {
    try { _fs.copyFileSync(from, to); return true; } catch (e) { return false; }
}
function file_getname(p) {
    return _path.basename(String(p));
}
function file_getext(p) {
    return _path.extname(String(p));
}
function mkdir(p) {
    try { _fs.mkdirSync(p, {recursive: true}); return true; } catch (e) { return false; }
}
function rmdir(p) {
    try { _fs.rmdirSync(p); return true; } catch (e) { return false; }
}
function directory(pattern) {
    // Synchronet directory() returns array of matching files.
    try {
        var dir = _path.dirname(pattern);
        var glob = _path.basename(pattern).replace(/\./g, '\\.').replace(/\*/g, '.*').replace(/\?/g, '.');
        var re = new RegExp('^' + glob + '$', 'i');
        return _fs.readdirSync(dir).filter(function(f) { return re.test(f); })
                  .map(function(f) { return _path.join(dir, f); });
    } catch (e) { return []; }
}

// === log() — Synchronet's built-in log function ===
var LOG_EMERG = 0, LOG_ALERT = 1, LOG_CRIT = 2, LOG_ERR = 3;
var LOG_WARNING = 4, LOG_NOTICE = 5, LOG_INFO = 6, LOG_DEBUG = 7;
function log(arg1, arg2) {
    // Two forms: log(msg) or log(level, msg)
    var msg = arg2 !== undefined ? arg2 : arg1;
    process.stderr.write('[door log] ' + String(msg) + '\n');
    return msg.length;
}
// alias used by some scripts
var alert = log;

// === Synchronet top-level I/O globals ===
// `print(s)` — writes string + CRLF with Ctrl-A code processing. DSR uses
// it for prompts in init() (`print("Preparing ..."); etc.`).
function print(s) {
    process.stdout.write(_translateCtrlA(s === undefined ? '' : String(s)) + '\r\n');
}
// `write(s)` and `writeln(s)` mirror console equivalents at top level.
function write(s) {
    process.stdout.write(_translateCtrlA(s === undefined ? '' : String(s)));
}
function writeln(s) {
    process.stdout.write(_translateCtrlA(s === undefined ? '' : String(s)) + '\r\n');
}
// Synchronet `exit([code])` — ends the door cleanly. DSR calls it from
// init() if ImageMagick is missing or the user declines the SIXEL warning.
function exit(code) {
    process.exit((typeof code === 'number') ? code : 0);
}
// Synchronet `printf(fmt, ...)` — writes formatted output (no auto-CRLF).
// DSR's mainMenu uses it to render selection markers/prompts.
function printf() {
    var args = Array.prototype.slice.call(arguments);
    if (!args.length) return;
    process.stdout.write(_translateCtrlA(format.apply(null, args)));
}
// Synchronet `sprintf` returns the formatted string (no output side-effect).
// Already aliased to `format` above; declare for symmetry/discoverability.
var sprintf = format;
// `mswait(ms)` — Synchronet's blocking sleep. Doors use it for pacing.
function mswait(ms) {
    var n = Number(ms) || 0;
    if (n <= 0) return;
    // Best we can do synchronously in node — busy-wait is fine for short
    // pacing (animation frames). Long mswait calls in real Synchronet are
    // rare; if a door wants seconds it usually uses console.getkey timeout.
    var end = Date.now() + n;
    while (Date.now() < end) { /* spin */ }
}

// === File object ===
function File(filename) {
    this.name = filename;
    this.is_open = false;
    this._fd = null;
    this._content = '';
    this._pos = 0;
}
File.prototype.open = function(mode) {
    // Synchronet supports the full fopen-style mode language:
    //   'r'   read-only existing
    //   'w'   write, truncate (or create)
    //   'a'   append, position at EOF
    //   'r+'  read-write existing, pos 0
    //   'w+'  read-write, truncate / create, pos 0
    //   'a+'  read-write, position at EOF (writes always at EOF)
    // 'b' suffix (binary) is a no-op on Unix; we ignore it.
    var raw = String(mode || 'r').replace(/b/g, '');
    var truncate = raw === 'w' || raw === 'w+';
    var append   = raw === 'a' || raw === 'a+';
    this._can_write = (raw.indexOf('w') >= 0
                    || raw.indexOf('a') >= 0
                    || raw.indexOf('+') >= 0);
    try {
        if (truncate) {
            this._content = '';
        } else {
            // Read existing content. If the file doesn't exist AND mode
            // allows writing ('w*' and 'a*' implicitly create), start
            // with empty content; otherwise propagate ENOENT.
            try {
                this._content = _fs.readFileSync(this.name, 'binary');
            } catch (e) {
                if (this._can_write) {
                    this._content = '';
                } else {
                    throw e;
                }
            }
        }
        this._pos = append ? this._content.length : 0;
        this.is_open = true;
        this._flags = raw;
        return true;
    } catch(e) {
        // Synchronet doors often don't check the open() return value (DSR
        // for one). When a missing/unreadable file makes them later choke
        // on undefined ini keys, the silent fail makes diagnosis impossible.
        // Log to stderr so the wrapper's traceback shows what path failed.
        try {
            process.stderr.write('[BBS] File.open(' + JSON.stringify(this.name) +
                                 ', ' + JSON.stringify(mode) + ') failed: ' +
                                 (e && e.message ? e.message : e) + '\n');
        } catch(_) {}
        return false;
    }
};
File.prototype.close = function() {
    // Persist on close for any mode that can write. We already flush on
    // each writeBin/writeStr/flush, but close on a 'w' or 'a' mode should
    // also commit any plain .write() content that hasn't been flushed.
    if (this._can_write) {
        try {
            _fs.writeFileSync(this.name,
                              Buffer.from(this._content, 'binary'));
        } catch (e) {}
    }
    this.is_open = false;
};
File.prototype.read = function(maxlen) {
    var chunk = this._content.slice(this._pos, this._pos + (maxlen || this._content.length));
    this._pos += chunk.length;
    return chunk;
};
// Synchronet's File exposes record-locking methods used by recordfile.js:
//   file.lock(start, length)    — advisory range lock
//   file.unlock(start, length)  — release the range
//   file.flush()                — fsync()
//   file.truncate(N)            — shrink to N bytes (LORD uses for save reset)
// Single-node ANetBBS has no contention, so .lock/.unlock are no-ops
// returning true. .flush/.truncate map to fs equivalents on the actual
// on-disk file path stored in `this.name`.
File.prototype.lock = function () { return true; };
File.prototype.unlock = function () { return true; };
// Synchronet's binary I/O — used by recordfile.js to read/write fixed-
// width integers (1, 2, 4, or 8 bytes, little-endian, unsigned). LORD's
// per-record storage (war reports, player state, mail) goes through this.
// `_content` is a latin-1 binary string (one char per byte 0–255), so
// charCodeAt(i) is the raw byte value.
File.prototype.readBin = function (bytes) {
    bytes = Number(bytes) || 4;
    if (this._pos + bytes > this._content.length) return null;
    var val = 0;
    for (var i = 0; i < bytes; i++) {
        // Use Math.pow rather than <<i*8 for the 4-byte case so values
        // > 2^31-1 don't sign-extend into JS's signed-int land.
        val += (this._content.charCodeAt(this._pos + i) & 0xFF)
               * Math.pow(2, i * 8);
    }
    this._pos += bytes;
    return val;
};
File.prototype.writeBin = function (value, bytes) {
    bytes = Number(bytes) || 4;
    var v = Number(value) || 0;
    if (v < 0) {                       // tolerate signed input — wrap to unsigned
        v = v + Math.pow(2, bytes * 8);
    }
    var s = '';
    for (var i = 0; i < bytes; i++) {
        s += String.fromCharCode(Math.floor(v / Math.pow(2, i * 8)) & 0xFF);
    }
    // Overwrite (not insert) at current position. If writing past EOF
    // pad with zeros so subsequent reads see consistent bytes.
    if (this._pos > this._content.length) {
        this._content += '\x00'.repeat(this._pos - this._content.length);
    }
    this._content = this._content.slice(0, this._pos)
                  + s
                  + this._content.slice(this._pos + bytes);
    this._pos += bytes;
    // Flush right away so other code that does `file_size(name)` reads
    // a consistent view. recordfile.js relies on this.
    try {
        if (this._can_write) {
            _fs.writeFileSync(this.name,
                              Buffer.from(this._content, 'binary'));
        }
    } catch (e) {}
    return true;
};
// String variants — readStr returns the next N characters as a JS string
// (CP437/latin-1 charCodeAt range), writeStr writes them verbatim. Doors
// like Bot Wars use these for fixed-width identifier fields.
File.prototype.readStr = function (size) {
    var n = Number(size) || 0;
    if (n <= 0) return '';
    var s = this._content.slice(this._pos, this._pos + n);
    this._pos += s.length;
    return s;
};
File.prototype.writeStr = function (str, size) {
    var s = String(str == null ? '' : str);
    if (size && s.length < Number(size)) {
        s = s + ' '.repeat(Number(size) - s.length);
    } else if (size && s.length > Number(size)) {
        s = s.slice(0, Number(size));
    }
    if (this._pos > this._content.length) {
        this._content += '\x00'.repeat(this._pos - this._content.length);
    }
    this._content = this._content.slice(0, this._pos)
                  + s
                  + this._content.slice(this._pos + s.length);
    this._pos += s.length;
    try {
        if (this._can_write) {
            _fs.writeFileSync(this.name,
                              Buffer.from(this._content, 'binary'));
        }
    } catch (e) {}
    return true;
};
File.prototype.flush = function () {
    // Write-mode File holds content in _content; persist now without
    // closing so subsequent writes still queue.
    try {
        if (this._flags === 'w') {
            _fs.writeFileSync(this.name, Buffer.from(this._content, 'binary'));
        }
        return true;
    } catch (e) { return false; }
};
File.prototype.truncate = function (n) {
    var len = Number(n || 0);
    this._content = (this._content || '').slice(0, len);
    this._pos = Math.min(this._pos, len);
    // Persist if we already have a file on disk so a re-open sees the
    // truncated form (matches Synchronet's behaviour).
    try {
        if (_fs.existsSync(this.name)) {
            _fs.truncateSync(this.name, len);
        }
    } catch (e) {}
    return true;
};
// Synchronet's File exposes .position (getter+setter) for seeking. LORD's
// build_txt_index uses `txtfile.position` to remember byte offsets of
// `@#` records so display_file can later seek back. Without it the
// `position` reads/writes silently no-op.
Object.defineProperty(File.prototype, 'position', {
    get: function () { return this._pos; },
    set: function (v) {
        var n = Number(v);
        if (isNaN(n) || n < 0) return;
        this._pos = Math.min(n, this._content.length);
    },
    enumerable: true,
    configurable: true,
});
// Some doors (sauce_lib, BotWars) check .length as a property (NOT a
// method). We already defined File.prototype.length as a function above
// — turn it into a dual: callable for back-compat AND a property reader
// via a custom getter on the instance. Simplest: redefine as a property.
try {
    delete File.prototype.length;
} catch (_) {}
Object.defineProperty(File.prototype, 'length', {
    get: function () { return this._content.length; },
    enumerable: true,
    configurable: true,
});
File.prototype.readln = function() {
    // Synchronet semantics: returns null at EOF (NOT empty string). LORD's
    // `build_txt_index` loops `while (true) { l = readln(); if (l===null) break; }`
    // so returning '' here means infinite spin on the last line.
    if (this._pos >= this._content.length) {
        return null;
    }
    var nl = this._content.indexOf('\n', this._pos);
    if (nl < 0) nl = this._content.length;
    var line = this._content.slice(this._pos, nl);
    this._pos = (nl < this._content.length) ? nl + 1 : nl;
    return line.replace(/\r$/, '');
};
// CRITICAL: write() must respect the current `_pos` and OVERWRITE at the
// cursor — not append to EOF. recordfile.js calls `this.file.write(wr, len)`
// for every String / Date / Float field at a seeked record offset
// (`this.file.position = rec * RecordLength` then a sequence of writes).
// Plain `_content += s` dumps everything at the tail — every fixed-width
// field lands in the wrong slot. Result: player records appear as
// scrambled garbage in LORD's list, names mixed with timestamps, etc.
File.prototype.write = function(str, len) {
    var s = String(str == null ? '' : str);
    if (len !== undefined && Number(len) >= 0
            && s.length > Number(len)) {
        s = s.slice(0, Number(len));
    }
    if (this._pos > this._content.length) {
        this._content += '\x00'.repeat(this._pos - this._content.length);
    }
    this._content = this._content.slice(0, this._pos)
                  + s
                  + this._content.slice(this._pos + s.length);
    this._pos += s.length;
    try {
        if (this._can_write) {
            _fs.writeFileSync(this.name,
                              Buffer.from(this._content, 'binary'));
        }
    } catch (e) {}
    return true;
};
File.prototype.writeln = function(str) {
    return this.write(String(str == null ? '' : str) + '\n');
};
File.prototype.eof = function() { return this._pos >= this._content.length; };
// Synchronet's File API has these — without them, doors that use the standard
// f.readAll().join("") JSON-load idiom (Bot Wars) silently fail to load saves.
File.prototype.readAll = function() {
    var rest = this._content.slice(this._pos);
    this._pos = this._content.length;
    return rest.split('\n').map(function(l) { return l.replace(/\r$/, ''); });
};
File.prototype.writeAll = function(arr) {
    if (Array.isArray(arr)) this._content += arr.join('\n') + '\n';
    else this._content += String(arr);
    return true;
};
// Synchronet uses lowercase isopen as a property on File. Old code may also
// poll either form; mirror is_open onto isopen so both work.
Object.defineProperty(File.prototype, 'isopen', {
    get: function() { return !!this.is_open; },
});
File.prototype.length = function() { return this._content.length; };

// === Synchronet INI helpers — File.iniGetObject / iniGetValue / etc ===
// DSR's init() calls fIni.iniGetObject(null) (root section) and section
// reads. Doors like Bot Wars also use iniGetSections / iniGetAllObjects.
File.prototype._parseIni = function() {
    if (this._iniCache && this._iniCacheLen === this._content.length) {
        return this._iniCache;
    }
    var lines = this._content.split(/\r?\n/);
    var sections = { '': {} };
    var cur = '';
    for (var i = 0; i < lines.length; i++) {
        var line = lines[i].replace(/^\s+|\s+$/g, '');
        if (!line || line.charAt(0) === ';' || line.charAt(0) === '#') continue;
        var sm = line.match(/^\[(.*)\]$/);
        if (sm) {
            cur = sm[1];
            if (!sections[cur]) sections[cur] = {};
            continue;
        }
        var eq = line.indexOf('=');
        if (eq > 0) {
            var k = line.slice(0, eq).replace(/^\s+|\s+$/g, '');
            var v = line.slice(eq + 1).replace(/^\s+|\s+$/g, '');
            sections[cur][k] = v;
        }
    }
    this._iniCache = sections;
    this._iniCacheLen = this._content.length;
    return sections;
};
File.prototype.iniGetSections = function(prefix) {
    var s = this._parseIni();
    var out = [];
    for (var name in s) {
        if (name === '') continue;
        if (!prefix || name.indexOf(prefix) === 0) out.push(name);
    }
    return out;
};
File.prototype.iniGetKeys = function(section) {
    var s = this._parseIni();
    var key = (section === null || section === undefined) ? '' : section;
    return Object.keys(s[key] || {});
};
File.prototype.iniGetValue = function(section, key, defaultValue) {
    var s = this._parseIni();
    var skey = (section === null || section === undefined) ? '' : section;
    var sec = s[skey];
    if (sec && key in sec) return sec[key];
    return (defaultValue === undefined) ? null : defaultValue;
};
File.prototype.iniGetObject = function(section) {
    var s = this._parseIni();
    // Synchronet's iniGetObject returns the keys of one section as a flat
    // object. Default (no arg) is the root section (keys before any
    // [header]). 'root' is the conventional alias.
    // DSR wraps it as `{ root: fIni.iniGetObject() }` so the result must
    // be flat root keys, not nested.
    var skey = (section === null || section === undefined ||
                section === 'root') ? '' : section;
    var sec = s[skey];
    if (!sec) return null;
    var obj = {};
    for (var k in sec) obj[k] = sec[k];
    return obj;
};
File.prototype.iniGetAllObjects = function(idName, prefix) {
    var s = this._parseIni();
    var out = [];
    var prefLen = prefix ? prefix.length : 0;
    var idKey = idName || 'name';
    for (var name in s) {
        if (name === '') continue;
        if (prefix && name.indexOf(prefix) !== 0) continue;
        var obj = {};
        obj[idKey] = prefix ? name.slice(prefLen) : name;
        for (var k in s[name]) obj[k] = s[name][k];
        out.push(obj);
    }
    return out;
};

// === load / require ===
// Cache loaded files to prevent infinite recursion (a file `require()`ing
// another file that `require()`s it back, or scripts that load themselves).
var _load_cache = {};
var _load_depth = 0;

function load() {
    // Synchronet's load() supports several forms:
    //   load("file.js")
    //   load(scopeObject, "file.js")           ← used by sauce_lib & others
    //   load(scopeObject, "file.js", arg, ...)  ← scope + script args
    //   load("file.js", arg, ...)
    // Detect the scope-object form and shift args. The scope object's
    // properties become globals visible inside the loaded script — for
    // simplicity we merge the loaded module's exports back onto the
    // scope object (which is what callers like `var sauce = load({},
    // "sauce_lib.js")` expect — they reassign the scope after load).
    var args = Array.prototype.slice.call(arguments);
    var scope = null;
    var background = false;
    // load(true, "file.js", ...args) — synchronet's "spawn this script
    // in a background JS thread and return a Queue for IPC". Node has
    // no JS threads, so we run inline and return a stub Queue at the
    // end. Callers like sbbs_console.js use the Queue only for the
    // exit-cleanup write/poll, so an inert stub is fine.
    if (args.length >= 1 && (args[0] === true || args[0] === false)) {
        background = args.shift();
    }
    if (args.length >= 2 && typeof args[0] === 'object' && args[0] !== null
            && typeof args[1] === 'string') {
        scope = args.shift();
    }
    var filename = args.shift();
    if (typeof filename !== 'string') {
        throw new Error('load(): filename must be a string, got ' +
                        typeof filename);
    }
    var attempted = [];
    function _try(p) { attempted.push(p); return _fs.existsSync(p) ? p : null; }

    var fullpath = null;
    if (_path.isAbsolute(filename)) {
        fullpath = _try(filename);
    } else {
        // Honor js.load_path_list FIRST — doors prepend paths there
        // (e.g. LORD does `js.load_path_list.unshift(js.exec_dir+"dorkit/")`
        // so its dorkit helper libs resolve).
        if (Array.isArray(js.load_path_list)) {
            for (var _lp = 0; _lp < js.load_path_list.length && !fullpath; _lp++) {
                fullpath = _try(_path.join(js.load_path_list[_lp], filename));
            }
        }
        fullpath = fullpath
                || _try(_path.join(js.startup_dir || '.', filename))
                || _try(_path.join(js.exec_dir   || '.', filename))
                // Conventional Synchronet subdirs the script's own tree
                || _try(_path.join(js.exec_dir   || '.', 'dorkit', filename))
                || _try(_path.join(js.exec_dir   || '.', 'load',   filename))
                || _try(_path.resolve(filename))
                // Stub-tree dorkit/ + load/ FIRST — dorkit-internal files
                // (screen.js, graphic.js, attribute.js, etc.) have copies
                // in BOTH `<stubs_dir>/` flat (older / different) AND
                // `<stubs_dir>/dorkit/` (current upstream). Prefer the
                // dorkit-subdir version so Screen, Graphic, etc. carry
                // the prototype methods their callers expect.
                || _try(_path.join(js.stubs_dir  || '.', 'dorkit', filename))
                || _try(_path.join(js.stubs_dir  || '.', 'load',   filename))
                || _try(_path.join(js.stubs_dir  || '.', filename));
    }
    if (!fullpath) {
        var msg = 'Synchronet load(): cannot find "' + filename + '"\n';
        msg += 'Searched paths (each enclosed in [] to show exact value):\n';
        for (var _i = 0; _i < attempted.length; _i++) {
            msg += '  [' + attempted[_i] + ']  exists=' + _fs.existsSync(attempted[_i]) + '\n';
        }
        // Also show what stubs_dir resolves to + a sample listing
        try {
            var entries = _fs.readdirSync(js.stubs_dir || '.');
            msg += 'stubs_dir [' + js.stubs_dir + '] contains ' + entries.length + ' files';
            if (entries.length > 0) {
                msg += ', first 5: ' + entries.slice(0, 5).join(', ');
            }
        } catch (e) {
            msg += 'stubs_dir [' + js.stubs_dir + '] readdir failed: ' + e.message;
        }
        throw new Error(msg);
    }
    if (_load_cache[fullpath] && scope === null) {
        return; // already loaded (cache only applies to no-scope form)
    }
    _load_cache[fullpath] = true;
    _load_depth++;
    if (_load_depth > 50) {
        _load_depth--;
        throw new Error('Synchronet load(): max depth exceeded loading ' + filename
                        + ' (likely a circular require).');
    }
    try {
        var code = _fs.readFileSync(fullpath, 'utf8');
        // vm.runInThisContext runs the code with globalThis as its scope —
        // `var FOO = ...` and `function FOO()` create properties on
        // globalThis, which is what we need so require()'s lookup finds
        // them. Indirect (0,eval)() does NOT do this in Node's CommonJS
        // module wrapper context (hence past "SMB_SUCCESS is not defined").
        var result = _vm.runInThisContext(code, { filename: fullpath });
        // Synchronet's 2-arg form: load(scope, "file") returns whatever
        // the script's last expression evaluates to. Modules like
        // sauce_lib.js end with `this;` so the last expression is the
        // global object — which has the script's exported functions
        // attached after our runInThisContext run. Caller does
        // `var sauce = load({}, "sauce_lib.js")` and then `sauce.read(...)`
        // resolves to globalThis.read, which is exactly what's needed.
        if (scope !== null) {
            return result;
        }
        if (background) {
            // Caller used the load(true, ...) form expecting a Queue
            // back for IPC with the "spawned" script. Hand them an
            // empty Queue — the upstream sbbs_input.js loop is the
            // only realistic consumer and our replacement runs inline
            // anyway, so the Queue just collects cleanup writes.
            return new Queue('load_background_' + _path.basename(filename));
        }
    } finally {
        _load_depth--;
    }
    if (background) {
        return new Queue('load_background_' + _path.basename(filename));
    }
}

function require() {
    // Synchronet's require() accepts the same scope-prefix form as load():
    //   require(filename, objname)            ← classic form
    //   require(scope, filename, objname)     ← used by LORD (cnflib loader)
    // The scope object becomes the target for any `var FOO = ...` in the
    // loaded file: after the file runs we copy the now-global FOO back
    // onto `scope.FOO` so callers like `require(scope,"cnflib.js","CNF")`
    // can read `scope.CNF.read(...)`.
    var args = Array.prototype.slice.call(arguments);
    var scope = null;
    if (args.length >= 2 && typeof args[0] === 'object' && args[0] !== null
            && typeof args[1] === 'string') {
        scope = args.shift();
    }
    var filename = args.shift();
    var objname  = args.shift();
    if (typeof filename !== 'string') {
        throw new Error('require(): filename must be a string, got ' +
                        typeof filename);
    }
    load(filename);
    // After vm.runInThisContext, `var FOO = ...` in the loaded file is on
    // globalThis. Read from there directly — more reliable than eval.
    var g = (typeof globalThis !== 'undefined' ? globalThis : global);
    if (objname) {
        var val = (g[objname] !== undefined)
            ? g[objname]
            : ((function(){ try { return (0, eval)(objname); } catch(e) { return undefined; }})());
        if (scope && val !== undefined) { scope[objname] = val; }
        return val;
    }
}

// === Constants ===
var P_NONE = 0;
var P_NOATCODES = (1<<0);
var K_NONE = 0;
var K_UPPER = (1<<0);
var K_NOECHO = (1<<1);
var K_LINE = (1<<8);
var KEY_UP    = '\033[A';
var KEY_DOWN  = '\033[B';
var KEY_LEFT  = '\033[D';
var KEY_RIGHT = '\033[C';
var KEY_HOME  = '\033[H';
var KEY_END   = '\033[F';
var KEY_DEL   = '\033[3~';
var ANSI_NORMAL = '\033[0m';

// ANSI colour attribute constants (from sbbsdefs.js)
var BLACK   = 0;  var BLUE    = 1;  var GREEN   = 2;  var CYAN    = 3;
var RED     = 4;  var MAGENTA = 5;  var BROWN   = 6;  var LIGHTGRAY = 7;
var DARKGRAY= 8;  var LIGHTBLUE=9;  var LIGHTGREEN=10;var LIGHTCYAN=11;
var LIGHTRED=12;  var LIGHTMAGENTA=13; var YELLOW=14; var WHITE=15;
var HIGH    = 8;
var BG_BLACK   = 0<<4; var BG_BLUE    = 1<<4; var BG_GREEN   = 2<<4;
var BG_CYAN    = 3<<4; var BG_RED     = 4<<4; var BG_MAGENTA = 5<<4;
var BG_BROWN   = 6<<4; var BG_LIGHTGRAY = 7<<4;

// === Global utility functions ===
function random(n) { return Math.floor(Math.random() * n); }
function time() { return Math.floor(Date.now() / 1000); }
function ascii(n) { return String.fromCharCode(n); }
function ascii_str(s) { return s.charCodeAt(0); }

// Synchronet's strftime(fmt, unixSeconds) — same conversion specifiers as
// C's strftime. Doors (LORD's dorkit/local_console.js included) call
// `strftime("%H:%M", time())` for clocks; without this it ReferenceErrors.
function strftime(fmt, unixSeconds) {
    var d = (unixSeconds == null) ? new Date()
                                  : new Date(Number(unixSeconds) * 1000);
    function p2(n) { return (n < 10 ? '0' : '') + n; }
    var months_long  = ['January','February','March','April','May','June',
                        'July','August','September','October','November','December'];
    var months_short = ['Jan','Feb','Mar','Apr','May','Jun',
                        'Jul','Aug','Sep','Oct','Nov','Dec'];
    var days_long    = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
    var days_short   = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
    return String(fmt).replace(/%./g, function (m) {
        switch (m[1]) {
            case 'Y': return d.getFullYear();
            case 'y': return p2(d.getFullYear() % 100);
            case 'm': return p2(d.getMonth() + 1);
            case 'd': return p2(d.getDate());
            case 'e': return (d.getDate() < 10 ? ' ' : '') + d.getDate();
            case 'H': return p2(d.getHours());
            case 'I': { var h = d.getHours() % 12; return p2(h === 0 ? 12 : h); }
            case 'M': return p2(d.getMinutes());
            case 'S': return p2(d.getSeconds());
            case 'p': return d.getHours() < 12 ? 'AM' : 'PM';
            case 'P': return d.getHours() < 12 ? 'am' : 'pm';
            case 'A': return days_long[d.getDay()];
            case 'a': return days_short[d.getDay()];
            case 'B': return months_long[d.getMonth()];
            case 'b': case 'h': return months_short[d.getMonth()];
            case 'j': {
                var start = new Date(d.getFullYear(), 0, 0);
                var diff = d - start;
                return String(Math.floor(diff / 86400000)).padStart(3, '0');
            }
            case 'w': return String(d.getDay());
            case 'n': return '\n';
            case 't': return '\t';
            case '%': return '%';
            case 'c': return d.toString();
            case 'x': return p2(d.getMonth() + 1) + '/' + p2(d.getDate()) + '/' + p2(d.getFullYear() % 100);
            case 'X': return p2(d.getHours()) + ':' + p2(d.getMinutes()) + ':' + p2(d.getSeconds());
            default: return m;  // unknown — leave as-is
        }
    });
}
function format() {
    // C-style sprintf supporting flags (-/0/+/space), width (digits or *),
    // precision (.digits or .*), and types s/d/i/f/x/X/c/%.
    // DSR uses `printf("%*s", width, str)` — without `*` support that
    // shows up as literal "%*s" in the output and the gallery list is
    // unaligned.
    var args = Array.prototype.slice.call(arguments);
    var fmt = String(args.shift());
    return fmt.replace(/%(-?)(\*|\d+)?(?:\.(\*|\d+))?([sdifxXc%])/g,
        function(m, flags, w, p, t) {
            if (t === '%') return '%';
            var width = (w === '*') ? Number(args.shift()) : (w ? parseInt(w, 10) : 0);
            var prec  = (p === '*') ? Number(args.shift()) : (p ? parseInt(p, 10) : -1);
            var v = args.shift();
            var out;
            if (t === 'd' || t === 'i') out = String(parseInt(v, 10));
            else if (t === 'f') out = (prec >= 0) ? parseFloat(v).toFixed(prec) : String(parseFloat(v));
            else if (t === 'x') out = (parseInt(v, 10) >>> 0).toString(16);
            else if (t === 'X') out = (parseInt(v, 10) >>> 0).toString(16).toUpperCase();
            else if (t === 'c') out = String.fromCharCode(parseInt(v, 10));
            else { out = String(v == null ? '' : v); if (prec >= 0) out = out.slice(0, prec); }
            if (width > out.length) {
                var pad = ' '.repeat(width - out.length);
                out = (flags === '-') ? (out + pad) : (pad + out);
            }
            return out;
        });
}
function truncsp(s) { return String(s).replace(/\s+$/, ''); }
function strip_ctrl(s) { return String(s).replace(/[\x00-\x1f\x7f]/g, ''); }
function word_wrap(s, width) {
    var w = width || 79;
    var out = '';
    s.split('\n').forEach(function(line) {
        while (line.length > w) {
            var sp = line.lastIndexOf(' ', w);
            if (sp < 0) sp = w;
            out += line.slice(0, sp) + '\n';
            line = line.slice(sp + 1);
        }
        out += line + '\n';
    });
    return out;
}
function lfexpand(s) { return String(s).replace(/\n/g, '\r\n'); }
function backslash(s) { return String(s).replace(/\\/g, '/') + '/'; }
function file_exists(path) { return _fs.existsSync(path); }
function file_getname(path) { return _path.basename(path); }
function directory(pattern) {
    var dir = _path.dirname(pattern);
    var base = _path.basename(pattern).replace(/\*/g, '.*').replace(/\?/g, '.');
    var re = new RegExp('^' + base + '$');
    try {
        return _fs.readdirSync(dir).filter(function(f){ return re.test(f); })
                  .map(function(f){ return _path.join(dir, f); });
    } catch(e) { return []; }
}
// (log/alert defined earlier — handle both log(msg) and log(level, msg))

// Common helpers many doors expect at global scope (Synchronet ships them
// either as builtins or via `load("standard.js")`; we provide them directly).
function clearScreen() { return console.clear(); }
function home()        { return console.home(); }
function cleartoeol()  { return console.cleartoeol(); }
function gotoxy(x, y)  { return console.gotoxy(x, y); }
function crlf()        { return console.crlf(); }
function pause()       { return console.pause(); }
// (legacy duplicate format() removed — the upstream sprintf above now
// handles flags / width / precision / `*` properly. The previous override
// here clobbered it and stripped %*s width specifiers.)
function _unused_format_legacy_removed() {
    var args = Array.prototype.slice.call(arguments);
    var fmt = args.shift();
    var i = 0;
    return String(fmt).replace(/%([0-9.+-]*)([sdfx])/g, function(_, flags, type) {
        var v = args[i++];
        if (type === 'd') return parseInt(v, 10).toString();
        if (type === 'f') return parseFloat(v).toString();
        if (type === 'x') return parseInt(v, 10).toString(16);
        return String(v);
    });
}

// === Attach our compat functions/objects to GLOBAL so indirect-eval'd code
// === (the kind from `(0, eval)(code)` in load()) can see them. Without this,
// === code inside loaded files calling require(), file_exists(), bbs.X, etc
// === throws ReferenceError because indirect eval doesn't capture the local
// === function declarations of this script.
(function _registerGlobals() {
    var names = [
        'load', 'require', 'log', 'alert',
        'file_exists', 'file_isdir', 'file_isfile', 'file_size', 'file_date',
        'file_remove', 'file_rename', 'file_copy', 'file_getname', 'file_getext',
        'file_mutex',
        'mkdir', 'rmdir', 'directory',
        'lfexpand', 'backslash',
        'console', 'bbs', 'user', 'system', 'js', 'server', 'client',
        'file_area', 'msg_area', 'File',
        // Queue — Synchronet inter-script FIFO; needed by dorkit.js
        // line 273 (`new Queue("dorkit_input"...)`) when it's loaded
        // via vm.runInThisContext from inside our load() function.
        // Without exposing it to global, dorkit sees ReferenceError.
        'Queue',
        '_fs', '_path', '_readline', '_node_require',
        // Common helper names many doors expect (alias of console.X):
        'clearScreen', 'home', 'cleartoeol', 'gotoxy', 'crlf', 'pause',
        'format', 'sprintf', 'printf', 'print', 'write', 'writeln',
        // Top-level Synchronet globals doors and load()'d helpers reach for:
        'exit', 'mswait', 'random', 'time', 'ascii', 'ascii_str',
        'truncsp', 'strip_ctrl', 'word_wrap', 'strftime',
        // argv/argc — DSR reads argv[0] for an image path argument
        'argv', 'argc',
        // Also expose all the SYS_* / BBS_OPT_* / LOG_* / KEY_* / colour
        // constants — let the global object inherit them via for-in below.
    ];
    var g = (typeof global !== 'undefined' ? global : globalThis);
    for (var i = 0; i < names.length; i++) {
        try {
            if (typeof eval(names[i]) !== 'undefined') {
                g[names[i]] = eval(names[i]);
            }
        } catch (e) {}
    }
    // Sweep up the LOG_*, KEY_*, USER_*, SYS_* constants too
    var prefixes = ['LOG_', 'KEY_', 'USER_', 'SYS_', 'BBS_OPT_', 'SS_', 'CON_',
                    'ON_', 'P_', 'K_', 'CTRL_', 'NET_', 'LOGON_'];
    var allKeys = (typeof globalThis !== 'undefined') ? Object.keys(globalThis) : [];
    // Also pull from this scope by iterating Object.getOwnPropertyNames
    // of this function's surrounding closure isn't directly possible — but
    // assigning the names list above + the eval trick captures the most
    // commonly-needed ones. Constants from sbbsdefs.js etc are already
    // assigned to global by indirect-eval'd load().
})();

// === Execute the actual game ===
var _gameScript = process.argv[process.argv.length - 1];
process.stderr.write('[BBS] launching ' + _gameScript + '\n');
try {
    load(_gameScript);
    process.stderr.write('[BBS] script returned normally (no error)\n');
} catch(e) {
    // Print the error AND the JS stack — without the stack we can't see where
    // an undefined-symbol or type error originated and the user just sees
    // "Game ended" with nothing useful.
    process.stderr.write('[BBS] error: ' + (e && e.message ? e.message : e) + '\n');
    if (e && e.stack) process.stderr.write('[BBS] stack:\n' + e.stack + '\n');
    process.exit(1);
}
"""


def write_compat_script(game, user, node_number, bbs_name='ANetBBS'):
    """
    Write a customised synchronet_compat.js to a temp file and return its path.

    Args:
        game: Game model instance
        user: User model instance (may be None for admin test launches)
        node_number: Allocated node number
        bbs_name: BBS name string

    Returns:
        Absolute path to the generated .js file
    """
    # The caller may pass a SQLAlchemy User model (web path) OR a dict
    # (telnet/SSH/rlogin sessions store user as a dict). Helper handles both.
    def _u(field, default=None):
        if user is None:
            return default
        if isinstance(user, dict):
            return user.get(field, default)
        return getattr(user, field, default)

    username = _u('username') or 'Guest'
    display_name = _u('display_name') or username
    user_id = _u('id') or 0
    security_level = 255 if _u('is_admin') else 50
    login_count = _u('login_count') or 0

    # Resolve to absolute paths — the JS wrapper templates these into
    # `js.exec_dir` / `js.startup_dir` which doors then concatenate with
    # filenames like 'settings.ini'. Node's fs.readFileSync resolves
    # relative paths against process.cwd(); when the DB stores a relative
    # script path (e.g. 'doors/sbbs/dsr/dsr.js'), the resulting open
    # silently fails and DSR reads an empty object back from iniGetObject.
    _bbs_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    def _absroot(p):
        return p if os.path.isabs(p) else os.path.abspath(
            os.path.join(_bbs_root, p))
    exec_dir = _absroot(game.synchronet_exec_dir or '/tmp/sbbs/exec')
    game_dir = _absroot(os.path.dirname(
        game.synchronet_script_path or '/tmp') or '/tmp')
    data_dir = os.path.join(os.path.dirname(exec_dir), 'data')
    text_dir = os.path.join(os.path.dirname(exec_dir), 'text')
    mods_dir = os.path.join(os.path.dirname(exec_dir), 'mods')
    # Stubs dir ships with anetbbs (provides sbbsdefs.js etc) so doors
    # `load("sbbsdefs.js")` works without a full Synchronet install.
    stubs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sbbs_stubs')

    def _q(s):
        # The local _js_str() escapes special chars but does NOT add surrounding
        # quotes (unlike json.dumps). The placeholders in the template are
        # ALREADY inside JS string-literal single-quotes, so we just want the
        # escaped value as-is. The previous [1:-1] slicing was wrong (it
        # assumed _js_str added quotes) and stripped the first & last char of
        # actual paths, which produced "/home/.../botwars" -> "home/.../botwar"
        # (missing leading / and trailing s) → load() couldn't find anything.
        return _js_str(s)

    script = _COMPAT_TEMPLATE \
        .replace('{USERNAME}', _q(username)) \
        .replace('{DISPLAY_NAME}', _q(display_name)) \
        .replace('{USER_ID}', str(user_id)) \
        .replace('{SECURITY_LEVEL}', str(security_level)) \
        .replace('{LOGIN_COUNT}', str(login_count)) \
        .replace('{BBS_NAME}', _q(bbs_name)) \
        .replace('{NODE_NUMBER}', str(node_number)) \
        .replace('{EXEC_DIR}', _q(exec_dir)) \
        .replace('{GAME_DIR}', _q(game_dir)) \
        .replace('{STUBS_DIR}', _q(stubs_dir)) \
        .replace('{DATA_DIR}', _q(data_dir)) \
        .replace('{TEXT_DIR}', _q(text_dir)) \
        .replace('{MODS_DIR}', _q(mods_dir))

    tmp = tempfile.NamedTemporaryFile(
        mode='w',
        suffix='_synchronet_compat.js',
        delete=False,
        prefix='anetbbs_',
    )
    tmp.write(script)
    tmp.close()
    return tmp.name


def _js_str(value):
    """Escape a Python string for safe embedding in a JS string literal."""
    return value.replace('\\', '\\\\').replace("'", "\\'").replace('"', '\\"')
