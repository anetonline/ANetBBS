// Headless TW2 maintenance — runs RunMaint() without a player session.
//
// Spawned by the BBS's daily scheduler (anetbbs/games/tw2_maint.py) so
// the Cabal moves and inactive-player sweep happen on a fixed cadence
// instead of "next time someone logs in." Loads under the same Node +
// compat-shim toolchain as the interactive door, but with the bits that
// require a live TTY stubbed out.
//
// This file is prepended onto the regular compat shim by tw2_maint.py
// to produce one combined script — same pattern as door_runner.py's
// interactive flow, just with `main()` replaced by RunMaint().

(function () {
    // The compat shim defines `console` as a sysop/user-facing surface.
    // Maintenance only emits status lines; route them to stderr so the
    // sysop can see "ran ok" without spamming stdout.
    if (typeof console !== 'undefined') {
        var noop = function () {};
        // Stash the originals just in case maint references them.
        console.write    = function (s) { try { process.stderr.write(String(s || '')); } catch (_) {} };
        console.writeln  = function (s) { try { process.stderr.write(String(s || '') + '\n'); } catch (_) {} };
        console.crlf     = function () { try { process.stderr.write('\n'); } catch (_) {} };
        console.print    = console.write;
        console.pause    = noop;
        console.getkey   = function () { return ''; };
        console.getstr   = function () { return ''; };
        console.cleartoeol = noop;
        console.aborted  = false;
        console.attributes = '';
        console.line_counter = 0;
        console.saveline = noop;
        console.restoreline = noop;
    }

    // Synthesize a sysop-flavoured `user` so DeleteInactive's
    // `user.alias` reference resolves. Maintenance doesn't credit
    // anyone for sweeps, but it logs the operator name.
    if (typeof globalThis !== 'undefined' && !globalThis.user) {
        globalThis.user = {
            number: 1, alias: 'Maintenance', name: 'Maintenance',
            handle: 'Maintenance', security: { level: 99 },
        };
    }
})();

// After this prologue, door_runner-style concatenation appends tw2.js.
// We then override main() to call RunMaint() and exit.
globalThis.__ANETBBS_TW2_MAINT__ = true;
