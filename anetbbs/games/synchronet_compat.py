# anetbbs/games/synchronet_compat.py
"""
Synchronet JS Compatibility Layer for ANetBBS

Generates a Node.js wrapper script that provides Synchronet's JS API objects
so that Synchronet .js door games can run on ANetBBS via PTY + xterm.js.
"""
import os
import sys
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

// === SpiderMonkey-only Error.prototype.fileName getter ===
// tw2.js's startup_path probe does:
//
//     try { throw barfitty.barf(barf) } catch(e) { startup_path = e.fileName }
//
// In Synchronet's JS runtime, caught exceptions expose `.fileName`
// pointing at the source file where the throw occurred. Node has no
// such property — `.stack` is the only source of file paths. Add a
// getter on `Error.prototype` that parses `this.stack` and returns
// the file path of the most recent frame, so the idiom works.
Object.defineProperty(Error.prototype, 'fileName', {
    configurable: true,
    get: function () {
        try {
            var lines = String(this.stack || '').split('\n');
            for (var i = 1; i < lines.length; i++) {
                // Frames look like: "    at Foo (path/to/file.js:line:col)"
                // or                 "    at path/to/file.js:line:col"
                var m = lines[i].match(/\(([^()]+):\d+:\d+\)\s*$/) ||
                        lines[i].match(/at\s+([^\s()]+):\d+:\d+\s*$/);
                if (m && m[1]) return m[1];
            }
        } catch (e) {}
        return undefined;
    }
});

// === toSource() polyfill ===
// SpiderMonkey provides obj.toSource() returning a string that, when
// eval'd, reproduces the object. Synchronet doors use the idiom
//     var copy = eval(template.toSource());
// to deep-clone defaults (e.g. tw2's DefaultSector, DefaultCabal). V8/Node
// has no toSource. JSON.stringify gives valid JS for plain data-objects
// when wrapped in parens, which covers every use we've seen in xtrn doors.
// Functions and circular refs aren't supported — none of the upstream
// templates we ship use those, so this is sufficient.
if (typeof Object.prototype.toSource !== 'function') {
    Object.defineProperty(Object.prototype, 'toSource', {
        configurable: true, writable: true, enumerable: false,
        value: function () {
            try { return '(' + JSON.stringify(this) + ')'; }
            catch (e) { return '({})'; }
        }
    });
}
if (typeof Array.prototype.toSource !== 'function') {
    Object.defineProperty(Array.prototype, 'toSource', {
        configurable: true, writable: true, enumerable: false,
        value: function () {
            try { return JSON.stringify(this); }
            catch (e) { return '[]'; }
        }
    });
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
//
// Also buffers: a single "logical" screen update (e.g. one frame.js
// Display.cycle() flush) is frequently dozens of separate tiny
// console.write() calls (one gotoxy+char per changed cell) rather than
// one combined write. Sending each as its own write()/syscall means a
// real remote connection with any latency can deliver and paint a
// screen update only PARTIALLY complete -- confirmed as the leading
// explanation for Synchronetris's live "bleeding blocks" reports after
// extensive log analysis ruled out every application-level ordering bug
// (every write was provably correct and in the right order; the gap is
// in how many separate pieces it gets split into over the wire, not
// what gets sent). Buffering everything written during one burst of
// synchronous JS execution and flushing it as ONE combined write closes
// that window without changing what's sent, only how it's packaged.
//
// CRITICAL: deferring the actual write blindly (e.g. always via
// process.nextTick) would break the extremely common "print a prompt,
// then block reading a key" pattern -- readSync() is a real blocking
// syscall that does NOT yield to the event loop, so a nextTick-deferred
// prompt would never actually reach the screen before the read blocks,
// leaving the user staring at nothing. _flushStdoutNow() is called
// explicitly, synchronously, at every blocking stdin-read call site in
// this file (see _readKey, _readLine, Queue.prototype.read/poll) to
// guarantee anything buffered lands before any read can block.
var _stdoutPending = [];
var _stdoutFlushScheduled = false;
function _flushStdoutNow() {
    _stdoutFlushScheduled = false;
    if (_stdoutPending.length === 0) return;
    var chunks = _stdoutPending;
    _stdoutPending = [];
    var buf = (chunks.length === 1) ? chunks[0][0] : Buffer.concat(chunks.map(function (c) { return c[0]; }));
    var origWriteFn = _stdoutPatchedOrigWrite;
    origWriteFn(buf, function () {
        for (var i = 0; i < chunks.length; i++) {
            if (chunks[i][1]) chunks[i][1]();
        }
    });
}
var _stdoutPatchedOrigWrite = process.stdout.write.bind(process.stdout);
(function _patchStdoutForCP437() {
    process.stdout.write = function(chunk, encoding, cb) {
        if (typeof encoding === 'function') { cb = encoding; }
        var buf;
        if (typeof chunk === 'string') {
            // Translate Synchronet Ctrl-A colour codes here so callers that
            // bypass console.* (dd_lightbar_menu, printfile, raw load()'d
            // helpers) still get colours instead of leaking ␁W glyphs.
            if (chunk.indexOf('\x01') >= 0 && typeof _translateCtrlA === 'function') {
                chunk = _translateCtrlA(chunk);
            }
            buf = Buffer.from(chunk, 'binary');
        } else if (Buffer.isBuffer(chunk)) {
            // bbs.menu reads files as 'binary' (string) but other helpers
            // may pass Buffer directly. Translate Ctrl-A in raw bytes too.
            if (chunk.indexOf(0x01) >= 0 && typeof _translateCtrlA === 'function') {
                var s = chunk.toString('binary');
                s = _translateCtrlA(s);
                buf = Buffer.from(s, 'binary');
            } else {
                buf = chunk;
            }
        } else {
            buf = Buffer.from(String(chunk), 'binary');
        }
        _stdoutPending.push([buf, cb]);
        if (!_stdoutFlushScheduled) {
            _stdoutFlushScheduled = true;
            process.nextTick(_flushStdoutNow);
        }
        return true;
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
    _flushStdoutNow();
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

// Synchronet maps terminal CSI / SS3 escape sequences (real arrow/
// Home/End/PgUp/PgDn/Ins/Del keys) to single-byte control codes
// (Ctrl-^=Up, Ctrl-J=Down, etc. -- see key_defs.js) before a door ever
// sees them. Doors (and shared libraries like tree.js's lightbar
// navigation) compare the RESULT of console.inkey()/getkey() against
// those single-byte KEY_* constants, never against the raw \x1b[A wire
// bytes. Originally only getkey() did this translation -- inkey() just
// returned the bare first byte, so a real terminal's arrow-key press
// (which always starts with a lone \x1b) looked EXACTLY like a plain
// Escape keypress to any door using inkey() (the more common of the
// two -- e.g. chickendelivery.js's own main loop, and tree.js's
// getcmd()). Confirmed live: pressing an arrow key during a real
// Chicken Delivery session acted exactly like pressing Escape (its
// quit-confirmation popup fired), and the menu's up/down navigation
// didn't respond to arrows at all -- both symptoms of the same root
// cause. Factored out so both getkey() and inkey() share one
// implementation instead of only one of them being correct.
function _resolveKey(k) {
    if (k !== '\x1b') return k;
    var b = _readKey(50);
    if (!b) return k;       // bare ESC
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
}

function _readLine(maxlen, initial) {
    var buf = initial || '';
    if (buf) process.stdout.write(buf);
    while (buf.length < (maxlen || 80)) {
        try {
            var b = Buffer.alloc(1);
            _flushStdoutNow();
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
    // Real Synchronet console.clearline() (js_console.cpp) -- distinct
    // from cleartoeol() (cursor-to-end-of-line only): clears the
    // ENTIRE current line without moving the cursor (ANSI CSI 2K, vs
    // cleartoeol's bare CSI K which defaults to the 0-parameter
    // "cursor to end" form). Was completely missing from this shim --
    // found live auditing Star Stocks: `console.gotoxy(1,24);
    // console.clearline();` in processSelection(), the core "build an
    // outpost" gameplay flow, not an edge case.
    clearline:  function() { process.stdout.write('\033[2K'); },
    // Real Synchronet's console.gotoxy (confirmed against js_console.cpp's
    // js_gotoxy) accepts EITHER a single {x,y} object OR two separate
    // numbers -- inputline.js's own gotoxy() helper calls the object
    // form (console.gotoxy(position)), which this shim never supported,
    // producing a garbled "\x1b[undefined;[object Object]H" escape
    // sequence instead of a real cursor move.
    gotoxy:     function(x,y) {
        if (x !== null && typeof x === 'object') {
            y = x.y;
            x = x.x;
        }
        process.stdout.write('\033[' + y + ';' + x + 'H');
    },
    home:       function() { process.stdout.write('\033[H'); },
    // Real Synchronet's `mode` argument (confirmed against js_console.cpp,
    // which threads it straight through to the real terminal-level
    // inkey()/getkey()) is a bitmask including K_UPPER -- force the
    // returned key uppercase. This shim silently discarded `mode`
    // entirely, so any door comparing a K_UPPER'd inkey()/getkey()
    // result against an uppercase literal (e.g. Synchronetris's real
    // "Game Over" screen: `while (console.inkey(K_UPPER|...) != "Q");`)
    // could never match on a real lowercase keypress -- confirmed live:
    // pressing Q at Game Over did nothing, a total softlock, since the
    // loop just kept comparing a lowercase "q" against "Q" forever.
    getkey:     function(mode, timeout) {
        var k = _resolveKey(_readKey(timeout || 0));
        if (mode & K_UPPER) k = k.toUpperCase();
        return k;
    },
    // Synchronet's signature: console.getstr(maxlen, mode) — first arg is the
    // max length (a number), NOT a prompt. Old version wrote arg1 to stdout
    // which crashed when callers passed a number (Bot Wars: console.getstr(2)).
    //
    // Real Synchronet also has a genuine 3-arg overload,
    // console.getstr(str, maxlen, mode), where `str` (a STRING, not a
    // number) is pre-filled/editable initial text -- confirmed real,
    // not a door bug: Star Trek's setup() calls
    // `console.getstr("USS ", 30, K_LINE|K_EDIT)` to let the player
    // type a ship name after a fixed "USS " prefix. Without this, the
    // first arg would fail the `typeof maxlen !== 'number'` check
    // above, silently falling back to maxlen=80 with the "USS "
    // prefix dropped entirely and the real intended maxlen (30, the
    // door's actual SECOND argument) discarded too -- not a crash,
    // but a real behavioral gap (no prefix shown, wrong length limit).
    // Real crash^Wmisbehavior found live on Jerry's Pi3 playing
    // Thirstyville: typing "160" (meaning $1.60) into the price
    // prompt (`console.getstr("0.00", 8, K_EDIT|K_LINE)`) produced
    // "0.00160" -- the OLD behavior below (documented right above this
    // comment before the fix): the prefix was written once and a
    // FRESH read started after it with no way to backspace into it,
    // so typed digits always landed appended at the end, no matter
    // what. That's the correct behavior for a genuinely fixed,
    // non-editable prefix (Star Trek's "USS " ship-name prompt, which
    // stays working identically below since nothing changes for a
    // caller that never backspaces past their own typed suffix), but
    // K_EDIT's real, documented meaning (sbbsdefs.js: "Edit string
    // passed") is that the WHOLE string is a live, backspace-into-able
    // buffer -- exactly what Thirstyville's price/quantity prompts
    // need (pre-filled "0.00" or a previous order qty that the player
    // is meant to actually overwrite). Now routes K_EDIT calls through
    // _readLine's own initial-buffer support (backspace already
    // correctly deletes from any buffer contents, regardless of
    // whether they were pre-filled or freshly typed) instead of bare
    // string concatenation.
    getstr:     function(strOrMaxlen, maxlenOrMode, mode) {
        var initial = '';
        var maxlen = 80;
        var editable = false;
        if (typeof strOrMaxlen === 'string') {
            initial = strOrMaxlen;
            maxlen = (typeof maxlenOrMode === 'number') ? maxlenOrMode : 80;
            editable = !!(mode & K_EDIT);
        } else {
            maxlen = (typeof strOrMaxlen === 'number') ? strOrMaxlen : 80;
        }
        if (editable) return _readLine(Math.max(maxlen, initial.length), initial);
        if (initial) process.stdout.write(initial);
        return initial + _readLine(Math.max(0, maxlen - initial.length));
    },
    // Real Synchronet signature (js_console.cpp's js_getnum, confirmed
    // against the real C source): console.getnum(maxnum, dflt) reads a
    // number, returning `dflt` (default 0) if the user just presses
    // Enter with nothing typed. Real Synchronet's underlying
    // sbbs->getnum() also digit-filters and range-clamps interactively
    // as the user types -- not replicated here since every current
    // caller (Bubble Boggle's changeDate()) already does its own
    // separate range check on the returned value.
    getnum:     function(maxnum, dflt) {
        var s = _readLine(10);
        if (!s) return (typeof dflt === 'number') ? dflt : 0;
        var n = parseInt(s, 10);
        return isNaN(n) ? ((typeof dflt === 'number') ? dflt : 0) : n;
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
    // See getkey()'s own comment just above -- same missing K_UPPER
    // handling, same real bug class.
    inkey:      function(mode, timeout) {
        var k = _resolveKey(_readKey(timeout || 0));
        if (mode & K_UPPER) k = k.toUpperCase();
        return k;
    },
    // Synchronet console.getkeys(keys, maxnum, mode) is actually
    // multi-purpose in the real implementation (a numeric-input mode
    // as well as a restricted-keyset mode -- see js_console.cpp's
    // js_getkeys) -- only the keyset form is implemented here, since
    // that's the only one any currently-bundled door calls (e.g.
    // chickendelivery.js's `console.getkeys("YN")` for its quit-
    // confirmation popup). Blocks until a key matching `keys`
    // (case-insensitively) is pressed, returns it uppercased --
    // matches K_UPPER, the real default mode.
    getkeys:    function(keys, maxnum, mode) {
        var allowed = String(keys || '').toUpperCase();
        if (!allowed) return '';
        for (;;) {
            var k = _readKey(0).toUpperCase();
            if (allowed.indexOf(k) !== -1) return k;
        }
    },
    // strlen — count visible characters (strip ANSI escape sequences AND
    // Synchronet's own native \x01-prefixed Ctrl-A color codes -- e.g.
    // \x01y for bright yellow, matching the same \x01(.) pairing
    // _translateCtrlA already handles for actual output elsewhere in
    // this file). Missing the Ctrl-A stripping here was a real bug
    // found live: Synchronetris's own getMsgFrame() centers a message
    // frame via `Math.floor((frame.width - console.strlen(line)) / 2)`
    // -- a line with several \x01 codes (e.g.
    // "\1n\1yPress [\1hQ\1n\1y] to exit") had its computed width
    // inflated by the invisible code bytes, past the actual frame
    // width, producing a genuinely negative x coordinate and crashing
    // `new Frame(x, ...)` on the door's own real "Game Over" screen.
    strlen:     function(str) {
        var s = String(str || '');
        // Strip CSI sequences (\x1b[...m, \x1b[...H, etc) and bare ESC sequences
        s = s.replace(/\x1b\[[0-9;]*[A-Za-z]/g, '').replace(/\x1b./g, '');
        s = s.replace(/\x01./g, '');
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
    // Real Synchronet's console.charset (js_console.cpp) -- the active
    // terminal character set as a string ("CP437", "UTF-8", etc), read
    // by doors picking a charset-specific modopts.ini section (e.g.
    // Minesweeper's own modopts.js: `modname + ':charset=' +
    // console.charset.toLowerCase()`). Missing entirely crashed with
    // "Cannot read property 'toLowerCase' of undefined" the moment a
    // door read it -- found live running Minesweeper. ANetBBS is
    // CP437 throughout, so a static "CP437" is correct here, not a
    // live per-session value.
    charset: 'CP437',
    autoterm: 0x1E,        // ANSI + COLOR + RIP + PETSCII bits — claim everything
    // Real Synchronet's console.attributes is a live property: ASSIGNING
    // to it immediately changes the terminal's active color (that's the
    // documented, real convention doors use to set color -- e.g.
    // frame.js's Display.__drawChar__ does `console.attributes = attr;`
    // before writing each character). A plain data property here would
    // silently accept the assignment and do nothing -- confirmed live:
    // Chicken Delivery loaded and played correctly but rendered entirely
    // in monochrome, every character painted with whatever the terminal's
    // last real SGR state happened to be, since nothing ever told it to
    // change. Backed by _attributesValue so reads (`var save =
    // console.attributes`, e.g. chickendelivery.js's own init()/cleanUp())
    // still return the real current value, not just fire-and-forget the
    // ANSI code with nothing to read back.
    _attributesValue: 7,
    get attributes() { return this._attributesValue; },
    set attributes(v) {
        this._attributesValue = (typeof v === 'number') ? v : 7;
        process.stdout.write(this.ansi(this._attributesValue));
    },
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
    // console.status — real Synchronet bitfield (CON_* flags from
    // sbbsdefs.js, e.g. CON_MOUSE_CLK_PASSTHRU/CON_MOUSE_REL_PASSTHRU).
    // Minesweeper's mouse_enable() does `console.status |= mouse_passthru`
    // / `&= ~mouse_passthru` and saves/restores it via js.on_exit. This
    // shim never actually enables real terminal-side xterm mouse
    // tracking (no escape code anywhere turns it on -- the one door path
    // that would, cterm/ansiterm's graphics mode, is itself gated behind
    // a cterm_version this shim reports as too old to reach, so it's
    // never sent), so a real terminal never emits mouse byte sequences
    // in the first place and this bitfield has no behavior to back —
    // just remember whatever value doors read/write, same treatment as
    // ctrlkey_passthru above.
    status: 0,
    // console.mouse_mode — same story as status above: real Synchronet
    // uses this to toggle terminal mouse-reporting escape codes; this
    // shim has no real mouse wire protocol to drive, so it's a plain
    // read/write value doors can save (`orig_mouse = console.mouse_mode`)
    // and restore on exit without erroring.
    mouse_mode: 0,
    // console.creturn() — real Synchronet: writes a bare carriage return
    // (cursor to column 1, no linefeed), distinct from crlf(). Used by
    // Minesweeper between redraws of the same status line.
    creturn: function() { process.stdout.write('\r'); },
    // console.clear_hotspots() — real Synchronet clears any mouse
    // click-regions registered via console.add_hotspot(). This shim
    // never implements hotspot registration (no bundled door calls
    // add_hotspot; Minesweeper only ever calls clear_hotspots, never
    // add_hotspot), so there is nothing to clear — safe no-op.
    clear_hotspots: function() {},
    // console.getbyte(timeout) — real Synchronet: reads ONE raw,
    // untranslated byte (0-255) directly from input, or -1 if none
    // arrives within `timeout` ms. Distinct from getkey/inkey, which
    // resolve multi-byte ANSI sequences into KEY_* constants — getbyte
    // is for callers doing their own low-level framing (Minesweeper's
    // read_apc() reassembles a SyncTERM APC response byte-by-byte). Only
    // reachable when console.cterm_version >= 1316
    // (cterm_version_supports_copy_buffers); this shim reports 1190
    // (see cterm_version below), so that call path is never actually
    // exercised here — implemented to real semantics regardless, for
    // correctness and any future caller.
    getbyte: function(timeout) {
        var k = _readKey(timeout || 0);
        if (k === '') return -1;
        return k.charCodeAt(0) & 0xFF;
    },
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
    // Real ANetBBS user data (User.date_of_birth), wired in for
    // bbs.compare_ars()'s AGE check -- see that function's own
    // comment. Empty string when unknown/unset, which AGE checks
    // deliberately treat as failing rather than passing.
    birthdate: '{USER_BIRTHDATE}',
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
    // Real Synchronet property (js_user.cpp's USER_PROP_IS_SYSOP,
    // confirmed as `security.level >= 90`, the stock SYSOP ARS
    // threshold) -- was completely missing from this shim. Found
    // auditing Good Time Trivia: `doSysopMenu()` starts with
    // `if (!user.is_sysop) return;` -- with this undefined, `!undefined`
    // is true, so the real sysop's own admin menu (clear scores,
    // remove a player/BBS from the shared scoreboard) silently did
    // nothing no matter who was logged in. Not door-specific -- any
    // door gating sysop-only features on this real property had the
    // same silent lockout.
    is_sysop: {SECURITY_LEVEL} >= 90,
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
    // Real Synchronet property (confirmed against js_user.cpp's
    // USER_PROP_IPADDR / "ip_address") -- json-chat.js's JSONChat
    // constructor reads this off a User instance to build a Nick.
    ip_address: '{USER_IP}',
};

// Real Synchronet global constructor -- `new User(number)` looks up
// ANY user's record by number (js_user.cpp). This compat shim is
// single-session (one Node process per door session, no live
// cross-user database access -- the same constraint already
// documented for user.stats.*/system.username() elsewhere in this
// file), so this always resolves to the current session's own `user`
// object regardless of the number requested -- correct for every real
// call site found so far (json-chat.js's JSONChat.connect() always
// passes the CURRENT user's own number), and a safe, honest
// simplification for any future caller (matches the same "returns
// current-user-shaped data regardless of the argument" precedent
// system.username(n) already established).
function User(number) { return user; }

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
    // Real Synchronet bbs.compare_ars(arsString) (js_bbs.cpp) -- was
    // completely missing from this shim. Found live bundling Good Time
    // Trivia: its qa/dirty_minds.qa category carries a real "AGE 18"
    // ARS restriction, and getQACategoriesAndFilenames() calls
    // `bbs.compare_ars(sectionARS)` unconditionally for any category
    // with an ARS string set -- "TypeError: bbs.compare_ars is not a
    // function" the first time a real player tried to play at all
    // (question categories are enumerated before the menu, not lazily
    // per-category).
    //
    // Full Synchronet ARS grammar (LEVEL/AGE/GROUP/FLAG/EXEMPT/REST,
    // boolean AND/OR/NOT combinations) is out of scope -- this handles
    // the common real-world single-condition cases this project
    // actually has real backing data for: SYSOP and LEVEL n against
    // user.is_sysop/user.security.level (both real, wired from the
    // actual logged-in ANetBBS user), and AGE n against the real
    // user's actual date_of_birth (User.date_of_birth, newly wired
    // in via {USER_BIRTHDATE} below -- previously the `user.birthdate`
    // field was a hardcoded fake '1990-01-01' that would have silently
    // passed every AGE check regardless of who was actually logged
    // in). AGE is deliberately conservative: an unknown/unset
    // birthdate FAILS the check rather than passing it, since this is
    // gating actual adult content, not a cosmetic feature -- the
    // opposite of this shim's usual "permissive by default" stance for
    // properties with no real backing data, and deliberate here.
    // Any other/unrecognized token in the ARS string is ignored
    // (treated as satisfied) rather than aborting the whole check,
    // matching this shim's general "don't block real users on an ARS
    // feature we can't fully evaluate" philosophy for anything we
    // don't have real data for.
    compare_ars: function (arsString) {
        var s = String(arsString == null ? '' : arsString).trim();
        if (!s) return true;
        var tokens = s.split(/\s+/);
        var result = true;
        for (var i = 0; i < tokens.length; i++) {
            var tok = tokens[i].toUpperCase();
            if (tok === 'SYSOP') {
                result = result && !!user.is_sysop;
            } else if (tok === 'LEVEL' && i + 1 < tokens.length && /^\d+$/.test(tokens[i+1])) {
                result = result && (user.security.level >= parseInt(tokens[++i], 10));
            } else if (tok === 'AGE' && i + 1 < tokens.length && /^\d+$/.test(tokens[i+1])) {
                var minAge = parseInt(tokens[++i], 10);
                var bday = String(user.birthdate || '');
                if (!/^\d{4}-\d{2}-\d{2}$/.test(bday)) {
                    result = false;
                } else {
                    var parts = bday.split('-').map(Number);
                    var birth = new Date(parts[0], parts[1] - 1, parts[2]);
                    var now = new Date();
                    var age = now.getFullYear() - birth.getFullYear();
                    var m = now.getMonth() - birth.getMonth();
                    if (m < 0 || (m === 0 && now.getDate() < birth.getDate())) age--;
                    result = result && (age >= minAge);
                }
            }
            // Unrecognized tokens (GROUP/FLAG/EXEMPT/REST/etc, and any
            // argument already consumed above) are silently skipped.
        }
        return result;
    },
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

// Real Synchronet's Socket class is a native object with a handful of
// static address-family constants directly on the constructor
// (confirmed by sockdefs.js's own real, unmodified source: `if
// (Socket.PF_INET !== undefined) var PF_INET = Socket.PF_INET;` and
// three more just like it). This project deliberately never
// implements a real Socket -- there's no synchronous TCP primitive in
// Node, and both json-client.js and http.js's own shim replacements
// exist specifically to avoid needing one (see those files' own
// docstrings) -- but sockdefs.js unconditionally reads `Socket.PF_INET`
// at load time regardless of whether anything ever constructs a real
// socket, so any door merely loading that file (a very standard
// pattern for anything network-adjacent, e.g. Jeopardized's own
// `require('sockdefs.js', 'SOCK_STREAM')`) crashed immediately with
// "Socket is not defined" before ever reaching its own logic -- no
// prior bundled door happened to load sockdefs.js, so this never
// surfaced until now. Just enough of a stub (the 4 static constants
// sockdefs.js actually reads, standard POSIX values used by every
// real socket API including Synchronet's own C source) to let that
// file load without crashing -- not a real, constructible Socket.
var Socket = { PF_INET: 2, PF_INET6: 10, AF_INET: 2, AF_INET6: 10 };

// === system object ===
var system = {
    // Real Synchronet global (js_system.cpp's SYS_PROP_TIMER, backed by
    // xp_timer() -- confirmed against the real C source, not guessed):
    // a monotonic, continuously-advancing clock in FRACTIONAL SECONDS
    // (CLOCK_MONOTONIC under the hood), read fresh on every access, NOT
    // a value computed once at startup. This is the primitive real
    // Synchronet doors use for all animation/movement timing --
    // sprite.js's own movement gating is `system.timer - this.lastMove
    // > this.ini.speed` throughout. Missing entirely meant
    // `system.timer` read as `undefined` on every access, so that
    // comparison was always `NaN > speed` (always false) -- sprites
    // and enemies never moved at all, confirmed live: menu navigation
    // and the HUD countdown (driven by a completely separate timer,
    // event-timer.js's Timer class) both worked fine, but gameplay
    // itself was a frozen screen. A getter (not a plain number) is
    // required -- code re-reads system.timer many times per frame
    // expecting a fresh value each time, the exact same class of bug
    // as console.attributes needing a real setter.
    get timer() { return Number(process.hrtime.bigint()) / 1e9; },
    name:       '{BBS_NAME}',
    operator:   'Sysop',
    nodes:      4,
    platform:   'Unix',
    version:    'ANetBBS Compat',
    // tw2's input.js (CheckNode) and other multi-node doors read
    // system.node_list[bbs.node_num-1].misc/.status to detect changes
    // pushed by another node. We're single-BBS, single-node — pre-fill
    // with inert stubs so the property access works without crashing.
    node_list:  [
        {misc:0, status:0}, {misc:0, status:0},
        {misc:0, status:0}, {misc:0, status:0}
    ],
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
    // Real Synchronet's telegram/node-message system (a short instant
    // message left for a user on another node, flagged via
    // node_list[n].misc's NODE_MSGW/NODE_NMSG bits -- confirmed in
    // nodedefs.js). Since node_list above is a static, never-updated
    // stub (single-BBS, single-node -- those bits never actually get
    // set), real doors checking `node_list[...].misc & NODE_MSGW`
    // before calling these should never actually reach them in
    // practice -- defensive stubs anyway, matching this project's
    // "never crash on a technically-reachable path" standard, rather
    // than assuming that guard is airtight for every caller.
    get_telegram: function (userNum) { return ''; },
    get_node_message: function (nodeNum) { return ''; },
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
    // Synchronet's system.timestr(time_t) formats a Unix timestamp using
    // the system's sys_timestr_default — the SBBS default is "%m/%d/%y %H:%M".
    // tw2's ports.js calls this for "last visited" displays.
    timestr: function(t) {
        if (t === undefined || t === null) t = Math.floor(Date.now() / 1000);
        var d = new Date(Number(t) * 1000);
        if (isNaN(d.getTime())) return '';
        var p = function(n) { return (n < 10 ? '0' : '') + n; };
        return p(d.getMonth() + 1) + '/' + p(d.getDate()) + '/'
             + p(d.getFullYear() % 100) + ' '
             + p(d.getHours()) + ':' + p(d.getMinutes());
    },
    // datestr(time_t) — date-only variant. Same SBBS default minus the clock.
    datestr: function(t) {
        if (t === undefined || t === null) t = Math.floor(Date.now() / 1000);
        var d = new Date(Number(t) * 1000);
        if (isNaN(d.getTime())) return '';
        var p = function(n) { return (n < 10 ? '0' : '') + n; };
        return p(d.getMonth() + 1) + '/' + p(d.getDate()) + '/'
             + p(d.getFullYear() % 100);
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
    // Not a real Synchronet js.* property -- internal to this shim only,
    // same treatment as js.stubs_dir above. MsgBase (see its own class
    // definition) shells out to this via spawnSync.
    msgbase_bridge: '{MSGBASE_BRIDGE}',
    python_bin:     '{PYTHON_BIN}',
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
    // Real Synchronet global (js_global.cpp's js_flatten_string) --
    // an internal SpiderMonkey perf hint that forces a "rope" string
    // (built from repeated concatenation) into one flat buffer; has
    // no JS-observable effect of its own. Gap found live: our own
    // sbbs_stubs/http.js (copied verbatim from the real vendored
    // source) calls `js.flatten_string(this.body)` in ReadBody() --
    // crashed with "js.flatten_string is not a function" the first
    // time any door actually exercised a real HTTP response body
    // (Jeopardized's func.js answer-checking call).
    flatten_string: function (str) { return str; },
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
// Real crash found live bundling Good Time Trivia: `msg_area.sub` was
// completely absent (only `.grp` existed) -- any door code checking
// `msg_area.sub.hasOwnProperty(code)` or `msg_area.sub[code]` (a
// normal, defensive "does this sub-board exist" check, not an exotic
// call) threw "Cannot read properties of undefined (reading
// 'hasOwnProperty')" instead of correctly reporting "no sub-boards
// configured" the way a real empty stub should. This shim
// deliberately doesn't wire up real message-base sub-board data
// (documented architecture decision, docs/15-synchronet-compat.md) --
// but an empty object is the honest way to represent that, not a
// missing property that crashes the first thing that touches it.
var msg_area  = { grp: {}, sub: {} };

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
            _flushStdoutNow();
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
// Real Synchronet global (js_global.cpp's js_cfgfname, backed by
// iniFileName() in xpdev/ini_file.c -- confirmed against the real C
// source, not guessed): resolves a per-machine config override before
// falling back to the plain path/filename -- tries
// "path/name.hostname.domain.ext", then "path/name.hostname.ext",
// then plain "path/name.ext". Bubble Boggle's boggle.js uses this for
// its own server.ini: `new File(file_cfgname(root, "server.ini"))`.
// The hostname-override mechanism is a real but rarely-used sysop
// feature (a per-machine config variant) -- correctness matters more
// than optimizing for the common case, since silently resolving to
// the wrong file would be a confusing, hard-to-diagnose bug.
function file_cfgname(path, fname) {
    var dir = String(path == null ? '' : path);
    if (dir && !/[\/\\]$/.test(dir)) dir += '/';
    var base = String(fname == null ? '' : fname);
    var ext = '';
    var dot = base.lastIndexOf('.');
    if (dot > 0) { ext = base.slice(dot); base = base.slice(0, dot); }
    try {
        var hostname = _node_require('os').hostname();
        var candidate = dir + base + '.' + hostname + ext;
        if (_fs.existsSync(candidate)) return candidate;
        var short = hostname.split('.')[0];
        if (short !== hostname) {
            candidate = dir + base + '.' + short + ext;
            if (_fs.existsSync(candidate)) return candidate;
        }
    } catch (e) {}
    return dir + base + ext;
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
function file_touch(p, atime, mtime) {
    // Synchronet's file_touch updates the mtime (and creates the file
    // if missing). tw2's LoadPlayer touches data/user/NNNN.tw2 to mark
    // the player record as live. Use utimesSync; create-on-missing.
    try {
        var t = mtime ? new Date(mtime * 1000) : new Date();
        var a = atime ? new Date(atime * 1000) : t;
        try { _fs.utimesSync(p, a, t); }
        catch (e) {
            // File doesn't exist — create empty + retry.
            try { _fs.mkdirSync(_path.dirname(p), {recursive: true}); } catch (_) {}
            _fs.closeSync(_fs.openSync(p, 'a'));
            _fs.utimesSync(p, a, t);
        }
        return true;
    } catch (e) { return false; }
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
// `sleep()` -- real Synchronet global, entirely missing from this shim
// until found live bundling Minesweeper (`ReferenceError: sleep is not
// defined`, its own show_image()/play() pacing calls). Real vendored
// sbbs_stubs/sbbslist_lib.js (`sleep(1000)`) and Minesweeper's own
// calls (`sleep(options.boom_delay)` etc, defaults 500-1500) both
// consistently pass millisecond-scale integers, not fractional
// seconds -- an 1000-SECOND (16+ minute) pause between an explosion
// and its reveal makes no sense for either door, so real Synchronet's
// sleep() evidently accepts milliseconds here despite the name reading
// like a seconds-based API. Implemented as a plain alias to the
// already-real mswait().
function sleep(ms) { return mswait(ms); }

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
    // Real bug found live bundling Minesweeper: open() itself never
    // touches the filesystem for a write-capable mode (the real write
    // only happens later, in whichever write/close/_writeIni method
    // actually runs) -- but none of those later writeFileSync calls
    // create missing parent directories either, so a door's very FIRST
    // save into a not-yet-existing directory (userprops.js's own
    // `data/user/<id>.ini`, a per-install path that genuinely doesn't
    // exist until something writes to it) always failed with ENOENT,
    // silently, every single time -- Minesweeper's own selector/
    // highlight/difficulty prefs, for instance, could never actually
    // persist. Real Synchronet's File API creates missing directories
    // on open-for-write, matching ordinary fopen()-with-mkdir-p
    // semantics doors are written to expect. Fixed once here (not at
    // each individual write call site) so every File-based save benefits,
    // not just this one door -- same reasoning as file_touch()'s own
    // mkdirSync a few hundred lines up.
    if (this._can_write) {
        try { _fs.mkdirSync(_path.dirname(this.name), {recursive: true}); }
        catch (e) {}
    }
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
        //
        // EXCEPT: a plain read-only open() of a file that simply doesn't
        // exist yet (ENOENT) is not a diagnosable problem -- it's the
        // completely normal, expected "no config saved yet" case every
        // real Synchronet door already handles gracefully by falling
        // back to defaults (confirmed live: Minesweeper's own modopts.js
        // lookup and winners.jsonl read both hit this on every fresh
        // install, forever, since a stock install never seeds sample
        // config/winner data). Real bug found live bundling Minesweeper:
        // this logged unconditionally, so a sysop's very first launch of
        // ANY door with no prior saved state produced a wall of scary-
        // looking "failed" messages for something that was never actually
        // wrong. Write-mode failures (this._can_write) still always log
        // -- after the File.open() mkdirSync fix a few lines up, those
        // should essentially never legitimately happen anymore, so one
        // showing up now is exactly the kind of real problem this
        // logging exists to surface.
        var _isBenignMissingRead = !this._can_write && e && e.code === 'ENOENT';
        if (!_isBenignMissingRead) {
            try {
                process.stderr.write('[BBS] File.open(' + JSON.stringify(this.name) +
                                     ', ' + JSON.stringify(mode) + ') failed: ' +
                                     (e && e.message ? e.message : e) + '\n');
            } catch(_) {}
        }
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
// Real Synchronet's File.eof is a PROPERTY, not a method -- same class
// of fix as .position/.length just above. A door checking `while
// (!dict.eof)` (Bubble Boggle's own dictionary scanner) against a
// bare function reference would always see it as truthy (a function
// object, not the boolean it's meant to be), so `!dict.eof` was
// always false and the loop would spin forever reading past EOF.
Object.defineProperty(File.prototype, 'eof', {
    get: function () { return this._pos >= this._content.length; },
    enumerable: true,
    configurable: true,
});
// rewind() -- seek back to the start of the file. Bubble Boggle's
// dictionary scanner calls this at the top of every lookup.
File.prototype.rewind = function() { this._pos = 0; return true; };
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
// Serialize the cached sections dict back into INI text and overwrite
// this._content. Real Synchronet's iniSetValue writes through to disk
// immediately, so flush to fs as well — doors like tw2's
// GameSettings_Save expect the change to be visible the next time the
// file is opened.
File.prototype._writeIni = function() {
    var s = this._iniCache || { '': {} };
    var out = '';
    var root = s[''] || {};
    var k;
    for (k in root) {
        out += k + ' = ' + root[k] + '\n';
    }
    for (var name in s) {
        if (name === '') continue;
        out += '\n[' + name + ']\n';
        for (k in s[name]) {
            out += k + ' = ' + s[name][k] + '\n';
        }
    }
    this._content = out;
    this._iniCacheLen = out.length;
    if (this._can_write) {
        try { _fs.writeFileSync(this.name, Buffer.from(out, 'binary')); }
        catch (e) {
            try { process.stderr.write('[BBS] iniWrite ' +
                JSON.stringify(this.name) + ' failed: ' + e + '\n'); } catch(_) {}
        }
    }
};
File.prototype.iniSetValue = function(section, key, value) {
    var s = this._parseIni();
    var skey = (section === null || section === undefined) ? '' : section;
    if (!s[skey]) s[skey] = {};
    s[skey][key] = (value === undefined || value === null) ? '' : String(value);
    this._writeIni();
    return true;
};
File.prototype.iniRemoveKey = function(section, key) {
    var s = this._parseIni();
    var skey = (section === null || section === undefined) ? '' : section;
    if (s[skey] && key in s[skey]) {
        delete s[skey][key];
        this._writeIni();
        return true;
    }
    return false;
};
File.prototype.iniRemoveSection = function(section) {
    var s = this._parseIni();
    if (section && s[section]) {
        delete s[section];
        this._writeIni();
        return true;
    }
    return false;
};
File.prototype.iniSetObject = function(section, obj) {
    if (!obj) return false;
    var s = this._parseIni();
    var skey = (section === null || section === undefined) ? '' : section;
    s[skey] = {};
    for (var k in obj) s[skey][k] = String(obj[k]);
    this._writeIni();
    return true;
};

// === load / require ===
// Cache loaded files to prevent infinite recursion (a file `require()`ing
// another file that `require()`s it back, or scripts that load themselves).
var _load_cache = {};
var _load_depth = 0;
// Real bug found live bundling Minesweeper: show_image() calls
// `var Graphic = load({}, "graphic.js");` on EVERY invocation (once
// per image shown -- welcome/mine/winner/loser/boom), not just once.
// The FIRST call executes the file fresh and correctly trusts its
// completion value (a real reference to globalThis.Graphic); every
// SUBSEQUENT call is a cache hit (no re-execution, so there's no
// fresh `result` to evaluate at all) and fell back to returning the
// bare `scope` wrapper object instead -- `new Graphic(...)` on the
// SECOND image shown threw "Graphic is not a constructor", even
// though the exact same call worked fine the first time. Remembers,
// per fullpath, the GLOBAL NAME a trusted completion value referred
// to (not the value itself, in case it's ever replaced) so a cache
// hit can re-derive the same real reference instead of only ever
// getting it right once per file per process.
var _load_result_name_cache = {};

// Some real Synchronet doors use SpiderMonkey/E4X's `for each (var x
// in y)` syntax (value iteration) in their OWN source, not just in
// vendored libraries -- Bubble Boggle's game.js (loaded via load()
// from boggle.js's own top-level code) has one real occurrence in
// storeRoundWinner(). A JS parse-time SyntaxError kills the ENTIRE
// file regardless of whether that branch ever executes. Doors stay
// byte-for-byte unmodified on disk (this project's own rule) --
// applied here, to EVERY file load() reads (which now includes a
// door's own top-level entry script too -- see door_runner.py's
// _build_command(), which runs it via `load(realScriptPath)` rather
// than concatenating it in literally, specifically so a door's own
// top-level vars are real globalThis properties visible to whatever
// IT load()s, not just module-wrapper-scoped local variables). Also a
// no-op safety net for the six vendored files already hand-fixed with
// Object.keys() iteration for readability (running this here too finds
// nothing left to rewrite in them). Rewrites just the `for (...)`
// header via a drop-in replacement so the loop body (braced or not) is
// never touched -- avoids brace-balancing entirely.
// Finds `function <name>(...) {` (tolerant of whitespace/newlines
// before the brace) and inserts `insertText` immediately before that
// SAME function's own closing brace -- located purely structurally,
// via balanced-brace scanning from the opening brace onward, never by
// matching any of the function's own body text. A no-op (returns
// `source` completely unchanged) if the function name isn't found at
// all or its braces don't balance, so this degrades safely rather
// than corrupting anything if a door's file changes in the future.
function _insertBeforeFunctionEnd(source, functionName, insertText) {
    var sigRe = new RegExp('function\\s+' + functionName + '\\s*\\([^)]*\\)\\s*\\{');
    var m = sigRe.exec(source);
    if (!m) return source;
    var i = m.index + m[0].length; // just past the opening brace
    var depth = 1;
    while (i < source.length && depth > 0) {
        if (source[i] === '{') depth++;
        else if (source[i] === '}') { depth--; if (depth === 0) break; }
        i++;
    }
    if (depth !== 0) return source;
    return source.slice(0, i) + insertText + source.slice(i);
}

// Symmetric twin of _insertBeforeFunctionEnd() above: same name+brace
// matching, but inserts right after the OPENING brace instead of
// right before the matching closing one -- i.e. as the very first
// statement in the function body, before any of the function's own
// code runs. Needed for guarding a crash that happens partway through
// a function's own body (appending code at the end can't help there,
// since it never runs until the function already returned normally --
// confirmed live: updateStatus()'s own
// `data.games[gameNumber].players[profile.name]` crashed on a still-
// corrupted record long before an end-of-function repair would ever
// have been reached).
function _insertAfterFunctionStart(source, functionName, insertText) {
    var sigRe = new RegExp('function\\s+' + functionName + '\\s*\\([^)]*\\)\\s*\\{');
    var m = sigRe.exec(source);
    if (!m) return source;
    var i = m.index + m[0].length; // just past the opening brace
    return source.slice(0, i) + insertText + source.slice(i);
}

// Real, confirmed bugs in specific bundled doors' OWN source, fixed
// here at load() time (applied to the in-memory code only, via
// _insertBeforeFunctionEnd() above) rather than by editing the door's
// file on disk -- so a sysop who drops in a fresh copy of the
// original door package still gets the fix automatically, and the
// door files themselves stay byte-for-byte unmodified (this project's
// own long-standing rule for every bundled door). Add future
// door-specific patches here following the same pattern: match on the
// loaded file's own path, then insert a call, never touch or store
// any of the door's own code text.
function _applyKnownDoorFixes(fullpath, source) {
    if (/[\/\\]sbbs_stubs[\/\\]dorkit[\/\\]graphic\.js$/.test(fullpath)) {
        // Real bug found live bundling Minesweeper: the flat
        // sbbs_stubs/graphic.js copy deliberately ends with a bare
        // `Graphic;` ("Leave as last line for convenient load() usage")
        // specifically so `var Graphic = load({}, "graphic.js")`
        // (Bubble Boggle's and Minesweeper's own real calling
        // convention) gets the real constructor back as load()'s
        // completion value. This dorkit/ copy -- which load()'s own
        // path search prefers over the flat one, same as the
        // cga_defs.js BG_HIGH/BG_BRIGHT split -- has no such trailer;
        // its real last statement is an ordinary prototype-method
        // assignment, so (after the fix that stops trusting an
        // assignment's incidental value as a real "result", see load()
        // itself) it fell back to returning `scope` instead --
        // `new Graphic(...)` then threw "Graphic is not a constructor"
        // since `scope` is a plain object, not the constructor. Adding
        // the same trailer this file's own flat sibling already uses
        // fixes it the same documented way, rather than special-casing
        // the caller.
        source += '\nGraphic;\n';
    }
    if (/[\/\\]sbbs_stubs[\/\\]frame\.js$/.test(fullpath)) {
        // Real bug found live bundling FatFish: a gray box appeared
        // over the top-left quadrant of the lake, exactly the size
        // and position of the door's own hidden shopFrame (created
        // and .clear(BG_LIGHTGRAY)'d at startup but never .open()'d
        // until the player presses the shop key) -- the shop frame's
        // gray fill was showing THROUGH the lake terrain it should
        // have been completely covered by. Root-caused with a direct
        // minimal reproduction (two overlapping sibling frames, the
        // smaller/later one filled first, then the larger/earlier one
        // .top()'d -- confirmed the .top() call has ZERO effect on
        // which one actually renders on top under this shim), not
        // guessed.
        //
        // Real cause: Display.prototype.__getTopCanvas__ (this real,
        // unmodified vendored file's own compositing logic) picks the
        // topmost frame by iterating `Object.keys(this.__properties__
        // .canvas)` and keeping the LAST match -- relying on
        // .top()/.bottom() reordering that key's position via a
        // delete-then-reinsert. That works fine for ordinary string
        // keys, but frame.id (Frame's constructor, confirmed above:
        // `this.__properties__.id = parent.display.nextID`, a bare
        // incrementing integer) becomes a canonical array-index-like
        // object key once used to key the `canvas` object -- and
        // per the ECMAScript spec, engines MUST enumerate integer-
        // index keys in ascending numeric order, unconditionally,
        // regardless of insertion or reinsertion order. Node's V8
        // follows this spec rule strictly. FatFish's shopFrame is
        // created (and thus gets its id) AFTER lakeFrame, so its key
        // is always enumerated last -- meaning it always "wins" the
        // topmost-canvas check, no matter how many times lakeFrame is
        // .top()'d. (Whatever engine real Synchronet actually embeds
        // must not enforce that same integer-key ordering rule, or
        // this code wouldn't work there either -- confirmed via
        // Jerry's own real game server screenshot showing correct
        // rendering with no gray box.)
        //
        // This isn't FatFish-specific -- ANY door with overlapping
        // sibling frames that relies on .top()/.bottom() to control
        // which one is visible would hit the same bug under this
        // shim, so the fix patches frame.js itself (matched by its
        // own fullpath, the same mechanism used for door-specific
        // fixes elsewhere in this function) rather than working
        // around it per-door. Display.prototype.open/close/top/
        // bottom/__getTopCanvas__ are all `X.prototype.Y = function
        // (){}` property assignments, not plain named function
        // declarations -- not directly targetable by
        // _insertBeforeFunctionEnd/_insertAfterFunctionStart (same
        // limitation noted elsewhere in this function) -- so this
        // wraps all five from OUTSIDE, appended after the whole file
        // (already real, unmodified, fully defined by this point)
        // finishes loading. Maintains an explicit insertion-ordered
        // array (canvasOrder) as the real source of z-order truth,
        // completely sidestepping the integer-key enumeration
        // problem, while leaving the original per-cell inclusion
        // test (`c.frame.parent == undefined || c.hasData(x,y)`)
        // untouched -- only the ORDER canvases are visited in changes.
        source += (
            '\n(function () {\n' +
            '\tvar __origOpen = Display.prototype.open;\n' +
            '\tvar __origClose = Display.prototype.close;\n' +
            '\tvar __origTop = Display.prototype.top;\n' +
            '\tvar __origBottom = Display.prototype.bottom;\n' +
            '\tfunction __remove(order, id) {\n' +
            '\t\tvar idx = order.indexOf(id);\n' +
            '\t\tif (idx !== -1) order.splice(idx, 1);\n' +
            '\t}\n' +
            '\tDisplay.prototype.open = function (frame) {\n' +
            '\t\t__origOpen.call(this, frame);\n' +
            '\t\tif (!this.__properties__.canvasOrder) this.__properties__.canvasOrder = [];\n' +
            '\t\t__remove(this.__properties__.canvasOrder, frame.id);\n' +
            '\t\tthis.__properties__.canvasOrder.push(frame.id);\n' +
            '\t};\n' +
            '\tDisplay.prototype.close = function (frame) {\n' +
            '\t\t__origClose.call(this, frame);\n' +
            '\t\tif (this.__properties__.canvasOrder) __remove(this.__properties__.canvasOrder, frame.id);\n' +
            '\t};\n' +
            '\tDisplay.prototype.top = function (frame) {\n' +
            '\t\t__origTop.call(this, frame);\n' +
            '\t\tif (!this.__properties__.canvasOrder) this.__properties__.canvasOrder = [];\n' +
            '\t\t__remove(this.__properties__.canvasOrder, frame.id);\n' +
            '\t\tthis.__properties__.canvasOrder.push(frame.id);\n' +
            '\t};\n' +
            '\tDisplay.prototype.bottom = function (frame) {\n' +
            '\t\t__origBottom.call(this, frame);\n' +
            '\t\tif (!this.__properties__.canvasOrder) this.__properties__.canvasOrder = [];\n' +
            '\t\t__remove(this.__properties__.canvasOrder, frame.id);\n' +
            '\t\tthis.__properties__.canvasOrder.unshift(frame.id);\n' +
            '\t};\n' +
            '\tDisplay.prototype.__getTopCanvas__ = function (x, y) {\n' +
            '\t\tvar top = undefined;\n' +
            '\t\tvar order = this.__properties__.canvasOrder || Object.keys(this.__properties__.canvas);\n' +
            '\t\tfor (var __i = 0; __i < order.length; __i++) {\n' +
            '\t\t\tvar c = this.__properties__.canvas[order[__i]];\n' +
            '\t\t\tif (!c) continue;\n' +
            '\t\t\tif (c.frame.parent == undefined || c.hasData(x, y)) top = c;\n' +
            '\t\t}\n' +
            '\t\treturn top;\n' +
            '\t};\n' +
            '})();\n'
        );
    }
    if (/[\/\\]sbbs_doors[\/\\]synchronetris[\/\\]game\.js$/.test(fullpath)) {
        // drawBoard() writes every board cell via Frame.setData(), but
        // setData() only updates the frame's internal buffer -- an
        // explicit draw()/cycle() call afterward is what actually
        // paints the change to the screen (confirmed against this
        // project's own vendored frame.js: draw()/cycle() are the only
        // methods that flush). Missing that call meant the board
        // never visually updated after a completed line was cleared
        // (getLines() calls drawBoard() expecting it to repaint) --
        // confirmed live: lines never visually disappeared even though
        // the underlying board data was correctly cleared.
        //
        // draw() was tried first and made things visibly worse: it
        // calls refresh() (Display.updateFrame(), an unconditional
        // repaint of every cell in the frame from its own buffer)
        // before cycle(). The falling piece is rendered by
        // drawCurrent()/unDrawCurrent() via direct gotoxy()+putmsg(),
        // bypassing the frame's setData()-backed buffer entirely, so
        // that full repaint stomps on the falling piece wherever it
        // overlaps board cells the buffer still thinks are empty --
        // confirmed live. cycle() alone (Display.cycle(), confirmed
        // against frame.js) only repaints the specific cells present
        // in __getUpdateList__() -- i.e. only cells actually touched
        // by setData() -- so it can't touch the separately-drawn
        // falling piece at all.
        source = _insertBeforeFunctionEnd(source, 'drawBoard', '\n\tplayer.stack.cycle();\n');
        // setPiece() writes a newly-locked piece straight into
        // player.grid but never calls drawBoard() at all, so a locked
        // piece never appears on screen until something else (a line
        // clear, another player's move) happens to trigger a redraw --
        // confirmed against the real Synchronet server source
        // (json-db.js) that this can't have been relying on receiving
        // an echo of the player's own "SET" push notification, since
        // real Synchronet explicitly never echoes a write back to
        // whoever sent it. Confirmed live: a locked piece would appear
        // to vanish, or a later piece would appear to visually merge
        // with a stack that hadn't actually updated on screen.
        source = _insertBeforeFunctionEnd(source, 'setPiece', '\n\tdrawBoard(localPlayer);\n');
        // setPiece() only ever sends a "SET" notification, which
        // carries no grid data at all (packageData()'s "SET" case is
        // completely empty -- confirmed reading it). The actual board
        // state only ever gets sent via a separate "GRID" message,
        // which getLines() only sends when a line actually clears --
        // meaning in multiplayer, other players never see your locked
        // stack update at all unless you happen to clear a line on
        // that exact piece. A real structural gap in the door's own
        // network sync, not just a rendering-timing issue. Always
        // sending GRID alongside SET closes it.
        source = _insertBeforeFunctionEnd(source, 'setPiece', '\n\tsend("GRID");\n');
        // setPiece() also never calls unDrawCurrent() before locking
        // the piece into player.grid -- it writes the grid data,
        // deletes currentPiece, then (via the fix directly above)
        // calls drawBoard(), which repaints the WHOLE grid including
        // the just-landed cells with the same character/color the
        // falling piece was already rendered with there, so setData()
        // treats it as no visual change and never re-marks it dirty.
        // That's fine IF the piece's own falling-render was already
        // correctly flushed to the real screen by then -- but nothing
        // guarantees that: a hard drop (fullDrop()) calls move()
        // rapidly in a tight loop with zero cycle() calls in between,
        // so the piece's final raw-drawn position can still be
        // sitting unflushed at the exact moment it locks in. Confirmed
        // live: hard-dropping left a ghost of the just-dropped piece
        // rendered overlapping/next to where it actually landed.
        // Erasing it explicitly via the same raw mechanism it was
        // drawn with, before the grid write and drawBoard() sweep,
        // closes this regardless of the flush-timing details above --
        // unDrawCurrent() needs currentPiece to still be set, so it
        // has to run at the START of setPiece(), before the existing
        // end-of-function patch deletes it.
        source = _insertAfterFunctionStart(source, 'setPiece', '\n\tunDrawCurrent(localPlayer);\n');
        // loadGarbage() (receiving garbage lines from another player
        // clearing rows) shift()s the top row off player.grid and
        // push()es a new garbage row at the bottom -- every existing
        // row's on-screen meaning changes -- but never calls
        // drawBoard() itself (it only reaches drawBoard() indirectly,
        // via setPiece(), on the separate branch where the falling
        // piece interferes with the shift and has to be locked in
        // place early). On the normal branch the stack's own visible
        // pixels are never resynced to the shifted grid data at all,
        // leaving stale blocks on screen exactly where they used to
        // be -- confirmed live: a lone block rendered disconnected
        // from the rest of the stack after other players caused a
        // garbage shift. Same fix class as drawBoard()/setPiece()
        // above -- append the missing flush.
        source = _insertBeforeFunctionEnd(source, 'loadGarbage', '\n\tdrawBoard(localPlayer);\n');
    }
    if (/[\/\\]sbbs_doors[\/\\]synchronetris[\/\\]lobby\.js$/.test(fullpath)) {
        // processUpdate()'s WRITE handler walks a dotted update
        // location (e.g. "games.5.players.Bob") and auto-vivifies any
        // missing intermediate object as a bare {} -- but a real Game
        // object's identity depends on its own .gameNumber property,
        // which a bare {} never gets. This only bites a client that
        // receives a nested-path update for a game it never received
        // the FULL creation write for (its own initial snapshot read,
        // loadGames(), happens once at door-script load time, before
        // subscribe() registers -- any game created by another player
        // in that window is invisible to the snapshot and only ever
        // arrives here as later nested-path fragments). Confirmed
        // live: a lobby tile rendered "Game undef" / "[finished]",
        // and joinGame() -> getOpenGame() returned that same bare
        // entry's gameNumber (undefined), logging "Error finding game
        // number" (isNaN(undefined) is true) instead of joining.
        // Repairing gameNumber from the object's own store key (this
        // door's own convention throughout -- data.games[gnum] is
        // always keyed by that same gnum) after every processed
        // update is a general, cheap self-heal, not a full parse of
        // update semantics.
        //
        // Also backfills .players -- confirmed live this gameNumber-
        // only repair wasn't enough on its own: fixing the display
        // (the tile now correctly showed "Game 1 [finished]" instead
        // of "Game undef") let the door consider it a real, joinable
        // game again (getOpenGame() doesn't exclude FINISHED, only
        // PLAYING/SYNCING), and joinGame()'s own
        // `data.games[gnum].players[profile.name] = player` crashed
        // with "Cannot set properties of undefined" the moment
        // someone actually tried to join it, since .players was
        // still missing. A real Game object always has both
        // (tetrisobj.js's own Game() constructor: gameNumber AND
        // players together) -- repairing one without the other just
        // traded one crash for a different one further down the same
        // code path.
        source = _insertBeforeFunctionEnd(source, 'processUpdate',
            '\n\tfor (var __gk in data.games) {\n' +
            '\t\tif (data.games[__gk] && data.games[__gk].gameNumber === undefined)\n' +
            '\t\t\tdata.games[__gk].gameNumber = parseInt(__gk, 10);\n' +
            '\t\tif (data.games[__gk] && !data.games[__gk].players)\n' +
            '\t\t\tdata.games[__gk].players = {};\n' +
            '\t}\n');
        // Neither self-heal above can reach joinGame()'s OWN fresh
        // client.read(game_id,"games."+gnum) call -- that fetches
        // directly from the server every time, bypassing the local
        // (already-repaired) data.games cache entirely, so a
        // still-corrupted server-side record comes back corrupted
        // every time regardless of the fixes above. Confirmed live:
        // getOpenGame() doesn't exclude FINISHED games (only
        // PLAYING/SYNCING), so a dead-but-still-FINISHED game (now
        // correctly showing "Game 1" instead of "Game undef" thanks
        // to the gameNumber repair) got offered as joinable, and
        // `data.games[gnum].players[profile.name] = player` crashed
        // on the still-missing .players the moment someone actually
        // tried to join it. Since that crash is mid-function (inside
        // joinGame() itself), it can't be healed after the fact by
        // appending code elsewhere -- appended code only runs once a
        // function returns normally. Instead, wrap client.read ONCE
        // at lobby startup (open() always runs before main()'s loop
        // can ever reach joinGame()) so any FUTURE read of a
        // "games.N" record is repaired the instant it comes back,
        // covering this call site and any other read of the same
        // corrupted record, not just this one crash.
        source = _insertBeforeFunctionEnd(source, 'open',
            '\n\t(function () {\n' +
            '\t\tvar __origRead = client.read;\n' +
            '\t\tclient.read = function (scope, location) {\n' +
            '\t\t\tvar __r = __origRead.apply(client, arguments);\n' +
            '\t\t\tif (__r && typeof __r === "object" && /^games\\.\\d+$/.test(String(location))) {\n' +
            '\t\t\t\tvar __gn = parseInt(String(location).split(".")[1], 10);\n' +
            '\t\t\t\tif (__r.gameNumber === undefined) __r.gameNumber = __gn;\n' +
            '\t\t\t\tif (!__r.players) __r.players = {};\n' +
            '\t\t\t}\n' +
            '\t\t\treturn __r;\n' +
            '\t\t};\n' +
            '\t})();\n');
        // Real Pi3 crash, same corruption class yet again but the
        // WORST-placed instance of it: updateStatus() is called
        // SYNCHRONOUSLY from inside processUpdate()'s own WRITE case
        // -- the SAME processUpdate() call that may have just
        // auto-vivified data.games[gameNumber] as a bare {} a few
        // lines earlier in that same switch statement, before ever
        // reaching processUpdate()'s own appended end-of-function
        // repair above. Confirmed live:
        // `data.games[gameNumber].players[profile.name]` crashed
        // with "Cannot read properties of undefined" -- an append-at-
        // end patch can't reach this, since appended code only runs
        // once the whole function returns, and this crash happens
        // partway through the SAME processUpdate() call that invoked
        // updateStatus(). Needs a guard at the very START of
        // updateStatus() itself instead, via
        // _insertAfterFunctionStart -- ahead of updateStatus()'s own
        // existing `if(!data.games[gameNumber]) return false;` check,
        // which already handles the game not existing at all but
        // never accounted for it existing in a still-corrupted shape.
        source = _insertAfterFunctionStart(source, 'updateStatus',
            '\n\tif (data.games[gameNumber] && !data.games[gameNumber].players)\n' +
            '\t\tdata.games[gameNumber].players = {};\n');
    }
    if (/[\/\\]sbbs_doors[\/\\]synchronetris[\/\\]tetrisobj\.js$/.test(fullpath)) {
        // Same corruption as processUpdate() above (a bare {} missing
        // .gameNumber), but reached a different way: GameData's own
        // this.loadGames() does the door's ONE-TIME initial bulk
        // fetch (client.read(game_id,"games",1)) and assigns straight
        // to this.games with no repair at all. The processUpdate()
        // fix above only ever runs in response to a LATER incoming
        // subscribe() push -- if a dead/abandoned game never gets
        // touched by another update again (the common case for
        // something already finished), that self-heal never gets a
        // chance to fire and the corrupted entry from this initial
        // fetch persists for the entire session. Confirmed live: the
        // "Game undef" tile came back after a corrupted entry was
        // cleared server-side and recurred from ordinary play, and
        // persisted through a whole session with no further updates
        // to repair it. this.loadGames() itself is an inline
        // `this.loadGames=function(){...}` assignment, not a named
        // function declaration this project's patch mechanism can
        // target directly -- but it's always called synchronously
        // from GameData's own constructor (a real named function),
        // before that constructor returns, so appending the same
        // repair there catches it right after the fetch completes.
        source = _insertBeforeFunctionEnd(source, 'GameData',
            '\n\tfor (var __gk in this.games) {\n' +
            '\t\tif (this.games[__gk] && this.games[__gk].gameNumber === undefined)\n' +
            '\t\t\tthis.games[__gk].gameNumber = parseInt(__gk, 10);\n' +
            '\t}\n');
    }
    if (/[\/\\]sbbs_doors[\/\\]jeopardized[\/\\]jeopardized\.js$/.test(fullpath)) {
        // Real crash found live smoke-testing: any BRAND NEW player
        // (anyone who's never played Jeopardized before -- confirmed
        // this includes the sysop's own account, since existing
        // players visible in the live message feed are the only ones
        // with actual records) crashes immediately on selecting "Play"
        // with "Cannot read properties of null (reading 'round')".
        //
        // Root cause, confirmed against the real server (a direct read
        // for a nonexistent key returns {"data": null}, not an omitted
        // field) and the real vendored client (sbbs_reference/
        // json-client.js's own wait() returns packet.data completely
        // raw, no null-to-undefined conversion): database.js's
        // getUser()/getUserGameState() both check
        // `typeof x === 'undefined'` to decide whether to create a
        // fresh record for a new player -- but a missing key reads
        // back as JS `null` (typeof 'object'), not `undefined`, so
        // that check never catches it and the raw null gets returned
        // straight through. This is a real bug in the door's own code
        // that would happen identically on real Synchronet (the real
        // client returns the exact same raw null), not a compat-shim
        // gap -- confirmed by reading the real reference client, not
        // guessed.
        //
        // getUser()/getUserGameState() are `this.NAME = function(){}`
        // property assignments inside Database's own constructor (also
        // itself a `var Database = function(settings){}` expression,
        // not a plain named declaration this project's patch mechanism
        // can target directly), and every path through both already
        // ends in an explicit return -- appending repair code at
        // either end is a no-op (dead code after an existing return).
        // Neither can the door's own private addUser()/
        // addUserGameState() helpers be called from outside; they're
        // closure-scoped inside Database's constructor, never exposed
        // on the instance. Wrapping both PUBLIC methods from
        // initDatabase() (a real named function in THIS file, always
        // called exactly once right after `database = new
        // Database(...)` completes) and re-running the same create-if-
        // missing sequence the door's own addUser()/addUserGameState()
        // already do (write the 'input' event, then retry-read) via a
        // fresh one-shot JSONClient -- database.getUserID() is a public
        // method so the id itself doesn't need re-deriving -- is the
        // only reachable fix point. The write payload shape below is
        // server-side protocol, not creative door logic: confirmed
        // directly from the door's own real addUser()/
        // addUserGameState(), which is the only source for what the
        // real json-service's 'input' handler actually expects.
        source = _insertBeforeFunctionEnd(source, 'initDatabase',
            '\n\t(function () {\n' +
            '\t\tfunction __ensure(key, extra, path) {\n' +
            '\t\t\tvar __client = new JSONClient(settings.JSONDB.host, settings.JSONDB.port);\n' +
            '\t\t\t__client.write(settings.JSONDB.dbName, "input", { key: key, data: extra }, 2);\n' +
            '\t\t\tvar __v;\n' +
            '\t\t\tfor (var __n = 0; __n < settings.JSONDB.retries; __n++) {\n' +
            '\t\t\t\tmswait(settings.JSONDB.retryDelay);\n' +
            '\t\t\t\t__v = __client.read(settings.JSONDB.dbName, path, 1);\n' +
            '\t\t\t\tif (__v !== null && typeof __v !== "undefined") break;\n' +
            '\t\t\t}\n' +
            '\t\t\treturn __v;\n' +
            '\t\t}\n' +
            '\t\tvar __origGetUser = database.getUser;\n' +
            '\t\tdatabase.getUser = function (usr) {\n' +
            '\t\t\tvar __r = __origGetUser.call(database, usr);\n' +
            '\t\t\tif (__r !== null) return __r;\n' +
            '\t\t\tvar __id = database.getUserID(usr);\n' +
            '\t\t\treturn __ensure(\n' +
            '\t\t\t\t"users",\n' +
            '\t\t\t\t{ id: __id, alias: usr.alias, system: system.name },\n' +
            '\t\t\t\t"users." + __id\n' +
            '\t\t\t);\n' +
            '\t\t};\n' +
            '\t\tvar __origGetUserGameState = database.getUserGameState;\n' +
            '\t\tdatabase.getUserGameState = function (usr) {\n' +
            '\t\t\tvar __r = __origGetUserGameState.call(database, usr);\n' +
            '\t\t\tif (__r !== null) return __r;\n' +
            '\t\t\tvar __id = database.getUserID(usr);\n' +
            '\t\t\treturn __ensure(\n' +
            '\t\t\t\t"game.users",\n' +
            '\t\t\t\t{ id: __id },\n' +
            '\t\t\t\t"game.users." + __id\n' +
            '\t\t\t);\n' +
            '\t\t};\n' +
            '\t})();\n');
    }
    if (/[\/\\]sbbs_doors[\/\\]synkroban[\/\\]synkroban\.js$/.test(fullpath)) {
        // Real portability bug in the door's own source, confirmed by
        // reading it directly: level-set loading (SkbLevelSet.init())
        // and the level-set picker (pick_level_set()) both hardcode
        // the literal path "/sbbs/xtrn/synkroban/" (the author's own
        // install location) instead of using js.exec_dir like every
        // other well-behaved door in this project -- skb_config's own
        // PATH_SYNKROBAN setting (used correctly, elsewhere, for
        // server.ini) is never consulted for these two call sites at
        // all. On any install NOT living at that exact absolute path
        // (guaranteed for ANetBBS, which installs at a variable,
        // per-deployment location), level files silently fail to load
        // -- confirmed: this is a plain string substitution, not a
        // function-boundary insertion, since the bug is a literal
        // wrong VALUE embedded mid-string in three places, not a
        // missing/misplaced statement _insertBeforeFunctionEnd or
        // _insertAfterFunctionStart could target. Breaking out of the
        // string literal to splice in the real js.exec_dir expression
        // (already resolved by our own load() pipeline to wherever
        // this specific install actually put the bundled door) fixes
        // every occurrence uniformly, whether standalone
        // ("/sbbs/xtrn/synkroban/" alone) or concatenated with a
        // further literal suffix (e.g. "/sbbs/xtrn/synkroban/levels/").
        source = source.split('/sbbs/xtrn/synkroban/').join('" + js.exec_dir + "');
    }
    if (/[\/\\]sbbs_doors[\/\\]startrek[\/\\]startrek\.js$/.test(fullpath)) {
        // Same real bug class already fixed for Jeopardized's
        // getUser()/getUserGameState() (see that branch above):
        // scoreBoard() checks `if (scores === undefined)` to decide
        // whether this is the very first score ever submitted for the
        // "STARTREK" scope, but the real server returns JSON `null`
        // for a missing key, not `undefined` -- confirmed against the
        // real live server and the real vendored json-client.js's own
        // wait() (`return packet.data;`, no null-to-undefined
        // conversion). On a brand-new scope (guaranteed here, since
        // Star Trek has never been played against this server before),
        // `scores` comes back `null`, the `=== undefined` check misses
        // it, and the very next line -- `for (var s = 0; s <
        // scores.length; ...)` -- crashes with "Cannot read
        // properties of null (reading 'length')" the first time ANY
        // player finishes a game. Plain string substitution (like the
        // synkroban fix above), not a function-boundary insertion --
        // simplest fix for a single wrong comparison operator, and
        // scoreBoard() is itself a real named function declaration
        // (unlike Jeopardized's this.getUser=function(){} pattern) so
        // an _insertAfterFunctionStart-based guard was considered, but
        // the crash is a genuine WRONG COMPARISON mid-function, not a
        // missing statement at the boundary -- fixing the comparison
        // itself is both simpler and more directly correct.
        source = source.split('scores === undefined')
            .join('(scores === undefined || scores === null)');
    }
    if (/[\/\\]sbbs_doors[\/\\]dicewarz2[\/\\]dicefunc\.js$/.test(fullpath)) {
        // Real crash found live bundling Dice Warz ][: starting a
        // single-player game crashed immediately entering the map
        // screen with "Cannot read properties of undefined (reading
        // 'owner')" in drawSector(). Root-caused with a direct,
        // isolated reproduction (not guessed): `map.grid` is a 2D
        // array where empty cells are left as genuine JS array holes
        // (`undefined`) by generateMap() -- but this game's own
        // architecture round-trips the whole map through JSON-RPC
        // write+read (client.write(...) then a later client.read(...)
        // in playGame(), a real network hop, not an in-memory reuse
        // of the object generateMap() just built). JSON.stringify()
        // converts array holes to literal `null`, not `undefined` --
        // confirmed directly: `JSON.parse(JSON.stringify([,5,]))` is
        // `[null,5,null]`. getTile()'s own check,
        // `grid[coords.x][coords.y]>=0`, silently treats that as a
        // VALID tile index, because `null >= 0` is `true` in
        // JavaScript (null coerces to 0 for a numeric comparison) --
        // while the original, never-serialized `undefined` correctly
        // failed the same check (`undefined >= 0` is `false`). The
        // very next line then indexes `map.tiles[null]` (coerced to
        // the property key `"null"`), which doesn't exist, and every
        // caller (drawSector() and everything else that asks "is
        // there a tile here?") crashes on `.owner`. This is a real
        // bug in the door's own source that would happen identically
        // on real Synchronet -- the JSON round-trip is this door's
        // own core persistence mechanism, not something the compat
        // shim introduces. Fixing getTile() itself (the single shared
        // helper every caller already goes through) closes this for
        // every call site at once, rather than patching each `>= 0`
        // check scattered through the file individually -- the other
        // two occurrences of this same comparison (landNearby(),
        // getRandomDirection()) only ever run against a freshly-
        // generated, never-serialized map, so they're not affected in
        // practice and are left untouched. Plain string substitution,
        // like the synkroban/startrek fixes above -- the bug is a
        // wrong comparison, not a missing statement at a function
        // boundary.
        source = source.split(
            'if(coords && grid[coords.x][coords.y]>=0) \n\t\treturn grid[coords.x][coords.y];'
        ).join(
            'if(coords && grid[coords.x][coords.y]!==null && grid[coords.x][coords.y]>=0) \n\t\treturn grid[coords.x][coords.y];'
        );
    }
    if (/[\/\\]sbbs_doors[\/\\]thirsty[\/\\]thirsty\.js$/.test(fullpath)) {
        // Same bug class as the startrek/dicewarz2 fixes above, found
        // auditing Thirstyville's source (not yet live-crashed, since
        // it's confirmed here before first bundling): confirmed
        // directly against Jerry's real json-rpc server that a READ or
        // KEYS op against a real but as-yet-empty location returns
        // JSON `null`, never `undefined` --
        //   echo '{"op":"read",...,"scope":"DICEWARZ2","location":"DICEWARZ2.NONEXISTENT_KEY",...}'
        //   => {"ok": true, "data": null}
        // dataInit()'s very first-ever game start does
        // `gameSettings = jsonClient.read("THIRSTY","THIRSTY.SETTINGS",1);
        // if(gameSettings === undefined) { ... }` -- on a brand new
        // THIRSTY module this returns null, the `=== undefined` guard
        // never fires, gameSettings stays null, and every later
        // `gameSettings.week`/`.updated`/etc access crashes. The
        // adjacent `(playerKeys === undefined) ? 1 :
        // playerKeys.length` has the identical bug against
        // THIRSTY.PLAYERS.keys() on the same first-ever run. Both
        // fixed with the same null-or-undefined widening as the
        // startrek fix above. getScores()'s `if(keys === undefined)
        // throw ...` has the same bug but doesn't crash (a bare
        // `for(var k in keys)` over null is a documented ES5 no-op,
        // not a throw) -- it only silently swallows the intended "no
        // players yet" error message, so it's widened too for
        // correctness while we're here.
        source = source.split('if(gameSettings === undefined) {')
            .join('if(gameSettings === undefined || gameSettings === null) {');
        source = source.split('((playerKeys === undefined) ? 1 : playerKeys.length)')
            .join('((playerKeys === undefined || playerKeys === null) ? 1 : playerKeys.length)');
        source = source.split('if(keys === undefined)\n\t\t\tthrow "THIRSTY.PLAYERS has no properties.";')
            .join('if(keys === undefined || keys === null)\n\t\t\tthrow "THIRSTY.PLAYERS has no properties.";');
    }
    if (/[\/\\]sbbs_doors[\/\\]thirsty[\/\\]player\.js$/.test(fullpath)) {
        // Same null-vs-undefined bug class as thirsty.js above, but
        // this one is a guaranteed crash rather than a fallback-logic
        // miss: getPlayer()'s `player = jsonClient.read("THIRSTY",
        // "THIRSTY.PLAYERS."+playerID, 1); if(player === undefined ||
        // player.money <= 0)` has no `|| update`-style short-circuit
        // to save it (unlike demographics.js/products.js/stock-
        // items.js/weather.js, which all pass their fetch result
        // through `X === undefined || update`, and `update` is always
        // true on the very run where X would be null -- see the
        // thirsty.js comment above). Every brand-new player's first
        // join reads a THIRSTY.PLAYERS.<id> key that has never been
        // written, confirmed to come back as real JSON `null` (not
        // `undefined`) the same way as every other never-written key
        // on this server. `player === undefined` is then false, so JS
        // evaluates the second half of the `||` -- `player.money`
        // -- on a null player, throwing "Cannot read properties of
        // null (reading 'money')" for literally every new player.
        source = source.split('if(player === undefined || player.money <= 0) {')
            .join('if(player === undefined || player === null || player.money <= 0) {');
    }
    if (/[\/\\]sbbs_doors[\/\\]thirsty[\/\\]stock-items\.js$/.test(fullpath)) {
        // Worst instance of the same null-vs-undefined bug class found
        // in this door: makeStockItems() runs unconditionally as part
        // of the very first game creation (getStockItems(true), from
        // dataInit(), BEFORE getPlayer() ever writes the first player
        // record) -- `jsonClient.keys("THIRSTY","THIRSTY.PLAYERS",1)`
        // has no guard AT ALL, not even a wrong `=== undefined` check,
        // so this crashes with "Cannot read properties of null
        // (reading 'length')" for literally the first player to ever
        // start a fresh Thirstyville install, unconditionally, every
        // time -- confirmed against the real live server that KEYS on
        // a not-yet-written location returns real JSON null.
        source = source.split(
            'var players = jsonClient.keys("THIRSTY", "THIRSTY.PLAYERS", 1).length;'
        ).join(
            'var players = (jsonClient.keys("THIRSTY", "THIRSTY.PLAYERS", 1) || []).length;'
        );
    }
    if (/[\/\\]sbbs_doors[\/\\]gttrivia[\/\\]gttrivia\.js$/.test(fullpath)) {
        // Same null-vs-undefined bug class found throughout this
        // session (a JSON-RPC read of a not-yet-written key returns
        // real JSON null): gttrivia.js's own guards use
        // `typeof(data) === "object"` to sanity-check a read result --
        // but `typeof null === "object"` is a well-known JavaScript
        // quirk, so these checks don't actually exclude null the way
        // they look like they do. Every one of these is already
        // wrapped in a try/catch (this door is unusually well-defended
        // compared to earlier doors this session), so the real-world
        // impact is a confusing on-screen JS error message rather than
        // a hard crash -- confirmed live against the real remote
        // server (digitaldistortionbbs.com, Jerry's own choice for
        // this door -- see gttrivia.ini) that the top-level SCORES
        // scope already has real historical data, so showServerScores()
        // won't hit this in practice today -- but a per-user/per-BBS
        // sub-path (e.g. any brand-new player/BBS never seen before)
        // genuinely can be null, and the sysop-menu removal functions
        // hit the identical pattern. Widened defensively rather than
        // waiting for a live report, since it's a one-line fix with
        // zero behavioral change for the already-working case.
        source = source.split('if (typeof(data) !== "object")')
            .join('if (typeof(data) !== "object" || data === null)');
        source = source.split(
            'if (typeof(data) === "object" && data.hasOwnProperty("systems"))'
        ).join(
            'if (typeof(data) === "object" && data !== null && data.hasOwnProperty("systems"))'
        );
        source = source.split('if (typeof(serverUserScoreData) === "object")')
            .join('if (typeof(serverUserScoreData) === "object" && serverUserScoreData !== null)');
    }
    if (/[\/\\]sbbs_doors[\/\\]thirsty[\/\\]weather\.js$/.test(fullpath)) {
        // Real crash found live smoke-testing Thirstyville:
        // "TypeError: POP.toFixed is not a function", every time
        // makeWeather()'s random roll for a day happens to land below
        // that weather condition's minimumPOP (common -- e.g. weather.
        // ini's "Cloudy" is minimumPOP=20/maximumPOP=70, roughly a 29%
        // chance per day, and this runs once per day for 7 days on
        // every world/reset). Root cause: `weatherConditions[condition]
        // .minimumPOP` comes straight out of File.iniGetAllObjects(),
        // which returns raw strings for every value (confirmed against
        // this shim's own implementation above, and real Synchronet's
        // iniGetObject/iniGetAllObjects behave the same way absent an
        // explicit template argument, which this door doesn't pass) --
        // `POP < ...minimumPOP` numerically coerces fine for the
        // comparison, but the very next line assigns that STRING
        // straight into POP with no parseInt/parseFloat, so
        // `POP.toFixed()` a few lines later throws on a string. A
        // genuine bug in the door's own source that would crash real
        // Synchronet identically, not something introduced by this
        // shim.
        source = source.split(
            'POP = weatherConditions[condition].minimumPOP;'
        ).join(
            'POP = parseInt(weatherConditions[condition].minimumPOP, 10);'
        );
    }
    return source;
}

// Real bug found live bundling Minesweeper: `ansiterm_lib.js`,
// `sauce_lib.js`, and `avatar_lib.js` are three separate, real, correct
// vendored Synchronet library files that each independently declare
// their own top-level `const defs = {...}` -- each is meant to be
// load()'d into its OWN caller-provided scope object (e.g. `load({},
// "sauce_lib.js")`), so real Synchronet's engine must give each loaded
// script its own fresh top-level scope for this to work. This shim's
// load() runs every file via `vm.runInThisContext(code)` against the
// SAME actual global object every time (needed so loaded files can see
// console/log/attr/etc.) -- fine for `var` (redeclaring a `var` is
// always harmless, just overwrites the same globalThis property), but
// `const`/`let` declared at a script's top level live in a persistent
// "script realm" lexical environment that Node's vm module shares
// across EVERY separate runInThisContext call in this process. A
// SECOND top-level `const defs` from a DIFFERENT file collides with
// the first one exactly like redeclaring it in the same file would --
// confirmed live: Minesweeper load()s ansiterm_lib.js (via cterm_lib.js
// requiring it) and later sauce_lib.js (via show_image()), and the
// second one threw `SyntaxError: Identifier 'defs' has already been
// declared`, uncaught by anything except the door's own generic
// try/catch. Since the existing scope-population regex a few lines
// below already treats top-level var/const/let as interchangeable for
// the purpose of copying names onto a caller's scope object, rewriting
// them to `var` here for EXECUTION purposes is consistent with that
// existing treatment, not a new distinction -- and it makes
// redeclaration harmless the same way it already is for `var`,
// eliminating this whole collision class for every current and future
// loaded file, not just these three. Only rewrites declarations that
// start a line (matching the existing scope regex's own `^` anchor
// convention) -- a `const`/`let` inside a function body's own block
// scope never hits this shared-realm collision in the first place, so
// leaving those alone preserves their real block-scoping semantics.
function _promoteTopLevelConstLet(source) {
    // No leading \s* on purpose -- matches the existing scope-population
    // regex's own `^(?:var|const|let)` convention exactly (column 0,
    // unindented), so only genuine top-level declarations are widened.
    // An indented const/let (inside a function body's own block scope)
    // never hits the shared-realm collision this exists to fix, and
    // widening ITS scope too would be a real, unwanted behavior change.
    return source.replace(/^(const|let)(\s+)/gm, 'var$2');
}
function _polyfillE4XForEach(source) {
    var re = /for\s+each\s*\(\s*var\s+(\w+)\s+in\s+/g;
    var out = '';
    var i = 0;
    var counter = 0;
    var m;
    while ((m = re.exec(source)) !== null) {
        out += source.slice(i, m.index);
        var varname = m[1];
        var exprStart = re.lastIndex;
        var depth = 1;
        var j = exprStart;
        while (j < source.length && depth > 0) {
            if (source[j] === '(') depth++;
            else if (source[j] === ')') { depth--; if (depth === 0) break; }
            j++;
        }
        if (depth !== 0) {
            out += source.slice(m.index, j);
            i = j;
            re.lastIndex = j;
            continue;
        }
        var expr = source.slice(exprStart, j).trim();
        counter++;
        var key = '__fe_keys_' + counter;
        var idx = '__fe_i_' + counter;
        out += 'for (var ' + varname + ', ' + key + '=Object.keys((' + expr
             + ')||{}), ' + idx + '=0; ' + idx + '<' + key + '.length && ('
             + varname + '=(' + expr + ')[' + key + '[' + idx + ']],true); '
             + idx + '++)';
        i = j + 1;
        re.lastIndex = i;
    }
    out += source.slice(i);
    return out;
}

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
    // Real Synchronet's load(scope, filename, arg1, arg2, ...) form
    // (confirmed against the real vendored modopts.js's own doc
    // comment: "var options = load({}, 'modopts.js',
    // 'your_module_name');") passes any trailing args through as the
    // loaded script's own `argv` -- was completely dropped on the
    // floor here (the loaded script would see the OUTER door's own
    // argv, always [], instead). Found live bundling Synchronet
    // Minesweeper: `load({}, "modopts.js", ini_section)` needs
    // ini_section to arrive as modopts.js's own `argv[0]`.
    var scriptArgs = args;
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
    // Real bug found live bundling Minesweeper: cterm_lib.js load()s
    // ansiterm_lib.js internally (its own line 7), and minesweeper.js
    // ALSO load()s ansiterm_lib.js directly first -- both via the
    // 2-arg scope form, which this cache used to never cover at all
    // ("cache only applies to no-scope form"). Running the SAME file's
    // top-level const/let declarations a second time throws
    // `SyntaxError: Identifier 'defs' has already been declared` --
    // those bindings live in a script-realm lexical environment shared
    // across EVERY separate vm.runInThisContext call in this process
    // (unlike `var`, which is just a globalThis property and tolerates
    // redeclaration fine), so a second real execution of the same
    // source always collides. Real Synchronet's own load()/require()
    // never re-executes an already-loaded file regardless of how it's
    // called either -- that's the entire point of a load cache -- so
    // the scope-vs-no-scope distinction here was simply an incomplete
    // implementation, not an intentional difference. The cache now
    // applies universally; a cache hit still runs the scope-population
    // logic below (against globalThis state left by whichever call
    // actually executed the file), just skips re-executing the source.
    var _alreadyLoaded = !!_load_cache[fullpath];
    if (_alreadyLoaded && scope === null) {
        return; // bare form just wants the one-time side effect
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
        code = _polyfillE4XForEach(code);
        code = _applyKnownDoorFixes(fullpath, code);
        code = _promoteTopLevelConstLet(code);
        var result;
        if (!_alreadyLoaded) {
            // vm.runInThisContext runs the code with globalThis as its scope —
            // `var FOO = ...` and `function FOO()` create properties on
            // globalThis, which is what we need so require()'s lookup finds
            // them. Indirect (0,eval)() does NOT do this in Node's CommonJS
            // module wrapper context (hence past "SMB_SUCCESS is not defined").
            //
            // argv is a real global the loaded script reads as its own
            // script arguments -- temporarily swap in this call's own
            // scriptArgs (see the comment above where scriptArgs is
            // captured) and always restore whatever was there before, so
            // a nested/recursive load() with different trailing args (or
            // none) can't leak its argv into an unrelated caller further
            // up the stack. Must go through globalThis explicitly, not a
            // bare `argv = ...` assignment: this function's own `argv`
            // reference resolves to the module-scoped `var argv = []`
            // declared earlier in this same outer script, which is a
            // SEPARATE binding from globalThis.argv (only a one-time
            // copy of it, made once by _registerGlobals() at startup) --
            // vm.runInThisContext-executed code (the loaded script itself)
            // reads globalThis.argv, so that's what actually has to change
            // for it to see the new value at all.
            var _argvHolder = (typeof global !== 'undefined' ? global : globalThis);
            var _prevArgv = _argvHolder.argv;
            _argvHolder.argv = scriptArgs;
            try {
                result = _vm.runInThisContext(code, { filename: fullpath });
            } finally {
                _argvHolder.argv = _prevArgv;
            }
        }
        // Synchronet's 2-arg form: load(scope, "file") is supposed to
        // populate `scope` with the loaded file's top-level declarations.
        // Some callers reassign the return value (`var sauce = load({},
        // "sauce_lib.js")`); others pre-create the scope object and pass
        // it in, discarding the return value and expecting `scope` itself
        // to be mutated in place (e.g. graphic.js does `Graphic.prototype
        // .defs = {}; load(Graphic.prototype.defs, "cga_defs.js");` with
        // no assignment at all). Since the code above always runs against
        // the real globalThis (needed so the loaded file still sees
        // console/log/attr/etc. from the rest of the shim), copy the
        // file's own top-level `var`/`const`/`function` names from
        // globalThis onto `scope` explicitly — this satisfies both
        // calling conventions regardless of whether the file happens to
        // end with the "Leave as last line: this;" convention.
        if (scope !== null) {
            var _declRe = /^(?:var|const|let)\s+([A-Za-z_$][\w$]*)|^function\s+([A-Za-z_$][\w$]*)\s*\(/gm;
            var _m;
            while ((_m = _declRe.exec(code)) !== null) {
                var _name = _m[1] || _m[2];
                if (_name && typeof globalThis[_name] !== 'undefined') {
                    scope[_name] = globalThis[_name];
                }
            }
            // Real crash found live bundling Minesweeper: modopts.js's
            // own doc comment shows `var options = load({}, "modopts.js",
            // "your_module_name");` -- the CALLER expects load()'s own
            // return value to be the script's real computed result
            // (modopts.js's last line is a bare `get_mod_options(...)`
            // call, no top-level var to copy onto scope at all), not
            // just the scope object the regex above populated. Real
            // Synchronet's load() supports BOTH conventions -- graphic.js
            // deliberately discards the return value and only cares
            // about `scope` being mutated in place (some of its loaded
            // files end with a harmless `this;` for exactly that
            // reason), while modopts.js-style callers reassign the
            // return value directly. Prefer the script's own real
            // completion value when it's a genuine result (not
            // undefined, and not just globalThis from a bare `this;`
            // trailer) -- falls back to `scope` otherwise, so the
            // already-working graphic.js/cga_defs.js path is untouched.
            //
            // Real regression found live bundling Minesweeper (in THIS
            // SAME fix, from earlier in this session): dorkit/graphic.js
            // -- the exact same file Bubble Boggle's own `var Graphic =
            // load({}, "graphic.js")` already relies on -- has no
            // deliberate "Leave as last line" trailer at all; its actual
            // last statement is an ordinary prototype-method assignment
            // (`Graphic.prototype.scrollup = function(){...};`). A JS
            // assignment EXPRESSION evaluates to the assigned value, so
            // `result` here was that unrelated function (V8 infers an
            // assigned function expression's `.name` from the property
            // it's assigned to, e.g. "scrollup"), not the real Graphic
            // constructor -- `result !== undefined && result !== _g` was
            // true, so it got returned and silently replaced the
            // correct constructor. `new Graphic(...)` still "worked"
            // (any function can be `new`'d), but the resulting
            // instance's prototype had none of graphic.js's real
            // methods (confirmed live: `graphic.load is not a
            // function`).
            //
            // First attempt at a fix here parsed the source TEXT to
            // find whether the last statement "looked like" an
            // assignment -- abandoned after it broke on this SAME
            // file's own regex literals (`/^\x1b\[([\x30-\x3f]*).../`,
            // real ANSI-parsing code a few hundred lines up): naive
            // brace/paren/bracket depth-counting has no notion of
            // "inside a string/regex literal," so a `[` inside a
            // character class throws the whole depth count off for the
            // rest of the file, silently misidentifying a huge, totally
            // unrelated multi-hundred-line block as "the last
            // statement." Checking the VALUE instead of the source text
            // sidesteps that whole class of problem: a deliberate
            // "export this real thing" completion value (the "Leave as
            // last line: Graphic;"/`defs;` convention) is always a bare
            // reference to something ALREADY a real, same-named global
            // -- `result.name` matches and `globalThis[result.name] ===
            // result`. An incidental one (assigned-function-expression
            // fallout) never does, since nothing declared it under that
            // name at the top level. Non-function results (modopts.js's
            // own convention: a plain object returned from its last
            // line's function CALL) are trusted unconditionally, same
            // as before -- this only tightens the check for functions.
            //
            // On a CACHE HIT there's no fresh `result` at all (the
            // source never re-runs -- see _load_result_name_cache's own
            // comment for why re-executing is unsafe) -- re-derive a
            // trusted function result from whatever name a PRIOR call
            // already proved trustworthy, so a file whose real "export"
            // is a named global (like Graphic) keeps working correctly
            // on the 2nd/3rd/... call, not just the 1st.
            var _g = (typeof globalThis !== 'undefined' ? globalThis : global);
            if (_alreadyLoaded && result === undefined && _load_result_name_cache[fullpath]) {
                result = globalThis[_load_result_name_cache[fullpath]];
            }
            var _trustResult = (result !== undefined && result !== _g);
            if (_trustResult && typeof result === 'function') {
                _trustResult = !!(result.name && globalThis[result.name] === result);
                if (_trustResult) {
                    _load_result_name_cache[fullpath] = result.name;
                }
            }
            if (_trustResult) {
                return result;
            }
            return scope;
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
// BLINK/BG_HIGH — real Synchronet globals (cga_defs.js's own values:
// BLINK=0x80 blink-bit, BG_HIGH=0x400 iCE-color/high-intensity-
// background bit), missing from this block entirely. Real bug found
// auditing Minesweeper: its very first executable line is
// `if(BG_HIGH === undefined) BG_HIGH = 0x400;` -- with BG_HIGH
// undeclared anywhere in scope (door + compat template share one
// concatenated file/scope; the door itself never loads cga_defs.js),
// that bare reference throws `ReferenceError: BG_HIGH is not defined`
// immediately on launch, before any other door code runs.
var BLINK = 0x80; var BG_HIGH = 0x400;

// === Global utility functions ===
function random(n) { return Math.floor(Math.random() * n); }
function time() { return Math.floor(Date.now() / 1000); }
// Synchronet's ascii() is overloaded: ascii(number) returns the char,
// ascii(string) returns the code of the first char. tw2/input.js calls
// ascii(key) to filter control chars with `if(ascii(key)<32) break;` —
// the number-only version made that comparison a silent no-op.
function ascii(x) {
    if (typeof x === 'string') return x.charCodeAt(0);
    if (typeof x === 'number') return String.fromCharCode(x);
    if (x == null) return 0;
    var s = String(x);
    return s.length ? s.charCodeAt(0) : 0;
}
function ascii_str(s) { return s.charCodeAt(0); }

// Synchronet's md5_calc(str, hex) -- real signature confirmed by
// usage across bundled doors (Thirstyville's playerID/drinkProperty
// hashing runs at load time, before main(); LORD's lordsrv.js also
// calls it): hex=true returns the standard 32-char lowercase hex MD5
// digest; hex=false/omitted returns the digest base64-encoded,
// matching Synchronet's own base64_encode() convention used elsewhere
// in its JS API. Was completely absent from this shim -- Thirstyville
// would ReferenceError immediately on load, before any door-specific
// code even ran.
function md5_calc(str, hex) {
    var hash = _node_require('crypto').createHash('md5').update(String(str), 'utf8').digest();
    return hex ? hash.toString('hex') : hash.toString('base64');
}

// Synchronet's crc16_calc(str) -- needed for MsgBase.get_index()'s own
// idx.to/idx.subject fields (real Synchronet's message index stores
// these as CRC16 hashes, not raw text, so doors compare a locally-
// computed CRC against the index entry as a fast pre-filter before
// fetching a full header -- Minesweeper's own get_winners() does
// exactly this: `var to_crc = crc16_calc(title.toLowerCase());`).
// Standard CRC-16/XMODEM (poly 0x1021, init 0, non-reflected) -- the
// commonly-documented real Synchronet variant. Getting this exactly
// byte-for-byte identical to the real C implementation is NOT required
// for correctness here: MsgBase.get_index() (see below) computes
// idx.to/idx.subject by calling this SAME function on raw text the
// bridge returns, rather than trying to reproduce a hash some other
// process already computed -- so both sides of every comparison always
// use the identical algorithm and agree with each other regardless of
// whether it matches upstream Synchronet's own C source exactly. No
// real cross-system CRC compatibility is needed (nothing here reads a
// CRC value computed by a different, real Synchronet install).
function crc16_calc(str) {
    var s = String(str == null ? '' : str);
    var crc = 0;
    for (var i = 0; i < s.length; i++) {
        crc ^= (s.charCodeAt(i) & 0xFF) << 8;
        for (var b = 0; b < 8; b++) {
            crc = (crc & 0x8000) ? ((crc << 1) ^ 0x1021) : (crc << 1);
            crc &= 0xFFFF;
        }
    }
    return crc;
}

// Real message-base access -- MsgBase(sub), where `sub` is an echo
// area's tag. Backed by anetbbs/games/msgbase_bridge.py (a new,
// synchronous CLI process, spawned once per call via spawnSync -- same
// established pattern as console.exec's own child_process.spawnSync
// use a few hundred lines up) since there's no other path from this
// Node subprocess back into ANetBBS's real Flask/SQLAlchemy echomail
// data. Built specifically to make Minesweeper's own real InterBBS
// DOVE-Net score-sharing work (its own MsgBase(options.sub) calls,
// options.sub resolved via modopts.ini -- see door_runner.py's own
// comment on that), but not Minesweeper-specific itself -- any door
// using MsgBase against a real configured echo area works the same way.
//
// Only implements the subset of the real MsgBase API doors calling
// through this shim actually use: open/close/save_msg/get_index/
// get_msg_header/get_msg_body, plus the .cfg/.last_msg properties
// Minesweeper reads directly. Real Synchronet's MsgBase has a much
// larger surface (message groups, config-only mode, etc) -- not
// implemented, matching this project's "extend reactively" convention
// for the compat shim generally.
function MsgBase(sub) {
    this._sub = String(sub == null ? '' : sub);
    this._opened = false;
    this.last_msg = 0;
    this.cfg = { data_dir: js.exec_dir, code: this._sub };
}
MsgBase.prototype._call = function(op) {
    var extraArgs = Array.prototype.slice.call(arguments, 1);
    try {
        var cp = _node_require('child_process');
        var argv = [js.msgbase_bridge, op, this._sub].concat(
            extraArgs.map(function(a) { return String(a); }));
        var r = cp.spawnSync(js.python_bin, argv, {encoding: 'utf8'});
        if (r.error) {
            return {ok: false, error: String(r.error)};
        }
        if (r.status !== 0) {
            return {ok: false, error: 'msgbase_bridge exited ' + r.status +
                    ': ' + (r.stderr || '')};
        }
        return JSON.parse(r.stdout || '{}');
    } catch (e) {
        return {ok: false, error: String(e && e.message ? e.message : e)};
    }
};
// Real Synchronet: open() returns true/false, and MUST be called before
// other methods are meaningful -- Minesweeper's own get_winners() does
// `if(msgbase.get_index !== undefined && msgbase.open())`. this.cfg/
// this.last_msg are real properties Minesweeper reads directly
// afterward (cfg.data_dir + cfg.code + ".ini" for its own import_ptr
// tracking file).
MsgBase.prototype.open = function() {
    var result = this._call('open');
    if (!result.ok) { return false; }
    this._opened = true;
    this.last_msg = result.last_msg || 0;
    this.cfg = { data_dir: js.exec_dir, code: this._sub };
    return true;
};
MsgBase.prototype.close = function() { this._opened = false; };
MsgBase.prototype.save_msg = function(hdr, body) {
    var payload = JSON.stringify({
        to: (hdr && hdr.to) || '',
        from: (hdr && hdr.from) || '',
        subject: (hdr && hdr.subject) || '',
        body: String(body == null ? '' : body),
    });
    var result = this._call('save_msg', payload);
    return !!result.ok;
};
// Real Synchronet's index entries carry to/subject as CRC16 hashes, not
// raw text -- doors compare a locally-computed CRC against these as a
// fast pre-filter before fetching a full header (see crc16_calc's own
// comment for why computing the hash HERE, from the bridge's raw text,
// rather than trying to reproduce a hash computed elsewhere, is what
// makes this correct without needing to match real Synchronet's exact
// C implementation). attr is always 0 -- EchomailMessage has no
// soft-delete column, so nothing a door checks against MSG_DELETE is
// ever actually set.
MsgBase.prototype.get_index = function() {
    var result = this._call('get_index', 0);
    if (!result.ok || !result.entries) { return []; }
    return result.entries.map(function(e) {
        return {
            number: e.number,
            offset: e.number,   // no separate on-disk offset concept here
            to: crc16_calc(String(e.to || '').toLowerCase()),
            subject: crc16_calc(String(e.subject || '').toLowerCase()),
            attr: 0,
        };
    });
};
// byOffset is accepted for real-signature compatibility but ignored --
// this shim's "offset" and "number" are the same value (see get_index
// above), so there's nothing to distinguish.
MsgBase.prototype.get_msg_header = function(byOffset, offsetOrNumber) {
    var result = this._call('get_header', offsetOrNumber);
    if (!result.ok) { return undefined; }
    return result.header;
};
// Real signature takes extra formatting flags (strip-quotes etc) --
// this shim always returns the raw stored body; callers needing their
// own cleanup (Minesweeper's own get_winners() does its own
// body.split("\n===",1)[0] trimming) already do it themselves.
MsgBase.prototype.get_msg_body = function(hdr) {
    var result = this._call('get_body', hdr && hdr.number);
    if (!result.ok) { return ''; }
    return result.body;
};

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
    // precision (.digits or .*), and types s/d/i/u/f/x/X/c/%.
    // DSR uses `printf("%*s", width, str)` — without `*` support that
    // shows up as literal "%*s" in the output and the gallery list is
    // unaligned.
    //
    // 'u' (unsigned decimal) was missing from the type class entirely --
    // real bug found live auditing Minesweeper: its game-clock display
    // (calc_time(): format("%2u:%02u", ...)) and every scoreboard column
    // (width/height/mine counts: format("%3u...%2u...%-3u", ...)) use
    // %u throughout. With 'u' absent from the regex's character class,
    // those tokens never matched at all and were left as literal text --
    // the on-screen timer would have shown "%2u:%02u" instead of a real
    // "05:23", and every scoreboard row similarly broken.
    var args = Array.prototype.slice.call(arguments);
    var fmt = String(args.shift());
    return fmt.replace(/%(-?)(0?)(\*|\d+)?(?:\.(\*|\d+))?([sdiufxXc%])/g,
        function(m, flags, zero, w, p, t) {
            if (t === '%') return '%';
            var width = (w === '*') ? Number(args.shift()) : (w ? parseInt(w, 10) : 0);
            var prec  = (p === '*') ? Number(args.shift()) : (p ? parseInt(p, 10) : -1);
            var v = args.shift();
            var out;
            if (t === 'd' || t === 'i' || t === 'u') out = String(parseInt(v, 10));
            else if (t === 'f') out = (prec >= 0) ? parseFloat(v).toFixed(prec) : String(parseFloat(v));
            else if (t === 'x') out = (parseInt(v, 10) >>> 0).toString(16);
            else if (t === 'X') out = (parseInt(v, 10) >>> 0).toString(16).toUpperCase();
            else if (t === 'c') out = String.fromCharCode(parseInt(v, 10));
            else { out = String(v == null ? '' : v); if (prec >= 0) out = out.slice(0, prec); }
            if (width > out.length) {
                var padChar = (zero === '0' && flags !== '-' && t !== 's') ? '0' : ' ';
                var pad = padChar.repeat(width - out.length);
                out = (flags === '-') ? (out + pad) : (pad + out);
            }
            return out;
        });
}
// Real Synchronet globals (js_global.cpp's js_b64_encode/js_b64_decode)
// -- confirmed against the real source, not guessed: standard base64
// alphabet (not URL-safe), and the C implementation only ever reads
// argv[0] -- a second argument (some doors pass `true`, e.g.
// chickendelivery.js's `base64_encode(uid, true)`) is silently
// ignored by real Synchronet too, so accepting-and-ignoring it here
// matches real behavior exactly rather than being a shortcut.
// 'binary' (not 'utf8') matches the C code's raw-byte semantics --
// same encoding name this file already uses elsewhere for the same
// reason (see e.g. the PTY output-write path above).
function base64_encode(str) {
    if (str === undefined || str === null) return null;
    return Buffer.from(String(str), 'binary').toString('base64');
}
function base64_decode(str) {
    if (str === undefined || str === null) return null;
    return Buffer.from(String(str), 'base64').toString('binary');
}
// Real Synchronet global (js_global.cpp's js_ctrl) -- confirmed
// against the real source: accepts a single character (or numeric
// code), computes `toupper(ch) & ~0x40` (clears bit 6, the standard
// Ctrl+letter mapping -- 'A' 0x41 -> 0x01, 'C' 0x43 -> 0x03, etc.),
// and returns it as a ONE-CHARACTER STRING (not a number) -- matches
// what console.inkey()/getcmd() return, since callers compare against
// it directly (e.g. `case ctrl('A'):` in frame.js's key handler).
function ctrl(ch) {
    var code = (typeof ch === 'string') ? ch.charCodeAt(0) : Number(ch);
    code = String.fromCharCode(code).toUpperCase().charCodeAt(0) & ~0x40;
    return String.fromCharCode(code);
}
function truncsp(s) { return String(s).replace(/\s+$/, ''); }
// Real Synchronet global (js_global.cpp's js_skipsp) -- the mirror
// image of truncsp() above: strips LEADING whitespace instead of
// trailing. Gap found live: Jeopardized's lib/frame-ext.js (bundled,
// reused by other doors' own layout code) calls
// `skipsp(truncsp(word))` when centering wrapped text, and no
// implementation of this existed anywhere in the shim -- only
// truncsp() did.
function skipsp(s) { return String(s).replace(/^\s+/, ''); }
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
// file_exists/file_getname/directory are NOT redeclared here on purpose.
// This file used to carry a second, weaker copy of all three right at
// this spot -- `function` redeclarations at the same scope silently
// shadow the EARLIER (better) one, exactly the "duplicate keys shadow
// the originals" trap already documented elsewhere in this file for
// object literals. Real bug found auditing Minesweeper: its top-level
// catch-all handler does `file_getname(e.fileName)` -- a real Synchronet
// (SpiderMonkey) Error has `.fileName`, but a plain V8/Node Error does
// not, so e.fileName is undefined here. The earlier, real definition
// (file_getname(p) { return _path.basename(String(p)); }`, up with the
// other file_* helpers) coerces that safely; this shadowing duplicate
// (`_path.basename(path)`, no coercion) threw a TypeError instead,
// turning a graceful in-door `alert(msg)` error report into a raw
// uncaught crash. The duplicate `directory()` here was also strictly
// worse than the earlier one (no try/catch around the dirname() call,
// no literal-dot escaping in the glob-to-regex conversion -- doors
// matching e.g. "*.bin" would have "." wrongly match any character,
// not just a literal dot -- and no case-insensitive flag), and the
// duplicate `file_exists()` dropped the try/catch that keeps a
// non-string/undefined path from throwing instead of just returning
// false. All three real, better versions already exist earlier in this
// file (see the other file_* helpers) — just don't re-declare them.
// (log/alert defined earlier — handle both log(msg) and log(level, msg))

// Common helpers many doors expect at global scope (Synchronet ships them
// either as builtins or via `load("standard.js")`; we provide them directly).
function clearScreen() { return console.clear(); }
function home()        { return console.home(); }
function cleartoeol()  { return console.cleartoeol(); }
function clearline()   { return console.clearline(); }
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
        'file_cfgname',
        'file_remove', 'file_rename', 'file_copy', 'file_getname', 'file_getext',
        'file_mutex', 'file_touch',
        'mkdir', 'rmdir', 'directory',
        'lfexpand', 'backslash',
        'console', 'bbs', 'user', 'system', 'js', 'server', 'client',
        'file_area', 'msg_area', 'File', 'User', 'Socket',
        // Queue — Synchronet inter-script FIFO; needed by dorkit.js
        // line 273 (`new Queue("dorkit_input"...)`) when it's loaded
        // via vm.runInThisContext from inside our load() function.
        // Without exposing it to global, dorkit sees ReferenceError.
        'Queue',
        '_fs', '_path', '_readline', '_node_require',
        // Common helper names many doors expect (alias of console.X):
        'clearScreen', 'home', 'cleartoeol', 'clearline', 'gotoxy', 'crlf', 'pause',
        'format', 'sprintf', 'printf', 'print', 'write', 'writeln',
        // Top-level Synchronet globals doors and load()'d helpers reach for:
        'exit', 'mswait', 'sleep', 'random', 'time', 'ascii', 'ascii_str',
        'truncsp', 'skipsp', 'strip_ctrl', 'word_wrap', 'strftime',
        // base64_encode/decode + ctrl -- real Synchronet globals
        // (js_global.cpp), needed once frame.js's key-handling code
        // (`case ctrl('A'):`) and any door computing a base64 id
        // (e.g. chickendelivery.js's uid) actually run via
        // vm.runInThisContext -- confirmed live: declaring the
        // function alone isn't enough, it has to be in THIS list too.
        'base64_encode', 'base64_decode', 'ctrl',
        // md5_calc — same "declaring it isn't enough" gotcha as
        // base64_encode above; Thirstyville's player.js calls it on
        // its very first line, at load time.
        'md5_calc',
        // crc16_calc — same gotcha; Minesweeper's own get_winners()
        // calls it to compute to_crc/winner_crc for its MsgBase index
        // pre-filter.
        'crc16_calc',
        // MsgBase — real message-base access (see the class definition
        // and its own comment for the design). Same registration
        // requirement as every other name in this list.
        'MsgBase',
        // argv/argc — DSR reads argv[0] for an image path argument
        'argv', 'argc',
        // BG_HIGH/BLINK — same "declaring it in the outer template's own
        // scope isn't enough" gotcha as base64_encode/ctrl/md5_calc
        // above. Real bug found live bundling Minesweeper: its very
        // first executable line reads BG_HIGH, and the ambient
        // cga_defs.js preload (a few lines below, `load('cga_defs.js')`)
        // does NOT reliably supply it -- load()'s own path search
        // prefers sbbs_stubs/dorkit/cga_defs.js (an older real
        // Synchronet revision bundled for LORD's dorkit) over the flat
        // sbbs_stubs/cga_defs.js copy, and that older revision calls the
        // same 0x400 bit `BG_BRIGHT`, not `BG_HIGH` -- two genuinely
        // different real upstream revisions, vendored at different
        // paths. Registering both names here makes them real globalThis
        // properties unconditionally, independent of which cga_defs.js
        // variant happens to win the path search.
        'BG_HIGH', 'BLINK',
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

// Real Synchronet always has the CGA-style color constants (GREEN,
// LIGHTGREEN, HIGH, BG_RED, etc. -- cga_defs.js) available to every
// door without it needing to load() that file itself -- they're just
// part of the ambient environment. Confirmed live: Bubble Boggle's
// game.js uses GREEN/LIGHTGREEN/CYAN/HIGH/BG_GREEN/etc. directly, but
// its own load() chain (graphic.js, sbbsdefs.js, funclib.js,
// calendar.js) never happens to pull in cga_defs.js -- only
// ansiterm_lib.js/avatar_lib.js do, neither of which this door
// touches. Rather than special-case this door (or wait for the next
// one to hit the same gap), load cga_defs.js here once, up front, so
// every door gets the real ambient behavior automatically -- matches
// how load() already makes a file's own top-level `var`s real
// globalThis properties, visible to every script loaded afterward.
try { load('cga_defs.js'); } catch (e) {}

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
    # Default to 1 (not 0): tw2's LoadPlayer scans players[] for a record
    # with UserNumber == user.number; the empty-slot stubs all start with
    # UserNumber == 0, so user.number == 0 would silently match the first
    # stub and DeletePlayer would crash on the plain-object record.
    user_id = _u('id') or 1
    security_level = 255 if _u('is_admin') else 50
    login_count = _u('login_count') or 0
    user_location = _u('location') or ''
    # Real User.date_of_birth (a Python date for the SQLAlchemy model
    # path, possibly a plain string for the dict-based telnet/SSH/
    # rlogin session path) -- feeds bbs.compare_ars()'s real AGE check
    # (see that function's own comment in the template). Empty string
    # when unset/unknown, which AGE checks deliberately treat as
    # failing rather than passing.
    _dob = _u('date_of_birth')
    if _dob is None:
        user_birthdate = ''
    elif hasattr(_dob, 'isoformat'):
        user_birthdate = _dob.isoformat()
    else:
        user_birthdate = str(_dob)

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
    # Scratch defaults for an unconfigured Synchronet-compat door;
    # game.synchronet_exec_dir/script_path (admin-configured) are used
    # when set.
    exec_dir = _absroot(game.synchronet_exec_dir or '/tmp/sbbs/exec')  # nosec B108
    game_dir = _absroot(os.path.dirname(
        game.synchronet_script_path or '/tmp') or '/tmp')  # nosec B108
    data_dir = os.path.join(os.path.dirname(exec_dir), 'data')
    text_dir = os.path.join(os.path.dirname(exec_dir), 'text')
    mods_dir = os.path.join(os.path.dirname(exec_dir), 'mods')
    # Stubs dir ships with anetbbs (provides sbbsdefs.js etc) so doors
    # `load("sbbsdefs.js")` works without a full Synchronet install.
    stubs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sbbs_stubs')
    # MsgBase's real backing -- msgbase_bridge.py, invoked via spawnSync
    # from the JS MsgBase class. sys.executable (not a bare 'python3' on
    # PATH) so the bridge runs under the exact same interpreter/venv
    # ANetBBS itself is running under, guaranteeing its own deps
    # (Flask/SQLAlchemy) are actually importable.
    msgbase_bridge = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  'msgbase_bridge.py')
    python_bin = sys.executable or 'python3'

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
        .replace('{USER_LOCATION}', _q(user_location)) \
        .replace('{USER_BIRTHDATE}', _q(user_birthdate)) \
        .replace('{USER_IP}', _q('127.0.0.1')) \
        .replace('{BBS_NAME}', _q(bbs_name)) \
        .replace('{NODE_NUMBER}', str(node_number)) \
        .replace('{EXEC_DIR}', _q(exec_dir)) \
        .replace('{GAME_DIR}', _q(game_dir)) \
        .replace('{STUBS_DIR}', _q(stubs_dir)) \
        .replace('{DATA_DIR}', _q(data_dir)) \
        .replace('{TEXT_DIR}', _q(text_dir)) \
        .replace('{MODS_DIR}', _q(mods_dir)) \
        .replace('{MSGBASE_BRIDGE}', _q(msgbase_bridge)) \
        .replace('{PYTHON_BIN}', _q(python_bin))

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
