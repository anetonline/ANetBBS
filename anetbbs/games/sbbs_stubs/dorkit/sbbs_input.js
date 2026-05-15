// ANetBBS Node-friendly replacement for Synchronet's sbbs_input.js.
//
// Upstream uses a forever-loop in a background JS thread to read stdin
// via `console.inkey(0, 100)` and push bytes into ansi_input.js (`ai.add`).
// Node has no JS threads. Strategy: switch stdin to non-blocking ONCE
// (stty min 0 time 1 — readSync returns 0 if no data within 100 ms),
// then register a callback on dk.console.input_queue_callback that does
// a single non-blocking readSync per dorkit waitkey() iteration and
// forwards the byte through ai.add(). dorkit's busy-loop drives the
// polling cadence; we don't have to do our own timer.

js.load_path_list.unshift(js.exec_dir + 'dorkit/');
if (typeof system !== 'undefined' && system.exec_dir) {
    js.load_path_list.unshift(system.exec_dir + 'dorkit/');
}
load('ansi_input.js', argv[0]);

// ansi_input.js created its own Queue named "dorkit_input"+argv[0].
// dorkit.js created one named "dorkit_input"+bbs.node_num. Our argv
// is typically empty here so ansi_input picked "dorkit_input" (no
// suffix) and dorkit picked "dorkit_input1" — DIFFERENT names, so
// the queue-name cache returns separate instances and bytes that
// ai.add() writes never reach the queue dorkit polls. Force them
// to share the SAME instance regardless of name games.
if (typeof ai !== 'undefined' && typeof dk !== 'undefined'
        && dk.console && dk.console.input_queue) {
    ai.input_queue = dk.console.input_queue;
}

// One-time stty flip to "non-blocking with 0.1 s timeout per read".
// stty `min 0 time 1` means: readSync(fd, buf, 0, N) returns
// immediately with whatever bytes are ready, or after 0.1 s waits 0 bytes.
// This makes our per-iteration callback fast even when no input arrives.
(function () {
    var cp = (typeof _node_require === 'function')
        ? _node_require('child_process')
        : require('child_process');
    try {
        cp.execSync('stty min 0 time 1 < /dev/tty 2>/dev/null',
                    {stdio: 'ignore'});
    } catch (_) {
        try { cp.execSync('stty min 0 time 1', {stdio: 'ignore'}); }
        catch (_2) {}
    }
})();

// Caller does `var input_queue = load(true, "sbbs_input.js", bbs.node_num)`
// — our patched load() returns a stub Queue.
var parent_queue = new Queue('parent_queue_' + (argv[0] || 0));

if (typeof dk !== 'undefined' && dk.console
        && Array.isArray(dk.console.input_queue_callback)) {
    var _fs_lib = (typeof _fs !== 'undefined') ? _fs : require('fs');
    var _readbuf = Buffer.alloc(1);
    dk.console.input_queue_callback.push(function () {
        try {
            var n = _fs_lib.readSync(0, _readbuf, 0, 1, null);
            if (n > 0 && typeof ai !== 'undefined') {
                ai.add(String.fromCharCode(_readbuf[0]));
            }
        } catch (_) { /* EOF / no tty */ }
        return undefined;  // dorkit will poll the queue itself after callbacks
    });
}
