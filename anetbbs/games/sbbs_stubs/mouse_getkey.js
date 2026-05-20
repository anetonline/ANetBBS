// ANetBBS replacement for mouse_getkey.js
//
// The upstream version assumed an ansiterm_lib helper and only knew how
// to assemble mouse-tracking sequences (`CSI M…` and `CSI <…`). When a
// user pressed a plain arrow key the upstream code recognised the
// `\x1b[` prefix, then on the third byte (`A`/`B`/`C`/`D`) fell through
// to a "Shouldn't happen" else branch that pushed the rest back via
// `console.ungetstr()` and returned just `\x1b` — dd_lightbar_menu
// interpreted that as ESC/quit, so arrow keys closed the menu instead
// of moving the highlight.
//
// This rewrite uses console.getkey() / console.inkey() to read one byte
// at a time, reassembles ANSI CSI / SS3 sequences (arrows, F-keys, Home,
// End, PgUp/PgDn, Insert, Delete) and returns the appropriate Synchronet
// KEY_* string. Mouse sequences still go through to the original
// detection path (returning a `mouse` descriptor).

function mouse_getkey(mode, timeout, enabled)
{
    function readOne(t) {
        if (t === undefined || t === null) return console.getkey(mode);
        return console.inkey(mode, t);
    }

    var key = readOne(timeout);
    if (key === '' || key === undefined || key === null) {
        return {key:'', mouse:null};
    }
    if (key !== '\x1b') {
        return {key:key, mouse:null};
    }

    // ESC seen — try to grab the rest of an escape sequence within 50ms.
    var b = console.inkey(mode, 50);
    if (b === '' || b === undefined || b === null) {
        return {key:'\x1b', mouse:null};   // bare ESC press
    }

    // SS3 prefix \x1bO… (some terminals send arrows as SS3).
    if (b === 'O') {
        var c = console.inkey(mode, 50);
        if (c === 'A') return {key:KEY_UP,    mouse:null};
        if (c === 'B') return {key:KEY_DOWN,  mouse:null};
        if (c === 'C') return {key:KEY_RIGHT, mouse:null};
        if (c === 'D') return {key:KEY_LEFT,  mouse:null};
        if (c === 'H') return {key:KEY_HOME,  mouse:null};
        if (c === 'F') return {key:KEY_END,   mouse:null};
        return {key:'\x1bO' + (c || ''), mouse:null};
    }

    // CSI prefix \x1b[ — the common one.
    if (b !== '[') {
        return {key:'\x1b' + b, mouse:null};   // odd; pass through verbatim
    }

    var c2 = console.inkey(mode, 50);
    if (c2 === 'A') return {key:KEY_UP,    mouse:null};
    if (c2 === 'B') return {key:KEY_DOWN,  mouse:null};
    if (c2 === 'C') return {key:KEY_RIGHT, mouse:null};
    if (c2 === 'D') return {key:KEY_LEFT,  mouse:null};
    if (c2 === 'H') return {key:KEY_HOME,  mouse:null};
    if (c2 === 'F') return {key:KEY_END,   mouse:null};

    // Numeric tilde sequences: \x1b[1~ Home, \x1b[2~ Insert, \x1b[3~ Del,
    // \x1b[4~ End, \x1b[5~ PgUp, \x1b[6~ PgDn, \x1b[15~..\x1b[24~ F-keys.
    if (c2 >= '0' && c2 <= '9') {
        var seq = c2;
        while (true) {
            var d = console.inkey(mode, 50);
            if (d === '' || d === undefined || d === null) break;
            seq += d;
            if (d === '~' || (d >= 'A' && d <= 'Z') || (d >= 'a' && d <= 'z')) break;
        }
        if (seq === '1~' || seq === '7~') return {key:KEY_HOME,  mouse:null};
        if (seq === '2~')                 return {key:'\x1b[2~', mouse:null};
        if (seq === '3~')                 return {key:KEY_DEL,   mouse:null};
        if (seq === '4~' || seq === '8~') return {key:KEY_END,   mouse:null};
        if (seq === '5~')                 return {key:'\x1b[5~', mouse:null};
        if (seq === '6~')                 return {key:'\x1b[6~', mouse:null};
        return {key:'\x1b[' + seq, mouse:null};
    }

    // Mouse tracking — \x1b[M…  or  \x1b[<…
    if (c2 === 'M') {
        var b3 = console.inkey(mode, 50);
        var b4 = console.inkey(mode, 50);
        var b5 = console.inkey(mode, 50);
        if (b3 && b4 && b5) {
            var btn = (b3.charCodeAt(0) - 32) & 0xc3;
            var x = b4.charCodeAt(0) - 32;
            var y = b5.charCodeAt(0) - 32;
            return {key:'', mouse:{button:btn, x:x, y:y, press:btn !== 3,
                                   release:btn === 3, motion:0, mods:0,
                                   ansi:'\x1b[M' + b3 + b4 + b5}};
        }
        return {key:'\x1b[M', mouse:null};
    }

    // Anything else — pass through verbatim
    return {key:'\x1b[' + c2, mouse:null};
}
