"""Regression test for real crashes found live playtesting Chicken
Delivery on the Pi3 test bed (the first real interactive door to
actually run through the Node.js compat fallback far enough to reach
these code paths):

    ReferenceError: base64_encode is not defined
    ReferenceError: ctrl is not defined
    TypeError: console.getkeys is not a function

`base64_encode`/`base64_decode` and `ctrl` are real Synchronet globals
(js_global.cpp) that chickendelivery.js and frame.js call directly;
`console.getkeys` is a real Synchronet console method chickendelivery.js
uses for its quit-confirmation popup. None existed in the compat shim
at all.

Fixing `base64_encode`/`ctrl` surfaced a SECOND, more fundamental bug:
declaring a bare top-level `function` in synchronet_compat.py's
generated script is not enough for code loaded via the compat shim's
own `load()` (which uses `vm.runInThisContext`, running with
`globalThis` as scope) to see it -- Node's CommonJS module wrapper
means top-level declarations in the OUTER script are scoped to the
module wrapper, not `globalThis`. The project already has a
deliberate, working fix for exactly this (`_registerGlobals()`'s
`names` allowlist) -- the bug was simply that the two new functions
weren't added to that list. This test guards both class of bug: the
functions must be defined AND actually visible to `load()`'d code, not
just present in the outer script's own scope.

A fourth real bug from the SAME playtest session (reported after the
above three were fixed and the door became fully playable): the game
loaded and played correctly but rendered entirely in monochrome, no
color at all. Root cause: `console.attributes` was a plain data
property (`attributes: 7`) with no setter side effect, but real
Synchronet's `console.attributes` is a LIVE property -- assigning to
it immediately changes the terminal's active color, which is exactly
what `frame.js`'s `Display.__drawChar__` does before painting every
single character (`console.attributes = attr;`). The assignment was
silently swallowed; every character painted with whatever the
terminal's last real SGR state happened to be. Fixed by converting it
to a real `get`/`set` accessor pair that emits the same ANSI sequence
`console.attr(n)` already did (and now delegates to, avoiding a
double-write). Confirmed live: a real end-to-end playtest went from 0
color escape sequences in the output stream to 18 distinct real SGR
color combinations.

A fifth real bug, reported once color made the door fully playable:
pressing an arrow key during actual gameplay acted exactly like
pressing Escape (fired the quit-confirmation popup), and the main
menu's up/down navigation didn't respond to arrow keys at all. Root
cause: `console.getkey()` already correctly translated a real
terminal's arrow-key escape sequence (`\x1b[A` etc.) into the single-
byte KEY_* control codes Synchronet doors compare against (see
key_defs.js) -- but `console.inkey()`, a SEPARATE function, did not;
it just returned the bare first byte with no translation at all. Both
chickendelivery.js's own main loop and tree.js's menu navigation call
`inkey()`, not `getkey()` -- so a real arrow key's leading `\x1b` byte
looked exactly like a standalone Escape press to the door, which is
precisely why pressing an arrow during gameplay fired the quit popup
(`ascii(userInput) == 27`) and why menu navigation didn't respond
(tree.js was comparing against `\x1e`/`\x0a` which never arrived).
Fixed by factoring the escape-sequence resolution out of `getkey()`
into a shared `_resolveKey()` helper both functions now use.

A sixth real bug, reported once arrow-key input worked: the menu
navigated fine, but starting an actual game showed a completely
frozen screen -- not even enemies moved. Root cause: `system.timer`
(a real Synchronet global, `js_system.cpp`'s `SYS_PROP_TIMER`, backed
by `xp_timer()` -- a monotonic, continuously-advancing clock in
fractional seconds) was entirely missing from the compat shim's
`system` object. `sprite.js`'s own movement gating throughout is
`system.timer - this.lastMove > this.ini.speed` -- with `system.timer`
reading as `undefined`, every such comparison was `NaN > speed`,
always false, so sprites and enemies never moved at all. The HUD
countdown timer (a completely separate mechanism, event-timer.js's
`Timer` class) worked fine the whole time, which is why the screen
wasn't fully frozen -- just gameplay itself. Fixed by adding a real
`get timer()` accessor (not a plain number -- code re-reads it many
times per frame expecting a fresh value each time, same class of fix
as `console.attributes`) backed by `process.hrtime.bigint()`, Node's
own monotonic clock. Confirmed live: two reads 300ms apart differed by
~0.30, matching real elapsed time.

A seventh through tenth bug class, found via a proactive real-source-
verified compat-shim audit for Bubble Boggle (The BRoKEN BUBBLe
Software) BEFORE any live playtest, following the exact same
methodology the six bugs above were each individually discovered and
fixed with:

  7. `File.prototype.eof` was a plain method, not a live property --
     `while (!dict.eof)` (Bubble Boggle's own dictionary scanner) is
     always truthy against a bare function reference, so the loop
     would spin forever reading past EOF. Also, `File.prototype.rewind`
     didn't exist at all, though the same scanner calls it at the top
     of every lookup. Fixed the same way as `.position`/`.length`.
  8. `console.getnum(maxnum, dflt)` (real js_console.cpp's js_getnum)
     was completely missing -- Bubble Boggle's changeDate() calls it.
  9. `file_cfgname(path, fname)` (real js_global.cpp's js_cfgfname /
     xpdev/ini_file.c's iniFileName(), hostname-override config
     resolution) was completely missing -- boggle.js's own entry point
     uses it for `new File(file_cfgname(root, "server.ini"))`.
 10. The compat shim's `load(scope, "file")` two-argument form never
     actually populated `scope` at all -- it only returned the vm
     execution's completion value, which many callers (including
     graphic.js's real, vendored `Graphic.prototype.defs = {};
     load(Graphic.prototype.defs, "cga_defs.js");`) discard entirely,
     expecting `scope` itself to be mutated in place. `Graphic.draw()`
     (used by Bubble Boggle's Lobby/GameBoard classes) depends on
     `this.defs.RED` etc. resolving correctly. Fixed by extracting the
     loaded file's own top-level `var`/`const`/`function` names via
     regex and copying their post-execution values from `globalThis`
     onto `scope` -- works for both calling conventions regardless of
     whether the loaded file follows the "Leave as last line: this;"
     convention.

A separate, related discovery from the same audit: Bubble Boggle's own
game.js (the door's OWN source, not a vendored library) has one real
`for each (var p in this.players)` occurrence in storeRoundWinner() --
a parse-time SyntaxError under Node regardless of whether that branch
executes. Since doors stay byte-for-byte unmodified on disk, this is
NOT fixed by editing the door; the fix (`_polyfillE4XForEach()`) lives
inside the compat shim's own `load()` in synchronet_compat.py instead,
applied to every file it reads (a door's own top-level script AND
anything it subsequently loads) -- see
test_for_each_e4x_syntax_in_a_loaded_door_file_is_polyfilled below.

An 11th and 12th real bug, found from Jerry's first actual Pi3 test of
Bubble Boggle (the proactive audit above got the door to LOAD and
reach a real network call, but didn't catch these two -- both needed
real hardware to surface):

 11. `client.lock(scope,"month",2); ...; client.read(scope,"month");
     ...; client.unlock(scope,"month");` -- game.js's own documented
     usage pattern -- hung until a real 30-second recv timeout
     ("connection error: timed out"). Root cause confirmed against the
     real Synchronet server source (json-db.js): a bare read/write
     requires the SAME connection/client identity to have already
     locked that record, but this shim's json-client.js opens a fresh,
     independent TCP connection for every single call (documented
     difference #1 in that file) -- a lock() sent on one connection is
     invisible to a read() on a different one. Chicken Delivery never
     hit this because it always passes `lock` inline to read/write
     (which the real server DOES correctly auto-wrap into an atomic
     per-request LOCK+op+UNLOCK, confirmed in the same source) and
     never calls lock()/unlock() as separate, standalone calls. Fixed
     in json-client.js: lock()/unlock() become pure local bookkeeping
     (no network call at all, so no stray real lock is ever left on
     the shared production server), and every data operation that
     doesn't receive its own explicit lock argument automatically
     picks up whichever currently-tracked lock covers its location
     (exact match, or nested under a locked parent -- matching the
     real server's own hierarchical lock inheritance) -- see
     test_jsonrpc_client_js_shim.py's lock-tracking tests.
 12. `Graphic.prototype.draw(xpos,ypos,width,height,xoff,yoff,cons)`
     crashed with `TypeError: Cannot read properties of undefined
     (reading 'cols')` -- game.js's own `open()` calls `splash.draw();`
     with zero arguments, so `cons` is undefined. Confirmed against
     the real exec/dorkit/graphic.js source that `cons` has no
     fallback there either -- a genuine bug in the door's own source
     that would crash real Synchronet too, not something fixable by
     editing the door. Compat-layer fix in dorkit/graphic.js: default
     `cons` to the global `console` when omitted -- the obvious,
     harmless interpretation of what an omitted `cons` was always
     going to mean.

A 13th real bug, reported once the door was confirmed working end to
end: the welcome/splash screen (boggle.bin, which genuinely encodes 10
distinct color attribute values) rendered in black and white, while
the rest of the game (a separate rendering path) was in color.
Confirmed against both the real exec/dorkit/graphic.js AND
js_console.cpp source: `Graphic.prototype.draw()`'s own per-cell
color-setting line is `cons.attr = this.data[...].attr;` -- but real
Synchronet's console object has no "attr" property at all (only
"attributes" has any live effect), and the value assigned is a whole
Attribute object rather than its numeric `.value` (every OTHER
assignment in the same file correctly unwraps `.value`). A real,
long-standing bug in Synchronet's own vendored library (confirmed
identical in the live upstream source, not introduced by vendoring)
that silently makes Graphic.draw() never apply per-cell color at all.
Fixed the same way as the BIN-setter bug -- it's a shared library
file, not door-specific source.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_NODE_PATH = os.environ.get('NODEJS_PATH', '/usr/bin/node')
_HAVE_NODE = os.path.isfile(_NODE_PATH)


@unittest.skipUnless(_HAVE_NODE, 'requires a real Node.js binary')
class SynchronetCompatMissingGlobalsTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)

    def _make_game(self, script_body):
        script_path = os.path.join(self._tmpdir.name, 'door.js')
        with open(script_path, 'w') as f:
            f.write(script_body)
        return SimpleNamespace(
            game_type='door_synchronet',
            synchronet_script_path=script_path,
            synchronet_exec_dir='',
            working_directory='',
        )

    def _run(self, script_body, send_input=None, run_seconds=12, return_timing=False):
        from anetbbs.games.door_runner import _build_command
        os.environ.pop('SBBS_JSEXEC', None)
        temp_files = []
        game = self._make_game(script_body)
        cmd, cwd = _build_command(game, node_number=1, bbs_name='TestBBS',
                                  user=None, temp_files_out=temp_files)
        try:
            result = self._run_under_pty(cmd, cwd, send_input, run_seconds,
                                          return_timing=return_timing)
        finally:
            for f in temp_files:
                try:
                    os.unlink(f)
                except OSError:
                    pass
        return result

    def _run_under_pty(self, cmd, cwd, send_input=None, run_seconds=12, return_timing=False):
        """The compat script's own raw-mode setup shells out to
        `stty ... < /dev/tty`, which needs a real controlling terminal
        -- a plain subprocess.run() with pipe-based stdio has none, so
        every test here would fail on that step regardless of the
        actual fix being tested. Uses a real PTY, matching how
        door_runner.py actually launches doors in production.

        `send_input`: optional list of (delay_seconds, bytes) pairs --
        each written to the PTY master once that many seconds have
        elapsed, for tests that need to simulate a real keypress (e.g.
        an arrow-key escape sequence) partway through a run.

        `return_timing`: if True, returns (output, status, first_byte_at)
        instead of (output, status), where first_byte_at is the elapsed
        seconds from process start until the FIRST output byte arrived
        -- needed to prove output reached the terminal BEFORE a delayed
        keypress was sent, not just that it eventually showed up
        somewhere in the final concatenated output (which can't
        distinguish "flushed immediately" from "flushed late, batched
        together with whatever came after the blocking read")."""
        import pty
        import select
        import signal
        import time
        send_input = send_input or []
        sent = []
        old_cwd = os.getcwd()
        pid, fd = pty.fork()
        if pid == 0:
            try:
                os.chdir(cwd)
                os.execvp(cmd[0], cmd)
            finally:
                os._exit(127)
        os.chdir(old_cwd)
        output = b''
        first_byte_at = None
        start = time.time()
        while time.time() - start < run_seconds:
            r, _, _ = select.select([fd], [], [], 0.5)
            if fd in r:
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                if first_byte_at is None:
                    first_byte_at = time.time() - start
                output += chunk
            elapsed = time.time() - start
            for idx, (delay, data) in enumerate(send_input):
                if elapsed > delay and idx not in sent:
                    try:
                        os.write(fd, data)
                    except OSError:
                        pass
                    sent.append(idx)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        _, status = os.waitpid(pid, 0)
        decoded = output.decode('utf-8', errors='replace')
        if return_timing:
            return decoded, status, first_byte_at
        return decoded, status

    def test_stdout_writes_are_flushed_before_a_blocking_key_read(self):
        """Real regression risk introduced by output batching (buffering
        many small console.write() calls -- one gotoxy+char per changed
        cell is typical -- into a single combined write, added to close
        the network-timing gap behind Synchronetris's "bleeding blocks"
        reports): if the buffered write were deferred blindly (e.g. via
        a bare process.nextTick with no explicit flush point), the
        extremely common "print a prompt, then block reading a key"
        pattern would break completely -- readSync() is a real blocking
        syscall that does not yield to the event loop AT ALL, so a
        nextTick-scheduled flush would never get a chance to run before
        the read blocks, and the user would stare at a blank screen
        with no visible prompt while the process waits forever for a
        key they were never shown a reason to press. _flushStdoutNow()
        is called explicitly, synchronously, right before every blocking
        stdin-read call site in the compat shim for exactly this reason.

        Proven here with real timing, not just final output content:
        final output can't distinguish "flushed immediately" from
        "flushed late, batched together with whatever came after the
        read" -- both would eventually contain the prompt text. Sending
        the keypress only after a LONG delay and checking the elapsed
        time until the FIRST output byte arrives is the only way to
        actually prove the prompt reached the terminal before the read
        could have unblocked."""
        script = (
            "process.stdout.write('PROMPT_MARKER_XYZ');\n"
            "console.getkey();\n"
            "process.stdout.write('AFTER_KEY_MARKER');\n"
        )
        output, _status, first_byte_at = self._run(
            script, send_input=[(3.0, b'Q')], run_seconds=5, return_timing=True)
        self.assertIsNotNone(first_byte_at, msg='no output arrived at all: ' + output)
        self.assertLess(first_byte_at, 1.5,
                         msg=f'prompt only appeared after {first_byte_at}s -- '
                             f'should have been flushed almost immediately, '
                             f'well before the 3s delayed keypress. output={output!r}')
        self.assertIn('PROMPT_MARKER_XYZ', output, msg=output)
        self.assertIn('AFTER_KEY_MARKER', output, msg=output)

    def test_queue_poll_flushes_stdout_like_the_other_blocking_read_sites(self):
        """Real bug found live: LORD (and any other door built on
        dorkit.js, the shared vendored library most non-trivial
        Synchronet doors use) "just sits stale, never loads". dorkit's
        own real input loop is `dk.console.waitkey()` -> a busy-poll
        `while (...) { if (queue.poll(timeout)) return true; }` --
        NOT console.getkey()/_readKey, so the flush fix proven by the
        test right above this one doesn't cover it. Queue.prototype.poll
        never called _flushStdoutNow() at all, so a door's own intro
        screen (drawn via buffered console.write/process.stdout.write
        calls) sat in the pending buffer indefinitely while dorkit
        silently spun on poll() waiting for a key -- confirmed live via
        an instrumented run of the real LORD door: the intro art and
        "Press a key" prompt never reached the terminal, while polling
        itself was working correctly in the background the whole time.
        Indistinguishable from a real hang to the player. Same timing-
        based proof as the getkey() test above -- final output alone
        can't tell "flushed immediately" from "flushed only once
        polling eventually gives up"."""
        script = (
            "process.stdout.write('INTRO_SCREEN_MARKER;');\n"
            "var q = new Queue('test_dorkit_style_poll');\n"
            "var deadline = Date.now() + 4000;\n"
            "while (Date.now() < deadline) {\n"
            "    if (q.poll(50)) { break; }\n"
            "}\n"
            "process.stdout.write('AFTER_POLL_LOOP;');\n"
        )
        output, _status, first_byte_at = self._run(
            script, run_seconds=6, return_timing=True)
        self.assertIsNotNone(first_byte_at, msg='no output arrived at all: ' + output)
        self.assertLess(first_byte_at, 1.5,
                         msg=f'intro screen only appeared after {first_byte_at}s -- '
                             f'should have been flushed on the very first poll() call, '
                             f'not deferred until the 4s busy-poll loop gave up. '
                             f'output={output!r}')
        self.assertIn('INTRO_SCREEN_MARKER;', output, msg=output)
        self.assertIn('AFTER_POLL_LOOP;', output, msg=output)

    def test_many_small_writes_still_produce_correct_combined_output(self):
        """Sanity check for the batching mechanism itself: a burst of
        many separate small console.write() calls (matching frame.js's
        own Display.cycle() flush pattern -- one gotoxy+char per cell)
        must still produce byte-for-byte correct combined output once
        buffered writes get coalesced into one real write() call, not
        just "doesn't crash." Uses 50 separate single-character writes,
        each preceded by its own cursor-position escape, mirroring the
        real per-cell write pattern."""
        parts = []
        for i in range(50):
            parts.append(f"process.stdout.write('\\x1b[{i+1};1H');\n")
            parts.append(f"process.stdout.write('{chr(65 + (i % 26))}');\n")
        script = ''.join(parts) + "process.stdout.write('DONE_MARKER');\n"
        output, _status = self._run(script)
        self.assertIn('DONE_MARKER', output, msg=output)
        for i in range(50):
            self.assertIn(chr(65 + (i % 26)), output, msg=output)

    def test_door_crash_message_is_flushed_before_the_blocking_keypress_wait(self):
        """Real bug found live bundling Good Time Trivia, in
        door_runner.py's own generated crash handler (NOT
        synchronet_compat.py) -- a completely different bug from the
        test above, in a different file, but the exact same root
        cause class: `process.stdout.write(msg)` followed immediately
        by a blocking `_fs.readSync(0, ...)` to wait for a keypress
        before exiting. The compat shim's own process.stdout.write is
        patched to buffer and flush on next-tick (see the test above),
        so without an explicit `_flushStdoutNow()` call in between,
        the crash message -- including its own "Press any key to
        return to BBS..." instruction -- never reached the terminal at
        all before the blocking read. A real player would see a
        completely blank, frozen screen with zero indication a key
        needed pressing, and this project's own debugging would see
        nothing but a hung process. Discovered live: a real uncaught
        exception (bbs.compare_ars missing) produced a bare hang with
        zero output until this fix. Uses the SAME real timing-based
        proof as the test above: the message must reach the terminal
        well before a deliberately-delayed keypress, not just
        eventually appear once something else unblocks it."""
        script = "throw new Error('SYNTHETIC_CRASH_MARKER');\n"
        output, _status, first_byte_at = self._run(
            script, send_input=[(3.0, b' ')], run_seconds=5, return_timing=True)
        self.assertIsNotNone(first_byte_at, msg='no output arrived at all: ' + output)
        self.assertLess(first_byte_at, 1.5,
                         msg=f'crash message only appeared after {first_byte_at}s -- '
                             f'should have been flushed almost immediately. output={output!r}')
        self.assertIn('SYNTHETIC_CRASH_MARKER', output, msg=output)
        self.assertIn('Press any key to return to BBS', output, msg=output)

    def test_base64_encode_decode_are_defined_and_visible_via_load(self):
        """Loading a real vendored library file (frame.js) is what
        actually reproduces the visibility bug -- calling
        base64_encode from the door's own top-level code (concatenated
        directly into the same file as the compat script) would pass
        even with the registration missing, since both live in the
        same scope. A door that also load()s something is what the
        real crash needed."""
        script = (
            "load('sbbsdefs.js');\n"
            "var encoded = base64_encode('Guest@ANetBBS');\n"
            "var decoded = base64_decode(encoded);\n"
            "process.stdout.write('ENCODED:' + encoded);\n"
            "process.stdout.write('DECODED:' + decoded);\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('ReferenceError', output, msg=output)
        self.assertIn('ENCODED:R3Vlc3RAQU5ldEJCUw==', output)
        self.assertIn('DECODED:Guest@ANetBBS', output)

    def test_base64_encode_ignores_extra_arguments_matching_real_synchronet(self):
        """Confirmed against the real js_b64_encode C source: it only
        ever reads argv[0] -- a second argument (chickendelivery.js
        passes `true`) is silently ignored by real Synchronet too."""
        script = "process.stdout.write(base64_encode('x', true, 'ignored', 123));\n"
        output, _status = self._run(script)
        self.assertNotIn('ReferenceError', output, msg=output)
        self.assertIn('eA==', output)  # base64('x')

    def test_ctrl_is_defined_and_visible_via_load(self):
        """Real crash was in frame.js's own key-handling code
        (`case ctrl('A'):`) -- load a real vendored file that uses it
        to reproduce the visibility bug, not just call ctrl() from the
        door's own top-level scope."""
        script = (
            "load('frame.js');\n"
            "process.stdout.write('CTRL_A:' + ctrl('A').charCodeAt(0));\n"
            "process.stdout.write('CTRL_C:' + ctrl('c').charCodeAt(0));\n"  # lowercase input
        )
        output, _status = self._run(script)
        self.assertNotIn('ReferenceError', output, msg=output)
        self.assertIn('CTRL_A:1', output)
        self.assertIn('CTRL_C:3', output)

    def test_skipsp_is_defined_and_visible_via_load(self):
        """Real crash found live smoke-testing Jeopardized: selecting
        "Play" reached lib/frame-ext.js's word-wrap/center code, which
        calls the real Synchronet global `skipsp(truncsp(word))`
        (js_global.cpp's js_skipsp -- the mirror image of the already-
        implemented truncsp(), stripping LEADING instead of trailing
        whitespace) -- "ReferenceError: skipsp is not defined", since
        no implementation existed anywhere in the shim. Loads a real
        file (matching the ctrl() test above) to reproduce the
        visibility bug through the real load()/vm.runInThisContext
        path, not just call skipsp() from the door's own top-level
        scope."""
        extra_path = os.path.join(self._tmpdir.name, 'skipsp_lib.js')
        with open(extra_path, 'w') as f:
            f.write("function useSkipsp(s) { return skipsp(s); }\n")
        script = (
            "load(" + json.dumps(extra_path) + ");\n"
            "process.stdout.write('RESULT:[' + useSkipsp('   hi there  ') + ']');\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('ReferenceError', output, msg=output)
        self.assertIn('RESULT:[hi there  ]', output, msg=output)

    def test_js_flatten_string_exists_and_returns_its_input(self):
        """Real crash found live smoke-testing Jeopardized: submitting
        an answer reaches func.js's real answer-checking HTTP call,
        which flows through our own sbbs_stubs/http.js's ReadBody()
        (copied verbatim from the real vendored source) -- it calls
        the real Synchronet global `js.flatten_string(this.body)`
        (js_global.cpp's js_flatten_string, an internal SpiderMonkey
        perf hint with no JS-observable effect) at the end, and no
        such method existed anywhere on the compat shim's `js` object
        at all: "TypeError: js.flatten_string is not a function". This
        one test file's own separate http.js-shim test
        (test_http_client_js_shim.py) stubs a fake js.flatten_string
        directly in its minimal harness -- which is why THAT test
        suite never caught this: it never exercises the real,
        production `js` object this test uses via the real compat
        shim's own load()/_run() pipeline."""
        script = (
            "process.stdout.write('IS_FUNCTION:' + (typeof js.flatten_string === 'function') + ';');\n"
            "process.stdout.write('RESULT:' + js.flatten_string('hello world'));\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('TypeError', output, msg=output)
        self.assertIn('IS_FUNCTION:true', output, msg=output)
        self.assertIn('RESULT:hello world', output, msg=output)

    def test_console_getkeys_is_a_function(self):
        """getkeys() blocks on real keyboard input, so this only
        confirms it exists and is callable -- the interactive behavior
        (matching a key from the given set) was confirmed manually via
        a real PTY-driven playtest of chickendelivery.js's quit-
        confirmation popup, not practical to drive headlessly here."""
        script = "process.stdout.write('IS_FUNCTION:' + (typeof console.getkeys === 'function'));\n"
        output, _status = self._run(script)
        self.assertNotIn('TypeError', output, msg=output)
        self.assertIn('IS_FUNCTION:true', output)

    def test_inkey_translates_a_real_arrow_key_escape_sequence(self):
        """Direct reproduction of the arrow-key-acts-like-Escape bug --
        sends a REAL right-arrow escape sequence (\\x1b[C) into the PTY
        mid-run and confirms console.inkey() returns the translated
        KEY_RIGHT code (\\x06), not a bare \\x1b (27) that a door would
        read as a standalone Escape press."""
        script = (
            "process.stdout.write('WAITING\\n');\n"
            "var k = console.inkey(0, 3000);\n"
            "process.stdout.write('GOTKEY:' + k.charCodeAt(0));\n"
        )
        output, _status = self._run(
            script, send_input=[(1.0, b'\x1b[C')], run_seconds=5)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('GOTKEY:6', output,
                      msg=f'expected KEY_RIGHT (0x06), got: {output!r}')
        self.assertNotIn('GOTKEY:27', output,
                         "arrow key must not be reported as a bare Escape (27)")

    def test_inkey_still_reports_a_real_bare_escape_correctly(self):
        """Sanity check the fix doesn't break genuine standalone Escape
        presses (the door's own quit-confirmation flow depends on
        this) -- a lone \\x1b with no follow-up bytes must still come
        back as plain Escape (27), not get eaten waiting for a
        sequence that never arrives."""
        script = (
            "var k = console.inkey(0, 3000);\n"
            "process.stdout.write('GOTKEY:' + k.charCodeAt(0));\n"
        )
        output, _status = self._run(
            script, send_input=[(1.0, b'\x1b')], run_seconds=5)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('GOTKEY:27', output, msg=f'output: {output!r}')

    def test_system_timer_is_a_real_advancing_clock(self):
        """Direct reproduction of the frozen-gameplay bug: real
        sprite.js movement gating is `system.timer - lastMove >
        speed`, which needs system.timer to be a genuinely advancing
        number on every read, not a missing/static value. Confirms
        both that it reads as a real number (not undefined/NaN, which
        would make every such comparison silently always-false) and
        that two reads separated by a real ~300ms busy-wait actually
        differ by approximately that much."""
        script = (
            "var t1 = system.timer;\n"
            "var start = Date.now();\n"
            "while (Date.now() - start < 300) {}\n"
            "var t2 = system.timer;\n"
            "process.stdout.write('ISNUM:' + (typeof t1 === 'number' && !isNaN(t1)));\n"
            "process.stdout.write(' DIFF:' + (t2 - t1));\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('ISNUM:true', output, msg=output)
        m = re.search(r'DIFF:([\d.]+)', output)
        self.assertIsNotNone(m, f'could not find DIFF in output: {output!r}')
        diff = float(m.group(1))
        self.assertGreater(diff, 0.2, f'expected ~0.3s elapsed, got {diff}')
        self.assertLess(diff, 2.0, f'expected ~0.3s elapsed, got {diff} (way too high)')

    def test_setting_console_attributes_emits_real_ansi_and_reads_back(self):
        """Direct reproduction of the monochrome-rendering bug: assign
        to console.attributes (the real Synchronet idiom frame.js's
        Display.__drawChar__ uses for every character) and confirm a
        real SGR escape sequence actually reaches stdout, not just that
        the stored value changed. Also confirms the read side still
        works (chickendelivery.js's own init()/cleanUp() save/restore
        `var attr = console.attributes; ...; console.clear(attr);`)."""
        script = (
            "console.attributes = 0x0F;\n"  # bright white on black
            "process.stdout.write('READBACK:' + console.attributes + ';');\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        # Bright white foreground (37), default background (40), bold (1).
        self.assertIn('\x1b[1;37;40m', output, msg=repr(output))
        self.assertIn('READBACK:15;', output)  # 0x0F == 15, value reads back correctly

    def test_attr_method_still_works_and_does_not_double_write(self):
        """attr(n) now delegates to the attributes setter -- confirms
        it still emits exactly one escape sequence, not two (the old
        implementation wrote the ANSI code directly AND set
        .attributes, which would double-write once the setter also got
        its own side effect)."""
        script = "console.attr(0x04);\n"  # plain red
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        count = output.count('\x1b[0;31;40m')
        self.assertEqual(count, 1, f'expected exactly one SGR write, got {count} in {output!r}')

    def test_file_eof_is_a_live_property_not_a_truthy_function(self):
        """Direct reproduction of the would-be infinite-loop bug: a
        two-line file, read to the end, must report eof===true -- and
        rewind() must bring it back to false at position 0. Before the
        fix, `.eof` was a bare function reference, always truthy."""
        script = (
            "var fs = _node_require('fs');\n"
            "var p = '/tmp/anetbbs_eof_test_' + process.pid + '.txt';\n"
            "fs.writeFileSync(p, 'one\\ntwo\\n');\n"
            "var f = new File(p);\n"
            "f.open('r');\n"
            "process.stdout.write('EOF0:' + f.eof + ';');\n"
            "f.readln(); f.readln();\n"
            "process.stdout.write('EOF1:' + f.eof + ';');\n"
            "f.rewind();\n"
            "process.stdout.write('EOF2:' + f.eof + ' POS:' + f.position + ';');\n"
            "fs.unlinkSync(p);\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('EOF0:false;', output)
        self.assertIn('EOF1:true;', output)
        self.assertIn('EOF2:false POS:0;', output)

    def test_console_getnum_parses_typed_digits_and_falls_back_to_default(self):
        """Bubble Boggle's changeDate() calls console.getnum(maxnum,
        dflt) expecting real Synchronet's numeric-entry behavior: typed
        digits parsed as a number, or `dflt` if Enter is pressed with
        nothing typed."""
        script = (
            "var n = console.getnum(31, 5);\n"
            "process.stdout.write('GOT:' + n);\n"
        )
        output, _status = self._run(
            script, send_input=[(0.5, b'12\r')], run_seconds=4)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('GOT:12', output, msg=output)

    def test_console_getstr_single_numeric_arg_still_works(self):
        """Regression guard for the original Bot Wars fix documented
        right above getstr()'s own definition: `console.getstr(2)`
        (a single numeric maxlen, no prompt/mode) must keep working
        exactly as before -- this is the case the comment there says
        broke when an even older version treated arg1 as a prompt
        string to print."""
        script = (
            "var s = console.getstr(2);\n"
            "process.stdout.write('GOT:[' + s + ']');\n"
        )
        output, _status = self._run(
            script, send_input=[(0.5, b'hi\r')], run_seconds=4)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('GOT:[hi]', output, msg=output)

    def test_console_getstr_three_arg_form_prefixes_with_the_initial_string(self):
        """Real Synchronet 3-arg overload found live auditing Star Trek
        before bundling it: `console.getstr("USS ", 30, K_LINE|K_EDIT)`
        is meant to pre-fill "USS " so the player only types the rest
        of a ship name. Before this fix, a string first argument fell
        through the `typeof maxlen !== 'number'` check and silently
        lost the prefix entirely (falling back to maxlen=80, with the
        door's real intended maxlen of 30 discarded along with it)."""
        script = (
            "load('sbbsdefs.js');\n"
            "var s = console.getstr('USS ', 30, K_LINE|K_EDIT);\n"
            "process.stdout.write('GOT:[' + s + ']');\n"
        )
        output, _status = self._run(
            script, send_input=[(0.5, b'Enterprise\r')], run_seconds=4)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('GOT:[USS Enterprise]', output, msg=output)

    def test_getstr_k_edit_allows_backspacing_into_the_prefilled_string(self):
        """Real bug reported live on Jerry's Pi3 playing Thirstyville:
        typing "160" (meaning $1.60) into the price prompt
        (`console.getstr("0.00", 8, K_EDIT|K_LINE)`) produced
        "0.00160", not "1.60" or "160". Root cause: the prefilled
        string was written once and a FRESH read started right after
        it with no way to backspace into it at all -- pressing
        backspace when the freshly-typed buffer was empty was always a
        silent no-op, so "0.00" could never be removed no matter how
        many times backspace was pressed. K_EDIT's real, documented
        meaning (sbbsdefs.js: "Edit string passed") is that the whole
        string is a live, backspace-into-able buffer -- exactly what a
        player needs to actually overwrite a pre-filled default value.
        Confirmed live end-to-end: backspacing 4 times to clear "0.00"
        then typing "1.60" now produces exactly "1.60"."""
        script = (
            "load('sbbsdefs.js');\n"
            "var s = console.getstr('0.00', 8, K_EDIT|K_LINE);\n"
            "process.stdout.write('RESULT:[' + s + ']');\n"
        )
        output, _status = self._run(
            script, send_input=[(1.0, b'\x7f\x7f\x7f\x7f'), (2.0, b'1.60\r')],
            run_seconds=6)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('RESULT:[1.60]', output, msg=output)

    def test_getstr_k_edit_still_allows_appending_after_the_prefill_without_backspacing(self):
        """Sanity check the fix doesn't remove the ability to just type
        MORE after a prefill without backspacing first -- a player who
        wants to keep "0.00" and add digits after it (unusual for a
        price, but a valid input sequence) must still get plain
        concatenation, matching a real editable-buffer text field
        where the cursor starts positioned at the end of the existing
        text."""
        script = (
            "load('sbbsdefs.js');\n"
            "var s = console.getstr('0.00', 8, K_EDIT|K_LINE);\n"
            "process.stdout.write('RESULT:[' + s + ']');\n"
        )
        output, _status = self._run(
            script, send_input=[(1.0, b'9\r')], run_seconds=4)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('RESULT:[0.009]', output, msg=output)

    def test_msg_area_sub_is_an_empty_object_not_missing(self):
        """Real crash found live bundling Good Time Trivia:
        `msg_area.sub.hasOwnProperty(code)` (a normal, defensive "does
        this sub-board exist" check -- not exotic) threw "Cannot read
        properties of undefined (reading 'hasOwnProperty')" because
        `msg_area` only ever had a `.grp` property, no `.sub` at all.
        This shim deliberately doesn't wire up real message-base data
        (documented gap), but an empty object is the honest
        representation of that, not a missing property that crashes
        the first thing that touches it."""
        script = (
            "process.stdout.write('HAS_SUB:' + (typeof msg_area.sub) + ';');\n"
            "process.stdout.write('HASOWN:' + msg_area.sub.hasOwnProperty('TEST') + ';');\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('HAS_SUB:object;', output, msg=output)
        self.assertIn('HASOWN:false;', output, msg=output)

    def test_user_is_sysop_reflects_the_real_security_level(self):
        """Real gap found live bundling Good Time Trivia:
        `doSysopMenu()`'s own `if (!user.is_sysop) return;` silently
        locked the real sysop out of the admin menu no matter who was
        logged in, since `user.is_sysop` didn't exist at all. Confirmed
        against real js_user.cpp semantics: `security.level >= 90`
        (the stock SYSOP ARS threshold)."""
        script = "process.stdout.write('IS_SYSOP:' + user.is_sysop);\n"
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        # The test harness launches with no real logged-in user, which
        # resolves to the non-admin default (security level 50) —
        # confirms the property exists and reads as a real boolean,
        # not that this particular harness run is an admin.
        self.assertIn('IS_SYSOP:false', output, msg=output)

    def test_bbs_compare_ars_sysop_checks_user_is_sysop(self):
        """Real crash found live bundling Good Time Trivia:
        `bbs.compare_ars` didn't exist at all -- getQACategoriesAndFilenames()
        calls it unconditionally for any trivia category with an ARS
        string set, so selecting "Play" crashed immediately with
        "TypeError: bbs.compare_ars is not a function", before a
        player could even choose a category."""
        script = "process.stdout.write('SYSOP_ARS:' + bbs.compare_ars('SYSOP'));\n"
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('SYSOP_ARS:false', output, msg=output)  # non-admin test harness user

    def test_bbs_compare_ars_level_checks_real_security_level(self):
        script = (
            "process.stdout.write('LOW:' + bbs.compare_ars('LEVEL 10') + ';');\n"
            "process.stdout.write('HIGH:' + bbs.compare_ars('LEVEL 200') + ';');\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('LOW:true;', output, msg=output)   # security level 50 >= 10
        self.assertIn('HIGH:false;', output, msg=output)  # security level 50 < 200

    def test_bbs_compare_ars_age_fails_closed_with_no_birthdate(self):
        """Real, meaningful case found live bundling Good Time Trivia:
        qa/dirty_minds.qa carries a real "AGE 18" ARS restriction on
        actual adult content -- unlike this shim's usual permissive-
        by-default stance for properties with no real backing data,
        AGE deliberately fails CLOSED (denies) when the real user's
        birthdate is unknown/unset, since this gates real content
        appropriateness, not a cosmetic feature. Confirmed live: a
        test player with no birthdate correctly never saw "Dirty
        Minds" in the category list at all."""
        script = "process.stdout.write('AGE_ARS:' + bbs.compare_ars('AGE 18'));\n"
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('AGE_ARS:false', output, msg=output)

    def test_bbs_compare_ars_age_passes_with_a_real_adult_birthdate(self):
        """Confirms the AGE check is genuinely computed from the real
        user's birthdate, not just hardcoded to always fail -- see the
        fail-closed test above for the no-birthdate case."""
        script = (
            "user.birthdate = '1990-01-01';\n"
            "process.stdout.write('ADULT:' + bbs.compare_ars('AGE 18') + ';');\n"
            "user.birthdate = '2015-01-01';\n"
            "process.stdout.write('MINOR:' + bbs.compare_ars('AGE 18') + ';');\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('ADULT:true;', output, msg=output)
        self.assertIn('MINOR:false;', output, msg=output)

    def test_file_cfgname_falls_back_to_plain_path_when_no_hostname_override(self):
        """boggle.js's real usage: `new File(file_cfgname(root,
        "server.ini"))`. With no per-hostname override file present
        (the common case), must resolve to the plain path unchanged."""
        script = (
            "var p = file_cfgname('/tmp/anetbbs_cfgname_test/', 'server.ini');\n"
            "process.stdout.write('PATH:' + p);\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('PATH:/tmp/anetbbs_cfgname_test/server.ini', output)

    def test_load_scope_form_populates_a_pre_created_scope_object(self):
        """Direct reproduction of graphic.js's real usage pattern
        (`Graphic.prototype.defs = {}; load(Graphic.prototype.defs,
        "cga_defs.js");` -- no assignment of load()'s return value at
        all). Before the fix, the pre-created object was never
        populated, so Graphic.draw()'s `this.defs.RED` etc. would all
        read undefined."""
        script = (
            "var defs = {};\n"
            "load(defs, 'cga_defs.js');\n"
            "process.stdout.write('GREEN:' + defs.GREEN + ' BG_RED:' + defs.BG_RED + ';');\n"
            "var s2 = load({}, 'cga_defs.js');\n"  # existing var-reassign convention still works
            "process.stdout.write('S2_RED:' + s2.RED + ';');\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('GREEN:2 BG_RED:64;', output, msg=output)
        self.assertIn('S2_RED:4;', output, msg=output)

    def _write_synthetic_door_file(self, *path_parts, content):
        """Writes a synthetic (entirely original, not derived from any
        real door) test file at a path matching the directory
        structure _applyKnownDoorFixes() matches on, e.g.
        ('sbbs_doors', 'synchronetris', 'game.js')."""
        door_dir = os.path.join(self._tmpdir.name, *path_parts[:-1])
        os.makedirs(door_dir, exist_ok=True)
        file_path = os.path.join(door_dir, path_parts[-1])
        with open(file_path, 'w') as f:
            f.write(content)
        return file_path

    def test_known_door_fix_patches_drawboard_to_flush_the_frame(self):
        """Confirms the load()-time door-patch mechanism through
        observable behavior (matching this file's established pattern
        for the for-each polyfill above), not by calling the internal
        helper functions directly (they're deliberately not exposed as
        globals -- load() calls them internally, same-scope, and never
        needs them registered). Uses an entirely synthetic stand-in
        file at a path matching Synchronetris's real game.js location
        -- NOT any of that door's own code -- with an empty drawBoard()
        body: if the real fix (found live: completed lines never
        visually cleared, since drawBoard() updates the frame's data
        buffer via setData() but never flushes it to the screen) is
        correctly injected, calling this empty stand-in function still
        triggers the flush. Uses cycle(), not draw(): draw() was tried
        first (shipped as v1.0.21) and made things visibly worse live,
        since it calls refresh() first -- an unconditional full-frame
        repaint that stomps on the separately (unbuffered-)rendered
        falling piece. cycle() alone only repaints cells actually
        touched by setData(), confirmed against the vendored frame.js."""
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'synchronetris', 'game.js',
            content=(
                "var flushCount = 0;\n"
                "var player = { stack: { cycle: function() { flushCount++; } } };\n"
                "function drawBoard(player) { }\n"
            ))
        script = (
            "load(" + json.dumps(path) + ");\n"
            "drawBoard(player);\n"
            "process.stdout.write('FLUSH_COUNT:' + flushCount);\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('FLUSH_COUNT:1', output, msg=output)

    def test_known_door_fix_patches_setpiece_to_call_drawboard(self):
        """Same mechanism, covering the second real bug found live: a
        locked piece never appeared on screen until something else
        happened to force a redraw, since setPiece() updates the board
        array but never calls drawBoard() at all."""
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'synchronetris', 'game.js',
            content=(
                "var drawBoardCalls = 0;\n"
                # Both known fixes always apply together against a real
                # Synchronetris path, so drawBoard()'s own injected
                # player.stack.cycle() call fires too when setPiece()
                # calls drawBoard(localPlayer) -- localPlayer needs a
                # real stack.cycle stub for that not to throw. setPiece()
                # is now also patched to call unDrawCurrent() at the
                # start (separate test below covers that specifically),
                # so a stub is needed here too for this test not to throw.
                "var localPlayer = { stack: { cycle: function() {} } };\n"
                "function drawBoard(player) { drawBoardCalls++; }\n"
                "function unDrawCurrent(player) { }\n"
                "function send(cmd) { }\n"
                "function setPiece() { }\n"
            ))
        script = (
            "load(" + json.dumps(path) + ");\n"
            "setPiece();\n"
            "process.stdout.write('DRAWBOARD_CALLS:' + drawBoardCalls);\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('DRAWBOARD_CALLS:1', output, msg=output)

    def test_known_door_fix_patches_setpiece_to_undraw_the_falling_piece_first(self):
        """Third real bug in setPiece(), found live after a hard drop:
        a ghost of the just-dropped piece rendered overlapping/next to
        where it actually landed. setPiece() writes the locked piece
        into player.grid and (via the fix above) calls drawBoard() to
        repaint the whole board -- but drawBoard() repaints using the
        SAME character/color the falling piece was already rendered
        with at that exact cell, so Frame's own dedup (matching
        against its already-stored value, not what's actually reached
        the real screen yet) treats it as no change and doesn't
        re-mark it dirty. That's harmless if the falling piece's own
        raw render had already been flushed -- but a hard drop
        (fullDrop()) calls move() rapidly with zero cycle() calls in
        between, so nothing guarantees that by the moment it locks in.
        Erasing the falling piece's raw render explicitly, via the
        same mechanism it was drawn with, closes this regardless of
        flush timing. Must run BEFORE the grid write / drawBoard()
        call (which is why this needs _insertAfterFunctionStart, not
        the end-of-function mechanism the other two setPiece fixes
        use) -- confirmed here by checking call ORDER, not just count."""
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'synchronetris', 'game.js',
            content=(
                "var callOrder = [];\n"
                "var localPlayer = { stack: { cycle: function() {} } };\n"
                "function drawBoard(player) { callOrder.push('drawBoard'); }\n"
                "function unDrawCurrent(player) { callOrder.push('unDrawCurrent'); }\n"
                # setPiece() is now also patched to send("GRID") at the
                # end (separate test below covers that specifically) --
                # needs a send() stub for this test not to throw.
                "function send(cmd) { }\n"
                "function setPiece() { }\n"
            ))
        script = (
            "load(" + json.dumps(path) + ");\n"
            "setPiece();\n"
            "process.stdout.write('ORDER:' + JSON.stringify(callOrder));\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('ORDER:["unDrawCurrent","drawBoard"]', output, msg=output)

    def test_known_door_fix_patches_setpiece_to_also_send_grid(self):
        """Real gap found by a top-to-bottom audit of the door's own
        source: setPiece() only ever sends a "SET" notification when a
        piece locks, and packageData()'s own "SET" case is completely
        empty -- no grid data at all. The actual board state only ever
        gets sent via a separate "GRID" message, which getLines() only
        sends when a line actually clears -- meaning in multiplayer,
        other players never see a locked stack update at all unless
        that exact piece happened to clear a line. Always sending GRID
        alongside SET closes the gap."""
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'synchronetris', 'game.js',
            content=(
                "var sentCommands = [];\n"
                "var localPlayer = { stack: { cycle: function() {} } };\n"
                "function drawBoard(player) { }\n"
                "function unDrawCurrent(player) { }\n"
                "function send(cmd) { sentCommands.push(cmd); }\n"
                "function setPiece() { }\n"
            ))
        script = (
            "load(" + json.dumps(path) + ");\n"
            "setPiece();\n"
            "process.stdout.write('SENT:' + JSON.stringify(sentCommands));\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('SENT:["GRID"]', output, msg=output)

    def test_known_door_fix_patches_loadgarbage_to_call_drawboard(self):
        """Third real bug in the same family: loadGarbage() (adding
        garbage rows sent by another player clearing lines) shifts
        every row of player.grid but never calls drawBoard() on the
        normal path -- confirmed live: a lone block rendered
        disconnected from the rest of the stack after a garbage
        shift, since the stack's own visible pixels were never
        resynced to the shifted grid data at all."""
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'synchronetris', 'game.js',
            content=(
                "var drawBoardCalls = 0;\n"
                "var localPlayer = { stack: { cycle: function() {} }, grid: [] };\n"
                "function drawBoard(player) { drawBoardCalls++; }\n"
                "function loadGarbage(lines, space) { }\n"
            ))
        script = (
            "load(" + json.dumps(path) + ");\n"
            "loadGarbage(1, 0);\n"
            "process.stdout.write('DRAWBOARD_CALLS:' + drawBoardCalls);\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('DRAWBOARD_CALLS:1', output, msg=output)

    def test_known_door_fix_does_not_apply_to_a_different_doors_game_js(self):
        """Confirms the patch is scoped to Synchronetris specifically
        -- an otherwise-identical synthetic file at a different door's
        path must NOT be patched."""
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'bublbogl', 'game.js',
            content=(
                "var flushCount = 0;\n"
                "var player = { stack: { cycle: function() { flushCount++; } } };\n"
                "function drawBoard(player) { }\n"
            ))
        script = (
            "load(" + json.dumps(path) + ");\n"
            "drawBoard(player);\n"
            "process.stdout.write('FLUSH_COUNT:' + flushCount);\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('FLUSH_COUNT:0', output, msg=output)

    def test_real_bundled_synchronetris_game_js_loads_cleanly_with_the_fix_applied(self):
        """End-to-end sanity check against the REAL bundled game.js
        (not a synthetic stand-in): confirms the door-specific patch
        doesn't break loading the actual file -- it must still parse
        and execute its own top-level code without error through the
        real load() pipeline (E4X polyfill + door fix chained
        together, exactly as production does it)."""
        real_game_js = str(
            (Path(__file__).resolve().parents[1] / 'anetbbs' / 'games' /
             'sbbs_doors' / 'synchronetris' / 'game.js'))
        if not os.path.isfile(real_game_js):
            self.skipTest('real bundled synchronetris/game.js not present in this checkout')
        # game.js defines `function playGame(profile,game) {...}` as its
        # only top-level statement -- loading it should just define
        # that function, not execute any game logic.
        script = (
            "load(" + json.dumps(real_game_js) + ");\n"
            "process.stdout.write('HAS_PLAYGAME:' + (typeof playGame === 'function'));\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('HAS_PLAYGAME:true', output, msg=output)

    def test_known_door_fix_repairs_a_game_missing_gamenumber_after_processupdate(self):
        """Real Pi3 crash: a lobby tile rendered "Game undef
        [finished]" and joining logged "Error finding game number".
        Root cause: processUpdate()'s WRITE handler auto-vivifies a
        missing intermediate object in a dotted update path
        (e.g. "games.5.players.Bob") as a bare {} -- which never gets
        a .gameNumber. This only happens for a game this client never
        received the FULL creation write for (loadGames()'s one-time
        snapshot read happens before subscribe() registers, so a game
        created by another player in that window is invisible until a
        later nested-path fragment arrives). getOpenGame() then
        returns that bare entry's gameNumber (undefined), and
        isNaN(undefined) is true. Confirms the injected repair walks
        data.games and fixes any entry missing gameNumber, keyed by
        its own store key (this door's own convention throughout)."""
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'synchronetris', 'lobby.js',
            content=(
                "var data = { games: { '5': { status: 0 } } };\n"
                "function processUpdate(update) { }\n"
            ))
        script = (
            "load(" + json.dumps(path) + ");\n"
            "processUpdate({});\n"
            "process.stdout.write('GAMENUMBER:' + data.games['5'].gameNumber);\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('GAMENUMBER:5', output, msg=output)

    def test_known_door_fix_does_not_touch_a_game_that_already_has_gamenumber(self):
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'synchronetris', 'lobby.js',
            content=(
                "var data = { games: { '5': { gameNumber: 5, status: 0 } } };\n"
                "function processUpdate(update) { }\n"
            ))
        script = (
            "load(" + json.dumps(path) + ");\n"
            "processUpdate({});\n"
            "process.stdout.write('GAMENUMBER:' + data.games['5'].gameNumber);\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('GAMENUMBER:5', output, msg=output)

    def test_known_door_fix_repairs_a_missing_gamenumber_from_the_initial_bulk_load_too(self):
        """The processUpdate() self-heal above only ever runs in
        response to a LATER incoming subscribe() push -- it never
        gets a chance to fire for a dead/abandoned game that no
        further update ever touches again. Confirmed live: the
        corrupted entry was cleared server-side, but the "Game undef"
        tile came back from ordinary play and persisted through an
        entire session with no further updates to trigger the other
        fix. The real gap: GameData's own this.loadGames() (the
        door's one-time initial bulk fetch, in tetrisobj.js) assigns
        straight to this.games with no repair at all. this.loadGames
        is an inline `this.loadGames=function(){...}` property
        assignment, not a named function declaration -- not directly
        patchable -- but it's always called synchronously from
        GameData's own constructor before that constructor returns,
        so the repair is appended there instead."""
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'synchronetris', 'tetrisobj.js',
            content=(
                "function GameData() {\n"
                "\tthis.games = { '5': { status: 3 } };\n"
                "}\n"
            ))
        script = (
            "load(" + json.dumps(path) + ");\n"
            "var data = new GameData();\n"
            "process.stdout.write('GAMENUMBER:' + data.games['5'].gameNumber);\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('GAMENUMBER:5', output, msg=output)

    def test_known_door_fix_also_backfills_players_not_just_gamenumber(self):
        """Confirmed live: repairing only gameNumber wasn't enough --
        it fixed the display ("Game undef" -> "Game 1"), which made a
        dead FINISHED game look joinable again (getOpenGame() doesn't
        exclude FINISHED), and joinGame()'s own
        `data.games[gnum].players[profile.name] = player` then
        crashed on the still-missing .players. Both self-heal sites
        must backfill .players too, matching what a real Game object
        always has (tetrisobj.js's own Game() constructor sets
        gameNumber and players together)."""
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'synchronetris', 'lobby.js',
            content=(
                "var data = { games: { '5': { status: 3 } } };\n"
                "function processUpdate(update) { }\n"
            ))
        script = (
            "load(" + json.dumps(path) + ");\n"
            "processUpdate({});\n"
            "process.stdout.write('PLAYERS:' + JSON.stringify(data.games['5'].players));\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('PLAYERS:{}', output, msg=output)

    def test_known_door_fix_wraps_client_read_to_repair_a_corrupted_games_record(self):
        """Real Pi3 crash: joinGame() re-fetches a game straight from
        the server via its own client.read(game_id,"games."+gnum)
        call, bypassing the local (already-repaired) data.games cache
        entirely -- a still-corrupted server-side record comes back
        corrupted every time no matter what the other two self-heal
        fixes already did locally. That crash is mid-function inside
        joinGame() itself, so it can't be healed by appending code
        elsewhere (appended code only runs once a function returns
        normally) -- instead client.read is wrapped once at lobby
        startup (open() always runs before joinGame() can be reached)
        so any FUTURE "games.N" read is repaired the instant it comes
        back."""
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'synchronetris', 'lobby.js',
            content=(
                "var client = { read: function (scope, location) { return { status: 3 }; } };\n"
                "function open() { }\n"
            ))
        script = (
            "load(" + json.dumps(path) + ");\n"
            "open();\n"
            "var result = client.read('S', 'games.5');\n"
            "process.stdout.write('GAMENUMBER:' + result.gameNumber + ' PLAYERS:' + JSON.stringify(result.players));\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('GAMENUMBER:5', output, msg=output)
        self.assertIn('PLAYERS:{}', output, msg=output)

    def test_known_door_fix_wrapped_client_read_ignores_non_games_scoped_reads(self):
        """Confirms the wrap is scoped precisely to "games.N" locations
        -- a read for something else (e.g. a profile record) must come
        back completely untouched, not accidentally get a gameNumber/
        players grafted onto it."""
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'synchronetris', 'lobby.js',
            content=(
                "var client = { read: function (scope, location) { return { name: 'Bob' }; } };\n"
                "function open() { }\n"
            ))
        script = (
            "load(" + json.dumps(path) + ");\n"
            "open();\n"
            "var result = client.read('S', 'profiles.Bob');\n"
            "process.stdout.write('KEYS:' + JSON.stringify(Object.keys(result)));\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('KEYS:["name"]', output, msg=output)

    def test_known_door_fix_guards_updatestatus_against_a_still_corrupted_game(self):
        """Real Pi3 crash, the worst-placed instance of the same
        corruption class: updateStatus() is called SYNCHRONOUSLY from
        inside processUpdate()'s own WRITE case -- possibly the same
        call that just auto-vivified data.games[gameNumber] as a bare
        {} a few lines earlier in that same switch statement, before
        processUpdate()'s own appended end-of-function repair (tested
        above) ever gets a chance to run. An append-at-end patch can't
        reach a crash that happens partway through the SAME function
        call that triggered it -- this needs a guard at the very
        START of updateStatus() itself, via the new
        _insertAfterFunctionStart mechanism, ahead of updateStatus()'s
        own existing "game doesn't exist at all" check."""
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'synchronetris', 'lobby.js',
            content=(
                "var data = { games: { '5': { status: 3 } } };\n"
                "var profile = { name: 'StingRay' };\n"
                "var status = { SYNCING: 1 };\n"
                "function updateStatus(statusUpdate, gameNumber) {\n"
                "\tif (!data.games[gameNumber]) return false;\n"
                "\tif (data.games[gameNumber].players[profile.name]) { }\n"
                "\treturn true;\n"
                "}\n"
            ))
        script = (
            "load(" + json.dumps(path) + ");\n"
            "var result = updateStatus(1, '5');\n"
            "process.stdout.write('RESULT:' + result);\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('RESULT:true', output, msg=output)

    def test_known_door_fix_repairs_a_brand_new_players_null_getuser(self):
        """Real crash found live smoke-testing Jeopardized (the 4th
        Synchronet JSON-RPC door): selecting "Play" as a brand new
        player (anyone with no existing record) threw "Cannot read
        properties of null (reading 'round')". Root cause, confirmed
        against the real live server (a direct read for a missing key
        returns JSON null, not an omitted field) and the real vendored
        json-client.js's own wait() (`return packet.data;`, no
        null-to-undefined conversion): database.js's getUser() checks
        `typeof result === 'undefined'` to decide whether to create a
        fresh record, but a missing key comes back as null (typeof
        'object'), so that check never catches it and the door crashes
        on the raw null. getUser()/getUserGameState() are `this.NAME =
        function(){}` property assignments inside Database's own
        constructor -- not directly patchable, and every path already
        returns -- so this wraps both PUBLIC methods from the door's
        real initDatabase() (called exactly once, right after
        `database = new Database(...)` completes), re-running the same
        create-if-missing write+retry-read sequence the door's own
        (unreachable from outside) addUser()/addUserGameState() do."""
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'jeopardized', 'jeopardized.js',
            content=(
                "var settings = { JSONDB: {\n"
                "\thost: 'testhost', port: 1234, dbName: 'JEOPARDIZED',\n"
                "\tretries: 3, retryDelay: 1\n"
                "} };\n"
                "var system = { name: 'TestBBS' };\n"
                "var writeCalls = [];\n"
                "function mswait(ms) { }\n"
                "function JSONClient(host, port) {\n"
                "\tthis.host = host; this.port = port;\n"
                "\tthis.write = function (dbName, op, payload, timeout) {\n"
                "\t\twriteCalls.push({ dbName: dbName, op: op, payload: payload });\n"
                "\t\treturn true;\n"
                "\t};\n"
                "\tthis.read = function (dbName, path, lock) {\n"
                "\t\tif (path === 'users.42') return { id: 42, alias: 'Bob', system: 'TestBBS' };\n"
                "\t\tif (path === 'game.users.42') return { id: 42, round: 1 };\n"
                "\t\treturn null;\n"
                "\t};\n"
                "}\n"
                "var database = {\n"
                "\tgetUserID: function (usr) { return 42; },\n"
                "\tgetUser: function (usr) { return null; },\n"
                "\tgetUserGameState: function (usr) { return null; }\n"
                "};\n"
                "function initDatabase() { }\n"
            ))
        script = (
            "load(" + json.dumps(path) + ");\n"
            "initDatabase();\n"
            "var usr = { alias: 'Bob' };\n"
            "var u = database.getUser(usr);\n"
            "var gs = database.getUserGameState(usr);\n"
            "process.stdout.write('USER:' + JSON.stringify(u) +\n"
            "\t' STATE:' + JSON.stringify(gs) +\n"
            "\t' WRITES:' + writeCalls.length +\n"
            "\t' WRITE0KEY:' + writeCalls[0].payload.key +\n"
            "\t' WRITE1KEY:' + writeCalls[1].payload.key);\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn(
            'USER:{"id":42,"alias":"Bob","system":"TestBBS"}', output, msg=output)
        self.assertIn('STATE:{"id":42,"round":1}', output, msg=output)
        self.assertIn('WRITES:2', output, msg=output)
        self.assertIn('WRITE0KEY:users', output, msg=output)
        self.assertIn('WRITE1KEY:game.users', output, msg=output)

    def test_known_door_fix_leaves_an_existing_players_getuser_untouched(self):
        """Confirms the wrap is a no-op for the common case (a player
        who already has a record): the original method's real
        non-null result must pass straight through, with zero writes
        or extra JSONClient round trips."""
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'jeopardized', 'jeopardized.js',
            content=(
                "var settings = { JSONDB: {\n"
                "\thost: 'testhost', port: 1234, dbName: 'JEOPARDIZED',\n"
                "\tretries: 3, retryDelay: 1\n"
                "} };\n"
                "var system = { name: 'TestBBS' };\n"
                "var writeCalls = [];\n"
                "function mswait(ms) { }\n"
                "function JSONClient(host, port) {\n"
                "\tthis.write = function (dbName, op, payload, timeout) {\n"
                "\t\twriteCalls.push(payload);\n"
                "\t};\n"
                "\tthis.read = function (dbName, path, lock) { return null; };\n"
                "}\n"
                "var database = {\n"
                "\tgetUserID: function (usr) { return 7; },\n"
                "\tgetUser: function (usr) { return { id: 7, alias: usr.alias }; },\n"
                "\tgetUserGameState: function (usr) { return { id: 7, round: 3 }; }\n"
                "};\n"
                "function initDatabase() { }\n"
            ))
        script = (
            "load(" + json.dumps(path) + ");\n"
            "initDatabase();\n"
            "var usr = { alias: 'Alice' };\n"
            "var u = database.getUser(usr);\n"
            "var gs = database.getUserGameState(usr);\n"
            "process.stdout.write('USER:' + JSON.stringify(u) +\n"
            "\t' STATE:' + JSON.stringify(gs) +\n"
            "\t' WRITES:' + writeCalls.length);\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('USER:{"id":7,"alias":"Alice"}', output, msg=output)
        self.assertIn('STATE:{"id":7,"round":3}', output, msg=output)
        self.assertIn('WRITES:0', output, msg=output)

    def test_known_door_fix_does_not_apply_to_a_different_doors_jeopardized_named_file(self):
        """Confirms the patch is scoped to the real jeopardized.js path
        specifically -- an otherwise-identical synthetic file at a
        different location must not be patched, matching the existing
        scoping tests for the Synchronetris fixes above."""
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'otherdoor', 'jeopardized.js',
            content=(
                "var database = { getUser: function (usr) { return null; } };\n"
                "function initDatabase() { }\n"
            ))
        script = (
            "load(" + json.dumps(path) + ");\n"
            "initDatabase();\n"
            "process.stdout.write('RESULT:' + database.getUser({}));\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('RESULT:null', output, msg=output)

    def test_known_door_fix_rewrites_synkrobans_hardcoded_install_path(self):
        """Real portability bug found reading Synkroban's own source
        before bundling: level-set loading and the level-set picker
        both hardcode the author's own literal install path
        ("/sbbs/xtrn/synkroban/") instead of using js.exec_dir --
        guaranteed to silently fail to find any level file on any
        install not living at that exact absolute path, which
        includes every real ANetBBS install. Confirms the fix (a
        plain string substitution, not a function-boundary insertion,
        since the bug is a wrong literal VALUE embedded mid-string,
        not a missing statement) correctly splices in a real js.exec_dir
        expression for both the standalone-literal case and the
        concatenated-with-a-suffix case."""
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'synkroban', 'synkroban.js',
            content=(
                "var skb_config = { PATH_SYNKROBAN: \"/sbbs/xtrn/synkroban/\" };\n"
                "var filename = \"/sbbs/xtrn/synkroban/levels/\" + \"Foo\" + \".txt\";\n"
                "var pattern = \"/sbbs/xtrn/synkroban/levels/*.txt\";\n"
            ))
        script = (
            "js.exec_dir = '/opt/anetbbs/games/sbbs_doors/synkroban/';\n"
            "load(" + json.dumps(path) + ");\n"
            "process.stdout.write('PATH:' + skb_config.PATH_SYNKROBAN + ';');\n"
            "process.stdout.write('FILENAME:' + filename + ';');\n"
            "process.stdout.write('PATTERN:' + pattern + ';');\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn(
            'PATH:/opt/anetbbs/games/sbbs_doors/synkroban/;', output, msg=output)
        self.assertIn(
            'FILENAME:/opt/anetbbs/games/sbbs_doors/synkroban/levels/Foo.txt;',
            output, msg=output)
        self.assertIn(
            'PATTERN:/opt/anetbbs/games/sbbs_doors/synkroban/levels/*.txt;',
            output, msg=output)

    def test_known_door_fix_does_not_touch_a_different_doors_hardcoded_path(self):
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'otherdoor', 'synkroban.js',
            content="var p = \"/sbbs/xtrn/synkroban/\";\n"
        )
        script = (
            "load(" + json.dumps(path) + ");\n"
            "process.stdout.write('P:' + p);\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('P:/sbbs/xtrn/synkroban/', output, msg=output)

    def test_frame_top_actually_controls_compositing_order(self):
        """Real bug found live bundling FatFish: a gray box appeared
        over the top-left quadrant of the lake, exactly matching the
        door's own hidden shopFrame (created and cleared with a gray
        fill at startup, never .open()'d until the shop key is
        pressed) -- its gray fill was showing through the lake terrain
        it should have been completely covered by.

        Root cause: Display.prototype.__getTopCanvas__ (this real,
        unmodified vendored frame.js's own compositing logic) picks
        the topmost canvas via `Object.keys(this.__properties__
        .canvas)`, relying on .top()/.bottom() reordering that key's
        position in the object. frame.id (confirmed in the Frame
        constructor: `this.__properties__.id = parent.display.nextID`)
        is a bare incrementing integer -- and once used as an object
        key, JS engines are REQUIRED by spec to enumerate integer-
        index-like keys in ascending numeric order, unconditionally,
        regardless of insertion or reinsertion order. Node's V8
        follows this strictly, so .top()/.bottom() had ZERO effect on
        which sibling frame actually rendered on top -- the
        numerically-higher-id frame (whichever was CREATED later)
        always won, no matter how many times an earlier frame was
        .top()'d. Confirmed via this exact minimal reproduction (two
        overlapping sibling frames, the smaller/later one filled
        first, then the larger/earlier one .top()'d) before the fix
        existed: it returned the wrong frame. This isn't door-
        specific -- fixed by patching frame.js itself (matched by its
        own fullpath) with an explicit insertion-ordered z-order
        array, not a per-door workaround."""
        script = (
            "load('frame.js');\n"
            "var frame = new Frame();\n"
            "var a = new Frame(1, 1, 10, 10, undefined, frame);\n"  # created first -> lower id
            "var b = new Frame(1, 1, 5, 5, undefined, frame);\n"    # created second -> higher id
            "frame.open();\n"
            "a.setData(0, 0, 'A', WHITE);\n"
            "b.setData(0, 0, 'B', WHITE);\n"
            "a.top();\n"
            "var d = frame.__properties__.display;\n"
            "var top = d.__getTopCanvas__(0, 0);\n"
            "process.stdout.write('TOP_IS_A:' + (top.frame === a));\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('TOP_IS_A:true', output, msg=output)

    def test_frame_bottom_still_works_after_the_zorder_fix(self):
        """Confirms .bottom() (the opposite operation) also still
        works correctly through the fix, not just .top()."""
        script = (
            "load('frame.js');\n"
            "var frame = new Frame();\n"
            "var a = new Frame(1, 1, 10, 10, undefined, frame);\n"
            "var b = new Frame(1, 1, 5, 5, undefined, frame);\n"
            "frame.open();\n"
            "a.setData(0, 0, 'A', WHITE);\n"
            "b.setData(0, 0, 'B', WHITE);\n"
            "b.bottom();\n"  # b is already topmost by creation order; explicitly sink it
            "var d = frame.__properties__.display;\n"
            "var top = d.__getTopCanvas__(0, 0);\n"
            "process.stdout.write('TOP_IS_A:' + (top.frame === a));\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('TOP_IS_A:true', output, msg=output)

    def test_known_door_fix_repairs_star_treks_first_ever_score_submission(self):
        """Same real bug class as Jeopardized's getUser()/
        getUserGameState() (see those tests above): scoreBoard()
        checks `scores === undefined` to detect "no scores submitted
        yet for this scope", but the real server returns JSON `null`
        for a missing key, not `undefined` -- on a brand-new "STARTREK"
        scope (guaranteed the first time anyone ever plays against a
        given server), `scores` comes back `null`, the check misses
        it, and `scores.length` crashes immediately after. Confirms
        the door-patch treats both `undefined` (still handled, for
        safety) and the real `null` case as "first run"."""
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'startrek', 'startrek.js',
            content=(
                "var firstRun = false;\n"
                "var scores = null;\n"
                "if (scores === undefined) {\n"
                "\tfirstRun = true;\n"
                "\tscores = ['seeded'];\n"
                "}\n"
                "process.stdout.write('FIRSTRUN:' + firstRun +\n"
                "\t' LEN:' + scores.length);\n"
            ))
        script = "load(" + json.dumps(path) + ");\n"
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('FIRSTRUN:true LEN:1', output, msg=output)

    def test_known_door_fix_does_not_touch_a_different_doors_undefined_check(self):
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'otherdoor', 'startrek.js',
            content=(
                "var scores = null;\n"
                "var isUndefined = (scores === undefined);\n"
                "process.stdout.write('U:' + isUndefined);\n"
            ))
        script = "load(" + json.dumps(path) + ");\n"
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('U:false', output, msg=output)

    def test_known_door_fix_repairs_dicewarz2s_getTile_against_a_serialized_null_hole(self):
        """Real crash found live bundling Dice Warz ][: starting a
        single-player game crashed entering the map screen with
        "Cannot read properties of undefined (reading 'owner')" in
        drawSector(). Root cause: map.grid is a 2D array with genuine
        JS array holes (undefined) for empty cells, but this door
        round-trips the whole map through a real JSON-RPC write+read
        (not an in-memory reuse) -- JSON.stringify() turns those holes
        into literal `null`, and getTile()'s own `grid[x][y]>=0` check
        treats that as a VALID tile index because `null >= 0` is
        `true` in JavaScript, unlike the original `undefined` (which
        correctly failed the same check). Confirms the fix rejects a
        `null` cell exactly like it already rejects a negative one,
        while still correctly returning a genuine tile index."""
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'dicewarz2', 'dicefunc.js',
            content=(
                "function Coords(x,y) { this.x=x; this.y=y; }\n"
                "function getTile(grid,coords) {\n"
                "\tif(coords && grid[coords.x][coords.y]>=0) \n"
                "\t\treturn grid[coords.x][coords.y];\n"
                "\telse \n"
                "\t\treturn -1;\n"
                "}\n"
            ))
        script = (
            "load(" + json.dumps(path) + ");\n"
            "var grid = JSON.parse(JSON.stringify([[null, 5]]));\n"  # index 0 is a real hole -> null after the round-trip
            "process.stdout.write('HOLE:' + getTile(grid, new Coords(0, 0)) + ';');\n"
            "process.stdout.write('REAL:' + getTile(grid, new Coords(0, 1)));\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('HOLE:-1;', output, msg=output)
        self.assertIn('REAL:5', output, msg=output)

    def test_known_door_fix_does_not_touch_a_different_doors_gettile(self):
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'otherdoor', 'dicefunc.js',
            content=(
                "function Coords(x,y) { this.x=x; this.y=y; }\n"
                "function getTile(grid,coords) {\n"
                "\tif(coords && grid[coords.x][coords.y]>=0) \n"
                "\t\treturn grid[coords.x][coords.y];\n"
                "\telse \n"
                "\t\treturn -1;\n"
                "}\n"
                "var grid = JSON.parse(JSON.stringify([[null, 5]]));\n"
                "process.stdout.write('HOLE:' + getTile(grid, new Coords(0, 0)));\n"
            ))
        script = "load(" + json.dumps(path) + ");\n"
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        # Unpatched (different door): null coerces to 0 in the >= 0
        # check, so the buggy version returns the null cell itself,
        # not -1 -- confirms the patch really is scoped to dicewarz2.
        self.assertIn('HOLE:null', output, msg=output)

    def test_real_bundled_jeopardized_js_patches_and_parses_cleanly(self):
        """Sanity check against the REAL bundled jeopardized.js (not a
        synthetic stand-in) that stops short of actually running it:
        unlike Synchronetris's game.js (which only ever DEFINES
        functions), jeopardized.js's own top-level code unconditionally
        calls init()+main() and connects to a real, live JSON-RPC
        server for real interactive play (see the PTY-based smoke
        tests used manually during development) -- genuinely running
        it here would hit that production server on every test run,
        which is exactly what this test must NOT do. Instead this
        applies the exact same two source-transform functions load()
        itself calls (_polyfillE4XForEach, then _applyKnownDoorFixes)
        directly to the real file's text and writes the RESULT to a
        temp file, without ever executing it -- then confirms that
        result is still syntactically valid via `node --check` (the
        same static-only technique test_sbbs_stubs_node_syntax.py
        already uses for the vendored library files) and that the
        patch actually fired (not silently skipped by a path-matching
        bug) by checking for its own injected marker text."""
        real_jeopardized_js = str(
            (Path(__file__).resolve().parents[1] / 'anetbbs' / 'games' /
             'sbbs_doors' / 'jeopardized' / 'jeopardized.js'))
        if not os.path.isfile(real_jeopardized_js):
            self.skipTest('real bundled jeopardized/jeopardized.js not present in this checkout')
        from anetbbs.games.synchronet_compat import write_compat_script
        game = SimpleNamespace(
            synchronet_exec_dir=os.path.dirname(real_jeopardized_js),
            synchronet_script_path=real_jeopardized_js,
        )
        compat_path = write_compat_script(game, user=None, node_number=1)
        self.addCleanup(lambda: os.unlink(compat_path) if os.path.isfile(compat_path) else None)
        patched_out = os.path.join(self._tmpdir.name, 'jeopardized_patched.js')
        # `_fs`/`_polyfillE4XForEach`/`_applyKnownDoorFixes` are plain
        # top-level `var`/`function` in the generated compat script,
        # scoped to Node's CommonJS module wrapper when that script
        # runs as the entry file -- NOT globalThis properties, so code
        # loaded afterwards via load()'s own vm.runInThisContext
        # (which runs with globalThis as scope, deliberately, so a
        # DOOR's own top-level declarations become visible to each
        # other -- see this file's module docstring) can't see them.
        # Cutting the compat script off right before its own trailing
        # "load the real door and run it" section and appending the
        # dump driver directly keeps everything in that same one
        # module scope, with no vm/self-load involved at all, so
        # jeopardized.js's own init()/main() never runs.
        header = open(compat_path).read().split(
            '// === Execute the actual game ===')[0]
        driver = (
            "var code = _fs.readFileSync(" + json.dumps(real_jeopardized_js) + ", 'utf8');\n"
            "code = _polyfillE4XForEach(code);\n"
            "code = _applyKnownDoorFixes(" + json.dumps(real_jeopardized_js) + ", code);\n"
            "_fs.writeFileSync(" + json.dumps(patched_out) + ", code);\n"
        )
        driver_path = os.path.join(self._tmpdir.name, 'driver.js')
        with open(driver_path, 'w') as f:
            f.write(header + driver)
        result = subprocess.run(
            ['/usr/bin/node', driver_path],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertTrue(os.path.isfile(patched_out))
        patched_source = open(patched_out).read()
        self.assertIn('__origGetUser', patched_source, msg='door fix did not fire')
        check = subprocess.run(
            ['/usr/bin/node', '--check', patched_out],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(check.returncode, 0, msg=check.stderr)

    def test_for_each_e4x_syntax_in_a_loaded_door_file_is_polyfilled(self):
        """Real reproduction of Bubble Boggle's game.js bug: a real
        file on disk (not the vendored library files already covered
        by test_sbbs_stubs_node_syntax.py) with a
        `for each (var p in obj)` occurrence, reached via load() --
        exactly how game.js is reached from boggle.js's own
        `load(root + "game.js")`. Before the fix, this is a parse-time
        SyntaxError for the entire file. Confirms both that it parses
        AND that the polyfilled value-iteration semantics are correct
        (iterates VALUES, matching real E4X `for each`, not KEYS)."""
        extra_path = os.path.join(self._tmpdir.name, 'winner_lib.js')
        with open(extra_path, 'w') as f:
            f.write(
                "var players = {a: {points: 3}, b: {points: 9}, c: {points: 1}};\n"
                "var winner;\n"
                "for each(var p in players) {\n"
                "    if (winner === undefined || p.points > winner.points) winner = p;\n"
                "}\n"
                "process.stdout.write('WINNER:' + winner.points);\n"
            )
        script = "load(" + json.dumps(extra_path) + ");\n"
        output, _status = self._run(script)
        self.assertNotIn('SyntaxError', output, msg=output)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('WINNER:9', output, msg=output)

    def test_for_each_over_undefined_or_null_silently_does_nothing(self):
        """Real Pi3 crash from Synchronetris: `for each(var p in
        game.players)` in lobby.js's listGames() threw
        `TypeError: Cannot convert undefined or null to object` at
        `Object.keys(game.players)` whenever game.players was
        undefined. Standard JS `for...in` (and, by the same
        enumeration protocol, Mozilla's E4X `for each...in`) silently
        does zero iterations over null/undefined rather than throwing
        -- confirmed independently by Synchronetris's own
        updateGame() explicitly guarding `if(!game.players)` before
        it dares to iterate elsewhere, implying the door authors
        never expected iteration itself to be able to throw. The
        polyfill's unconditional Object.keys(expr) diverged from that
        real semantics; it must no-op instead of throwing."""
        extra_path = os.path.join(self._tmpdir.name, 'undefined_foreach_lib.js')
        with open(extra_path, 'w') as f:
            f.write(
                "var iterations = 0;\n"
                "var obj;\n"
                "for each(var p in obj) { iterations++; }\n"
                "var nullObj = null;\n"
                "for each(var q in nullObj) { iterations++; }\n"
                "process.stdout.write('ITERATIONS:' + iterations);\n"
            )
        script = "load(" + json.dumps(extra_path) + ");\n"
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('ITERATIONS:0', output, msg=output)

    def test_json_chat_join_survives_a_null_history_response(self):
        """Real crash found live bundling Dice Warz ][: entering the
        in-game chat channel for the first time crashed with "Cannot
        convert undefined or null to object" -- json-chat.js's own
        join() (an ANetBBS-adapted file, not a byte-identical vendor
        mirror -- its real E4X `for each(var x in history)` was
        already permanently converted to an explicit Object.keys()
        loop, matching the same fix already applied to frame.js/
        tree.js/cvslib.js/layout.js/json-db.js) calls
        `Object.keys(history)` on the result of `client.slice(...)`
        for a channel's message history -- the real server returns
        JSON null for a channel with no history yet (a brand new
        channel, the common case the very first time anyone joins),
        and `Object.keys(null)` throws immediately, unlike the real
        E4X `for each` construct it replaced (which silently does
        zero iterations over null/undefined). Drives join() directly
        against a fake client object (no real network) whose .slice()
        returns null, matching the real server's own confirmed
        behavior for a missing/empty history key."""
        script = (
            "load('json-chat.js');\n"
            "var fakeClient = {\n"
            "\tconnect: function () { return true; },\n"
            "\tsubscribe: function () {},\n"
            "\tslice: function () { return null; },\n"
            "\twrite: function () {},\n"
            "\twho: function () {}\n"
            "};\n"
            "var chat = new JSONChat(0, fakeClient, undefined, undefined);\n"
            "chat.join('testchan');\n"
            "process.stdout.write('JOINED:' + (typeof chat.channels['TESTCHAN']));\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('JOINED:object', output, msg=output)

    def test_graphic_draw_defaults_cons_to_the_global_console(self):
        """Direct reproduction of the real Pi3 crash: Bubble Boggle's
        game.js calls `splash.draw();` with zero arguments (a real bug
        in the door's own source -- see module docstring bug #12).
        Confirms draw() no longer throws when `cons` is omitted, and
        that it actually used the real global console (real gotoxy/
        attr output reaches stdout) rather than silently no-op'ing."""
        script = (
            "load('graphic.js');\n"
            "var g = new Graphic(4, 2, 7, 'X');\n"
            "var ok = true;\n"
            "try { g.draw(); } catch (e) { ok = false; process.stdout.write('THREW:' + e.message); }\n"
            "process.stdout.write('OK:' + ok);\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('THREW', output, msg=output)
        self.assertIn('OK:true', output, msg=output)
        # Real console.gotoxy output (used internally by draw()) -- confirms
        # cons defaulted to a real, working console object, not just avoided
        # throwing by chance.
        self.assertIn('\x1b[', output, msg=f'expected real ANSI output: {output!r}')

    def test_getkey_and_inkey_honor_k_upper_mode(self):
        """Direct reproduction of a real softlock found live-testing
        Synchronetris: its real "Game Over" screen does
        `while (console.inkey(K_NOCRLF|K_NOSPIN|K_NOECHO|K_UPPER) != "Q");`
        -- confirmed against js_console.cpp that `mode` (including
        K_UPPER, "force the returned key uppercase") is a real,
        meaningful parameter threaded through to the real key-reading
        implementation, not just accepted-and-ignored. This shim
        discarded `mode` entirely for both getkey() and inkey(), so a
        real lowercase "q" keypress could never satisfy the != "Q"
        comparison -- the loop never exited no matter how many times
        the key was pressed."""
        script = (
            "load('sbbsdefs.js');\n"
            "var a = console.inkey(K_UPPER, 3000);\n"
            "var b = console.getkey(K_UPPER, 3000);\n"
            "process.stdout.write('INKEY:' + a + ' GETKEY:' + b);\n"
        )
        output, _status = self._run(
            script, send_input=[(0.3, b'q'), (0.6, b'q')], run_seconds=5)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('INKEY:Q', output, msg=output)
        self.assertIn('GETKEY:Q', output, msg=output)

    def test_getkey_and_inkey_do_not_uppercase_without_the_mode_bit(self):
        """Sanity check the fix doesn't force uppercase unconditionally
        -- real Synchronet's inkey() defaults to K_NONE (confirmed
        against js_console.cpp's own default), not K_UPPER."""
        script = (
            "var a = console.inkey(0, 3000);\n"
            "process.stdout.write('INKEY:' + a);\n"
        )
        output, _status = self._run(script, send_input=[(0.3, b'q')], run_seconds=4)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('INKEY:q', output, msg=output)

    def test_strlen_strips_ctrl_a_codes_not_just_ansi_escapes(self):
        """Direct reproduction of a real crash found live-testing
        Synchronetris: `Error: invalid Frame x coordinate: -2` when the
        door's own "Game Over" screen tries to build a centered message
        frame. Root cause: getMsgFrame() computes
        `Math.floor((frame.width - console.strlen(line))/2)` for a line
        containing several \\x01-prefixed Ctrl-A color codes (Synchronet's
        native inline color mechanism, e.g. "\\x01y" for bright yellow) --
        console.strlen only stripped raw ANSI escapes, so the invisible
        code bytes were counted as real characters, inflating the
        computed width past the actual frame width and producing a
        genuinely negative x. Confirms \\x01 codes are now excluded the
        same way _translateCtrlA already excludes them for real output."""
        script = (
            "var withCodes = '\\x01n\\x01yPress [\\x01hQ\\x01n\\x01y] to exit';\n"
            "var plain = 'Press [Q] to exit';\n"
            "process.stdout.write('WITH:' + console.strlen(withCodes));\n"
            "process.stdout.write(' PLAIN:' + console.strlen(plain));\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('PLAIN:17', output, msg=output)
        self.assertIn('WITH:17', output, msg=output)

    def test_graphic_draw_applies_real_per_cell_color(self):
        """Direct reproduction of a real Pi3 report: the Bubble Boggle
        welcome/splash screen rendered in black and white while the
        rest of the game (a completely separate rendering path) was
        in color. Root cause confirmed against the real upstream
        exec/dorkit/graphic.js AND js_console.cpp source: draw()'s own
        per-cell color-setting line is `cons.attr = ...` -- but real
        Synchronet's console object has no "attr" property at all
        (only "attributes"), and the value assigned is a whole
        Attribute object rather than its .value (unlike every other
        place in the same file). A real, long-standing bug in
        Synchronet's own vendored library, not something introduced by
        vendoring -- fixed in this project's copy since it's a shared
        library file, not door-specific source. Confirms drawing two
        cells with genuinely different attribute values produces two
        genuinely different SGR sequences, not the same (or no) one."""
        script = (
            "load('graphic.js');\n"
            "var g = new Graphic(2, 1, 7, ' ');\n"
            "g.setCell('A', 0x04, 0, 0);\n"  # plain red
            "g.setCell('B', 0x02, 1, 0);\n"  # plain green
            "g.draw();\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('\x1b[0;31;40m', output, msg=f'expected red SGR: {output!r}')
        self.assertIn('\x1b[0;32;40m', output, msg=f'expected green SGR: {output!r}')

    def test_user_constructor_returns_current_user_and_has_ip_address(self):
        """Real reproduction of a crash found auditing Synchronetris
        (a real-time multiplayer door needing the JSON-RPC persistent-
        connection work): json-chat.js's JSONChat.connect() does
        `usr = new User(usernum); ... new Nick(usr.alias, system.name,
        usr.ip_address);` -- `User` didn't exist as a global at all
        (ReferenceError), and `user.ip_address` wasn't a real property
        either (confirmed against js_user.cpp's USER_PROP_IPADDR that
        it should be). This shim is single-session (no live cross-user
        DB access), so `new User(n)` always resolves to the current
        session's own `user` object regardless of `n` -- correct for
        every real call site found (always the current user's own
        number)."""
        script = (
            "var u = new User(user.number);\n"
            "process.stdout.write('SAME:' + (u === user));\n"
            "process.stdout.write(' ALIAS:' + u.alias);\n"
            "process.stdout.write(' IP:' + u.ip_address);\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('SAME:true', output, msg=output)
        self.assertIn('IP:', output, msg=output)
        self.assertNotIn('IP:undefined', output, msg=output)

    def test_gotoxy_accepts_the_real_object_calling_convention(self):
        """Real reproduction of a garbled cursor-move escape sequence
        found live-testing Synchronetris: inputline.js's own gotoxy()
        helper calls `console.gotoxy(position)` with a single {x,y}
        object (not two separate numbers) -- a real, documented
        Synchronet calling convention (confirmed against js_console.cpp's
        js_gotoxy, which explicitly validates an object argument's x/y
        properties). This shim only supported the two-number form,
        producing "\\x1b[undefined;[object Object]H" instead of a real
        cursor move."""
        script = "console.gotoxy({x: 5, y: 10});\n"
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('\x1b[10;5H', output, msg=repr(output))
        self.assertNotIn('undefined', output, msg=repr(output))
        self.assertNotIn('[object Object]', output, msg=repr(output))

    def test_gotoxy_still_accepts_the_original_two_number_form(self):
        """Sanity check the object-form fix didn't break the far more
        common two-argument call every other door already relies on."""
        script = "console.gotoxy(5, 10);\n"
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('\x1b[10;5H', output, msg=repr(output))

    def test_cga_defs_color_constants_are_ambiently_available(self):
        """Bubble Boggle's own game.js references GREEN, LIGHTGREEN,
        HIGH, BG_RED etc. as bare top-level globals without ever
        load()ing cga_defs.js itself (only graphic.js's internal,
        differently-scoped 2-arg load() does) -- the compat shim
        proactively load()s cga_defs.js into the real global scope so
        these resolve for any door, matching real Synchronet where
        these constants are always ambiently defined."""
        script = "process.stdout.write('GREEN:' + GREEN + ' HIGH:' + HIGH + ' BG_RED:' + BG_RED);\n"
        output, _status = self._run(script)
        self.assertNotIn('ReferenceError', output, msg=output)
        self.assertIn('GREEN:2 HIGH:8 BG_RED:64', output, msg=output)

    def test_md5_calc_is_defined_and_visible_via_load(self):
        """Real gap found auditing Thirstyville: player.js's very
        first line (`playerID = md5_calc(user.alias.toUpperCase() +
        system.name.toUpperCase(), true);`) runs at load time, before
        main() ever starts -- `md5_calc` was completely absent from
        the compat shim (no prior bundled door ever actually
        exercised it client-side). Loads a real file to reproduce the
        visibility bug through load()/vm.runInThisContext, matching
        this file's established pattern for base64_encode/ctrl/etc.
        Values cross-checked against Python's own hashlib: hex mode is
        the standard lowercase MD5 hex digest; the no-arg/false mode
        is that same digest base64-encoded (Synchronet's own
        base64_encode() convention)."""
        extra_path = os.path.join(self._tmpdir.name, 'md5_lib.js')
        with open(extra_path, 'w') as f:
            f.write("function useMd5(s, hex) { return md5_calc(s, hex); }\n")
        script = (
            "load(" + json.dumps(extra_path) + ");\n"
            "process.stdout.write('HEX:' + useMd5('hello', true) + ';');\n"
            "process.stdout.write('B64:' + useMd5('hello', false) + ';');\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('ReferenceError', output, msg=output)
        self.assertIn('HEX:5d41402abc4b2a76b9719d911017c592;', output, msg=output)
        self.assertIn('B64:XUFAKrxLKna5cZ2REBfFkg==;', output, msg=output)

    def test_known_door_fix_widens_thirsty_gamesettings_null_check(self):
        """Real crash class found auditing Thirstyville (not yet a
        live crash -- caught by direct verification against Jerry's
        real json-rpc server BEFORE this door was ever bundled):
        confirmed live that a READ of a not-yet-written key returns
        real JSON `null`, never `undefined`
        (`echo '{"op":"read",...,"location":"DICEWARZ2.NONEXISTENT",...}'`
        => `{"ok": true, "data": null}`). dataInit()'s very first-ever
        game start does `gameSettings = jsonClient.read(...); if
        (gameSettings === undefined) { ...build... }` -- on a brand
        new THIRSTY module this returns null, the guard never fires,
        and every later `gameSettings.week`/`.updated`/etc access
        would crash. Same fix pattern as the startrek/dicewarz2 fixes
        elsewhere in this file: widen the check, don't touch the
        server or the vendored source."""
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'thirsty', 'thirsty.js',
            content=(
                "var gameSettings = null;\n"
                "var built = false;\n"
                "if(gameSettings === undefined) {\n"
                "\tbuilt = true;\n"
                "}\n"
                "process.stdout.write('BUILT:' + built);\n"
            ))
        script = "load(" + json.dumps(path) + ");\n"
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('BUILT:true', output, msg=output)

    def test_known_door_fix_widens_thirsty_playerkeys_null_check(self):
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'thirsty', 'thirsty.js',
            content=(
                "var playerKeys = null;\n"
                "var result = ((playerKeys === undefined) ? 1 : playerKeys.length);\n"
                "process.stdout.write('RESULT:' + result);\n"
            ))
        script = "load(" + json.dumps(path) + ");\n"
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('RESULT:1', output, msg=output)

    def test_known_door_fix_widens_thirsty_getscores_keys_null_check(self):
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'thirsty', 'thirsty.js',
            content=(
                "var keys = null;\n"
                "var threw = false;\n"
                "try {\n"
                "if(keys === undefined)\n\t\t\tthrow \"THIRSTY.PLAYERS has no properties.\";\n"
                "} catch(err) { threw = true; }\n"
                "process.stdout.write('THREW:' + threw);\n"
            ))
        script = "load(" + json.dumps(path) + ");\n"
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('THREW:true', output, msg=output)

    def test_known_door_fix_repairs_thirsty_getplayer_null_crash(self):
        """Worse instance of the same bug class than the two above:
        getPlayer() has no `||update`-style short-circuit to save it
        (unlike demographics.js/products.js/stock-items.js/weather.js,
        which all pass through `X===undefined || update`, and update
        is always true exactly when X would be null). `player ===
        undefined` being false for a null player falls through to
        evaluate `player.money` on null -- a guaranteed crash for
        every brand-new player's first join. Confirmed live: this
        exact scenario ran end-to-end with the fix applied against
        Jerry's real json-rpc server with no error."""
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'thirsty', 'player.js',
            content=(
                "var player = null;\n"
                "var rebuilt = false;\n"
                "if(player === undefined || player.money <= 0) {\n"
                "\trebuilt = true;\n"
                "}\n"
                "process.stdout.write('REBUILT:' + rebuilt);\n"
            ))
        script = "load(" + json.dumps(path) + ");\n"
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('REBUILT:true', output, msg=output)

    def test_known_door_fix_repairs_thirsty_stockitems_unguarded_keys_crash(self):
        """Worst instance in this door: makeStockItems() runs
        unconditionally as part of the very first game creation,
        BEFORE any player record has ever been written -- `jsonClient.
        keys("THIRSTY","THIRSTY.PLAYERS",1).length` has no guard AT
        ALL, not even a wrong `===undefined` check, so this crashed
        with "Cannot read properties of null (reading 'length')" for
        literally the first player to ever start a fresh Thirstyville
        install, unconditionally, every time."""
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'thirsty', 'stock-items.js',
            content=(
                "function fakeKeys() { return null; }\n"
                "var players = (fakeKeys() || []).length;\n"
                "process.stdout.write('PLAYERS:' + players);\n"
            ))
        script = "load(" + json.dumps(path) + ");\n"
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('PLAYERS:0', output, msg=output)

    def test_known_door_fix_does_not_apply_thirsty_patches_to_a_different_door(self):
        """Confirms the thirsty.js patch is scoped specifically to
        Thirstyville's own bundled path -- an otherwise-identical
        synthetic file at a different door's path must NOT be
        patched, matching this file's established scoping-check
        pattern for the synchronetris fixes above."""
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'bublbogl', 'thirsty.js',
            content=(
                "var gameSettings = null;\n"
                "var built = false;\n"
                "if(gameSettings === undefined) {\n"
                "\tbuilt = true;\n"
                "}\n"
                "process.stdout.write('BUILT:' + built);\n"
            ))
        script = "load(" + json.dumps(path) + ");\n"
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('BUILT:false', output, msg=output)

    def test_known_door_fix_repairs_thirsty_weather_pop_string_crash(self):
        """Real crash found live smoke-testing Thirstyville:
        "TypeError: POP.toFixed is not a function", whenever
        makeWeather()'s random roll for a day landed below that
        weather condition's minimumPOP -- common, e.g. weather.ini's
        "Cloudy" is minimumPOP=20/maximumPOP=70, roughly a 29% chance
        per day, run 7 times on every world/reset. Root cause:
        `weatherConditions[condition].minimumPOP` comes straight out
        of File.iniGetAllObjects(), which returns raw strings for
        every value (confirmed against real Synchronet's own
        iniGetObject/iniGetAllObjects behavior absent an explicit
        template argument, which this door doesn't pass) -- the
        comparison `POP < ...minimumPOP` coerces fine, but the
        assignment on the next line does not, so `POP.toFixed()` a few
        lines later throws on a string. A genuine bug in the door's
        own source that would crash real Synchronet identically."""
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'thirsty', 'weather.js',
            content=(
                "var weatherConditions = [{ minimumPOP: '20' }];\n"
                "var condition = 0;\n"
                "var POP = 5;\n"
                "if(POP < weatherConditions[condition].minimumPOP)\n"
                "\tPOP = weatherConditions[condition].minimumPOP;\n"
                "process.stdout.write('FIXED:' + POP.toFixed());\n"
            ))
        script = "load(" + json.dumps(path) + ");\n"
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('FIXED:20', output, msg=output)

    def test_clearline_emits_the_whole_line_clear_sequence(self):
        """Real gap found auditing Star Stocks: `console.clearline()`
        (distinct from the already-supported `cleartoeol()` -- clears
        the ENTIRE current line rather than just cursor-to-end) was
        completely missing. Not an edge case: called in
        processSelection(), the core "build an outpost on a star"
        gameplay flow. Confirms the real CSI-2K "clear whole line"
        sequence (distinct from cleartoeol's bare CSI-K), and that the
        bare global alias works too, matching the established
        cleartoeol()/clearScreen() convention."""
        script = (
            "console.clearline();\n"
            "process.stdout.write('MARK1;');\n"
            "clearline();\n"
            "process.stdout.write('MARK2;');\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertEqual(output.count('\x1b[2K'), 2, msg=repr(output))
        self.assertIn('MARK1;', output, msg=output)
        self.assertIn('MARK2;', output, msg=output)

    def test_load_scope_form_passes_trailing_args_as_the_scripts_own_argv(self):
        """Real gap found auditing Synchronet Minesweeper: real
        Synchronet's `load(scope, filename, arg1, arg2, ...)` form
        (confirmed against the real vendored modopts.js's own doc
        comment: `var options = load({}, "modopts.js",
        "your_module_name");`) passes trailing args through as the
        loaded script's own `argv` -- this shim silently dropped them
        on the floor, so the loaded script always saw the outer door's
        own (always empty) argv instead."""
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'minesweeper', 'echo_argv.js',
            content="process.stdout.write('ARGV:' + JSON.stringify(argv));\n")
        script = (
            "load({}, " + json.dumps(path) + ", 'minesweeper', 42);\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('ARGV:["minesweeper",42]', output, msg=output)

    def test_load_scope_form_restores_the_callers_own_argv_after_returning(self):
        """Confirms the argv swap in the fix above doesn't leak past
        the load() call it belongs to -- the outer script's own argv
        (real Synchronet's actual command-line args to the running
        door) must read back correctly once load() returns."""
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'minesweeper', 'noop.js',
            content="var x = 1;\n")
        script = (
            "argv = ['outer'];\n"
            "load({}, " + json.dumps(path) + ", 'inner');\n"
            "process.stdout.write('OUTER_ARGV:' + JSON.stringify(argv));\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn("OUTER_ARGV:[\"outer\"]", output, msg=output)

    def test_load_scope_form_returns_the_scripts_real_completion_value(self):
        """Direct reproduction of the real bug: modopts.js's own last
        line is a bare `get_mod_options(argv[0], argv[1], argv[2]);`
        call expression, no top-level var for the existing scope-
        population regex to find -- the caller
        (`var options = load({}, "modopts.js", ...)`) needs load()'s
        own RETURN VALUE to be that call's real result, not just the
        (in this case empty) scope object."""
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'minesweeper', 'modopts_like.js',
            content="function compute() { return {ok: true, n: argv[0]}; }\ncompute();\n")
        script = (
            "var result = load({}, " + json.dumps(path) + ", 7);\n"
            "process.stdout.write('RESULT:' + JSON.stringify(result));\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('RESULT:{"ok":true,"n":7}', output, msg=output)

    def test_modopts_js_get_mod_options_does_not_crash_on_console_charset(self):
        """Real crash found live running Minesweeper on Jerry's server:
        the real vendored modopts.js (js.global.console branch) calls
        `console.charset.toLowerCase()` while resolving a modopts.ini
        section -- console.charset didn't exist at all in this shim's
        console object, so ANY door calling get_mod_options() (the
        documented `var options = load({}, "modopts.js", modname);`
        convention every modopts.ini-reading door uses) crashed with
        "Cannot read property 'toLowerCase' of undefined" before ever
        reaching its own game logic. Exercises the REAL vendored
        modopts.js, not a synthetic stand-in, since the bug is in how
        this shim's console object interacts with that real file."""
        script = (
            "var options = load({}, 'modopts.js', 'minesweeper');\n"
            "process.stdout.write('OPTIONS_TYPE:' + typeof options + ';');\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertNotIn('toLowerCase', output, msg=output)
        self.assertIn('OPTIONS_TYPE:', output, msg=output)

    def test_console_charset_is_a_real_string(self):
        """console.charset must be readable directly too, not just as a
        side effect of modopts.js -- real Synchronet returns a string
        like "CP437"/"UTF-8"; ANetBBS is CP437 throughout."""
        script = "process.stdout.write('CHARSET:' + console.charset + ';');\n"
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('CHARSET:CP437;', output, msg=output)

    def test_cursor_right_rounds_a_fractional_argument(self):
        """Real bug found live: Minesweeper's title bar renders via
        console_center() (minesweeper.js:737), which computes
        `console.right((screen_columns - strlen(text)) / 2)` -- a
        plain division with no rounding of its own, so any odd-parity
        title text (real example: "Synchronet Minesweeper 3.10", 27
        chars, against screen_columns=80, gives (80-27)/2 = 26.5)
        produces a fractional n. console.right() string-concatenated
        that raw float straight into the ANSI CSI parameter --
        "\\x1b[26.5C" is not a legal CSI sequence ('.' is an
        intermediate byte most parsers, including xterm.js, treat as
        ending parameter collection), so the terminal aborted the
        sequence and printed the tail literally -- confirmed live on
        Jerry's server: the title bar rendered a garbled "5C" (the
        literal tail of "26.5C") before the actual title text. Also
        covers console.cursor_right/left/up/down and the right/left/
        up/down aliases, all of which had the exact same bug."""
        script = (
            "console.right(26.5);\n"
            "process.stdout.write('MARK;');\n"
            "console.cursor_left(3.5);\n"
            "process.stdout.write('MARK2;');\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        # A real, legal CSI sequence -- integer parameter, no stray
        # intermediate byte -- must appear, not a fractional one.
        self.assertIn('\x1b[27C', output, msg=repr(output))
        self.assertIn('\x1b[4D', output, msg=repr(output))
        self.assertNotIn('.5', output, msg=repr(output))

    def test_load_scope_form_still_returns_scope_for_the_graphic_js_convention(self):
        """Regression guard for the already-established, already-
        working Bubble Boggle pattern this fix must NOT break:
        `Graphic.prototype.defs = {}; load(Graphic.prototype.defs,
        "cga_defs.js");` -- some loaded files deliberately end with a
        bare `this;` (a harmless globalThis reference) specifically so
        callers that DO use the return value don't get a confusing
        completion value; the real intended communication channel for
        these is `scope` being mutated in place. Confirms a `this;`-
        terminated file still returns the scope object (mutated),
        not globalThis itself."""
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'minesweeper', 'this_convention.js',
            content="var FOO = 99;\nthis;\n")
        script = (
            "var scope = {};\n"
            "var result = load(scope, " + json.dumps(path) + ");\n"
            "process.stdout.write('SAME_AS_SCOPE:' + (result === scope) + ';');\n"
            "process.stdout.write('FOO:' + result.FOO + ';');\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('SAME_AS_SCOPE:true;', output, msg=output)
        self.assertIn('FOO:99;', output, msg=output)

    def test_load_scope_form_ignores_a_trailing_assignment_as_a_fake_result(self):
        """Real regression found live bundling Minesweeper, in the very
        fix directly above this test: dorkit/graphic.js (the same real
        file Bubble Boggle's own `var Graphic = load({}, "graphic.js")`
        already relies on) has no deliberate "Leave as last line"
        trailer at all -- its actual last statement is an ordinary
        prototype-method assignment. A JS assignment EXPRESSION
        evaluates to the assigned value, so the old `result !==
        undefined && result !== _g` check trusted that incidental value
        and returned it instead of `scope` -- silently replacing a real
        constructor function with a throwaway unrelated one. `new
        Ctor(...)` still "worked" (any function can be new'd), but the
        resulting instance had none of the real prototype methods.
        Confirms a file ending in a plain assignment (no this;, no bare
        identifier, no call) still returns `scope`, not the assigned
        value."""
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'minesweeper', 'trailing_assignment.js',
            content=(
                "function Thing() {}\n"
                "Thing.prototype.method = function() { return 'real'; };\n"
                "Thing.prototype.other = function() { return 'other'; };\n"
            ))
        script = (
            "var scope = {};\n"
            "var result = load(scope, " + json.dumps(path) + ");\n"
            "process.stdout.write('SAME_AS_SCOPE:' + (result === scope) + ';');\n"
            "process.stdout.write('HAS_METHOD:' + (typeof result.Thing.prototype.method) + ';');\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('SAME_AS_SCOPE:true;', output, msg=output)
        self.assertIn('HAS_METHOD:function;', output, msg=output)

    def test_format_supports_u_conversion_and_zero_padding(self):
        """Real bug found auditing Minesweeper: format()'s sprintf-style
        regex had no 'u' (unsigned decimal) in its type character class
        at all -- every %u token in minesweeper.js (its game-clock
        display, `format("%2u:%02u", mins, secs)`, and every scoreboard
        column, `format("%3u...%2u...%-3u", w, h, mines)`) would have
        been left completely unexpanded, showing literally "%2u:%02u"
        on screen instead of a real "05:23". Separately, a leading '0'
        width flag (the "%02u" in that same clock format) was silently
        treated as an ordinary width digit with space padding, not a
        zero-pad flag -- confirms both are now fixed together."""
        script = (
            "process.stdout.write('A:' + format('%2u:%02u', 5, 3) + ';');\n"
            "process.stdout.write('B:' + format('%3u x %-3u', 7, 7) + ';');\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('A: 5:03;', output, msg=output)
        self.assertIn('B:  7 x 7  ;', output, msg=output)

    def test_bg_high_and_blink_constants_are_real_globals(self):
        """Real crash found auditing Minesweeper: its very first
        executable statement is `if(BG_HIGH === undefined) BG_HIGH =
        0x400;` -- BG_HIGH (and its sibling BLINK) are real Synchronet
        globals (cga_defs.js's own documented values) that were never
        declared anywhere in the compat template's constants block,
        so that bare reference threw `ReferenceError: BG_HIGH is not
        defined` immediately, before any other door code could run."""
        script = (
            "process.stdout.write('BG_HIGH:' + BG_HIGH + ';');\n"
            "process.stdout.write('BLINK:' + BLINK + ';');\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('BG_HIGH:1024;', output, msg=output)
        self.assertIn('BLINK:128;', output, msg=output)

    def test_file_getname_survives_undefined_like_a_real_spidermonkey_error(self):
        """Real bug found auditing Minesweeper: this file used to carry
        a SECOND, later definition of file_getname() (plus file_exists()
        and directory()) that silently shadowed the earlier, safer one
        -- `function` redeclarations at the same scope resolve to
        whichever one comes LAST in the file, same trap already
        documented for object-literal duplicate keys elsewhere here.
        Minesweeper's own top-level catch-all handler does
        `file_getname(e.fileName)` -- real Synchronet's SpiderMonkey
        Error objects have a real .fileName, but plain V8/Node Errors
        don't, so e.fileName is undefined. The shadowing duplicate
        (`_path.basename(path)`, no coercion) threw a TypeError, turning
        the door's own graceful error report into a raw crash."""
        script = (
            "process.stdout.write('OK:' + JSON.stringify(file_getname(undefined)) + ';');\n"
            "process.stdout.write('EXISTS:' + file_exists(undefined) + ';');\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('OK:', output, msg=output)
        self.assertIn('EXISTS:false;', output, msg=output)

    def test_directory_glob_escapes_literal_dots(self):
        """Sibling half of the same duplicate-shadowing bug: the later
        directory() definition that used to shadow the real one built
        its glob-to-regex conversion without escaping literal dots, so
        e.g. "*.bin" would treat the dot as "match any character"
        instead of a literal dot. Confirms a real directory listing
        with a same-named-but-different-extension file is NOT matched
        by a dotted glob."""
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'minesweeper', 'boom1.bin', content='x')
        # A file that would incorrectly match "boom?.bin" if '.' in the
        # glob were left as "any character" instead of a literal dot.
        other = self._write_synthetic_door_file(
            'sbbs_doors', 'minesweeper', 'boom1Xbin', content='x')
        pattern = json.dumps(str(Path(path).parent) + '/boom?.bin')
        script = (
            "var found = directory(" + pattern + ");\n"
            "process.stdout.write('COUNT:' + found.length + ';');\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('COUNT:1;', output, msg=output)

    def test_sleep_is_a_real_millisecond_global(self):
        """Real bug found live bundling Minesweeper: `sleep()` (its own
        show_image()/play() pacing calls, e.g. `sleep(options.
        boom_delay)`, default 1000) was entirely missing --
        `ReferenceError: sleep is not defined`. Real vendored
        sbbs_stubs/sbbslist_lib.js also calls sleep(1000) -- both
        consistently pass millisecond-scale integers (a 1000-SECOND
        pause between an explosion and its reveal makes no sense),
        so this is implemented as a millisecond-based alias to the
        already-real mswait(), not a fractional-seconds API."""
        script = (
            "var start = Date.now();\n"
            "sleep(30);\n"
            "process.stdout.write('ELAPSED_OK:' + ((Date.now() - start) >= 25) + ';');\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('ELAPSED_OK:true;', output, msg=output)

    def test_dorkit_graphic_js_load_returns_the_real_constructor(self):
        """Real bug found live bundling Minesweeper: dorkit/graphic.js
        (the file load()'s own path search prefers -- same split as the
        cga_defs.js BG_HIGH/BG_BRIGHT issue) has no "Leave as last
        line: Graphic;" trailer the way its flat sibling does, so
        `var Graphic = load({}, "graphic.js")` (Bubble Boggle's and
        Minesweeper's own real calling convention) got `scope` back
        instead of the real constructor once the trailing-assignment
        fix (see the test right above this one in file order) stopped
        trusting the dorkit file's own incidental completion value.
        `new Graphic(...)` then threw "Graphic is not a constructor".
        Confirms load({}, "graphic.js") returns something directly
        `new`-able with real prototype methods."""
        script = (
            "var Graphic = load({}, 'graphic.js');\n"
            "var g = new Graphic(5, 5);\n"
            "process.stdout.write('HAS_LOAD:' + (typeof g.load) + ';');\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('HAS_LOAD:function;', output, msg=output)

    def test_dorkit_graphic_js_load_still_works_on_a_second_call(self):
        """Real bug found live bundling Minesweeper, one layer deeper
        than the test right above this one: show_image() calls
        `var Graphic = load({}, "graphic.js");` on EVERY invocation
        (once per image shown -- welcome/mine/winner/loser/boom), not
        just once. The FIRST call executes the file fresh and correctly
        trusts its completion value; every call AFTER that is a cache
        hit (this shim never re-executes an already-loaded file -- see
        the SyntaxError-on-redeclaration fix elsewhere in load()) with
        no fresh completion value to evaluate at all, so it fell back
        to `scope` again -- `new Graphic(...)` on the SECOND image shown
        threw "Graphic is not a constructor" even though the identical
        call worked moments earlier for the first image. Confirms a
        second, independent load({}, "graphic.js") call in the same
        process still returns the real, `new`-able constructor."""
        script = (
            "var Graphic1 = load({}, 'graphic.js');\n"
            "var g1 = new Graphic1(5, 5);\n"
            "var Graphic2 = load({}, 'graphic.js');\n"
            "var g2 = new Graphic2(5, 5);\n"
            "process.stdout.write('FIRST:' + (typeof g1.load) + ';');\n"
            "process.stdout.write('SECOND:' + (typeof g2.load) + ';');\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('FIRST:function;', output, msg=output)
        self.assertIn('SECOND:function;', output, msg=output)

    def test_dorkit_graphic_draw_supports_center_convenience(self):
        """Real bug found live bundling Minesweeper: dorkit/graphic.js's
        draw() -- the file load()'s own path search prefers, same split
        as the BG_HIGH/BG_BRIGHT and missing-constructor-trailer issues
        found the same way -- had no handling at all for the real
        Synchronet 'center' convenience value for xpos/ypos. Minesweeper's
        own show_image() calls `graphic.draw('center', 'center')` for
        every splash image; without this fix, `ypos + y` (string +
        number) silently string-concatenated instead of computing a
        real row number, and the literal string flowed straight into
        console.gotoxy(), leaking garbage escape-sequence text
        ("enterN;centerH"-shaped) onto the screen instead of ever
        drawing anything. Confirms 'center' now resolves to a real,
        numeric, roughly-centered position instead of leaking through
        as a literal string."""
        script = (
            "var Graphic = load({}, 'graphic.js');\n"
            "var g = new Graphic(10, 5);\n"
            "var calls = [];\n"
            "console.gotoxy = function(x, y) { calls.push(x + ',' + y); };\n"
            "console.attributes = 7;\n"
            "console.print = function() {};\n"
            "g.draw('center', 'center');\n"
            "process.stdout.write('CALLS:' + JSON.stringify(calls) + ';');\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertNotIn('center', output.split('CALLS:')[1] if 'CALLS:' in output else '',
                         'the literal string "center" must never reach a gotoxy call')
        # First row's gotoxy call must be two real numbers, not
        # "center"/"center8"-shaped garbage.
        first_call = output.split('CALLS:')[1].split('"')[1]
        x_str, y_str = first_call.split(',')
        int(x_str)  # raises ValueError (failing the test) if not numeric
        int(y_str)

    def test_file_open_for_write_creates_missing_parent_directory(self):
        """Real bug found live bundling Minesweeper: userprops.js's own
        set() does `new File(system.data_dir + "user/0002.ini");
        file.open(file.exists ? "r+" : "w+")` -- File.prototype.open()
        never touched the filesystem for a write-capable mode (the real
        write happens later, in _writeIni()/etc), and none of THOSE
        writeFileSync calls created missing parent directories either.
        A fresh install's data/user/ directory genuinely doesn't exist
        until something writes to it, so the door's very first save
        (its own selector/highlight/difficulty prefs) silently failed
        with ENOENT every single time, logged but never actually
        persisted. Confirms a write into a not-yet-existing subdirectory
        now actually reaches disk."""
        path = self._write_synthetic_door_file(
            'sbbs_doors', 'minesweeper', 'writer.js',
            content="")
        import os as _os
        base_dir = _os.path.dirname(path)
        target = _os.path.join(base_dir, 'newsubdir', 'prefs.ini')
        self.assertFalse(_os.path.exists(target), 'test setup: must not already exist')
        script = (
            "var f = new File(" + json.dumps(target) + ");\n"
            "var opened = f.open(f.exists ? 'r+' : 'w+');\n"
            "f.iniSetValue('minesweeper', 'selector', '1');\n"
            "f.close();\n"
            "process.stdout.write('OPENED:' + opened + ';');\n"
        )
        output, _status = self._run(script)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('OPENED:true;', output, msg=output)
        self.assertTrue(os.path.isfile(target),
                        'the write must actually land on disk, not silently fail')
        with open(target, 'rb') as fh:
            content = fh.read().decode('latin-1')
        self.assertIn('selector', content)

    def test_missing_file_read_open_is_silent(self):
        """Real bug found live bundling Minesweeper: a plain read-only
        open() of a file that simply doesn't exist yet (ENOENT) used to
        log a scary-looking '[BBS] File.open(...) failed' line every
        single time -- but that's the completely normal, expected
        "no config saved yet" case every door already handles
        gracefully (Minesweeper's own modopts.js lookup and
        winners.jsonl read both hit this on every fresh install,
        forever). A sysop's first launch of any door with no prior
        saved state produced a wall of these for nothing actually
        wrong. Confirms the log line no longer appears for this case."""
        script = (
            "var f = new File('/tmp/definitely_does_not_exist_12345.ini');\n"
            "var opened = f.open('r');\n"
            "process.stdout.write('OPENED:' + opened + ';');\n"
        )
        output, _status = self._run(script)
        self.assertIn('OPENED:false;', output, msg=output)
        self.assertNotIn('File.open', output, msg=output)
        self.assertNotIn('failed', output, msg=output)

    def test_genuine_read_failure_still_logs(self):
        """The other half of the same fix: only a plain "file doesn't
        exist" (ENOENT) read is the benign, expected case -- a
        DIFFERENT real failure reading in read-only mode (here: the
        path is a directory, not a file -- EISDIR, not ENOENT) is
        exactly the kind of genuine problem this logging exists to
        surface, and must still be loud, not silently swallowed along
        with the benign missing-file case."""
        import os as _os
        dir_path = self._write_synthetic_door_file(
            'sbbs_doors', 'minesweeper', 'placeholder.txt', content='x')
        real_dir = _os.path.dirname(dir_path)
        script = (
            "var f = new File(" + json.dumps(real_dir) + ");\n"
            "var opened = f.open('r');\n"
            "process.stdout.write('OPENED:' + opened + ';');\n"
        )
        output, _status = self._run(script)
        self.assertIn('OPENED:false;', output, msg=output)
        self.assertIn('File.open', output, msg=output)
        self.assertIn('failed', output, msg=output)

    def _seed_msgbase_db(self, extra_messages=None):
        """Real on-disk SQLite DB (not :memory:), seeded with one
        EchomailNetwork/EchoArea (tag SYNCDATA) -- the MsgBase tests
        below run a REAL synthetic door through the real Node compat
        shim, which in turn spawns the real msgbase_bridge.py as an
        independent subprocess (see synchronet_compat.py's
        MsgBase._call) -- that subprocess needs a real DB file on disk
        it can open on its own, not an in-process/:memory: shortcut.
        extra_messages, if given, is a list of EchomailMessage kwargs
        (e.g. direction='inbound') seeded directly, for tests needing
        a message the bridge itself can't create (save_msg only ever
        creates direction='outbound' rows -- exactly right for a
        door's own local post, not how a real inbound network message
        arrives)."""
        db_path = os.path.join(self._tmpdir.name, 'msgbase_test.db')
        import anetbbs.config as cfg_mod
        old_dev = cfg_mod.DevelopmentConfig.SQLALCHEMY_DATABASE_URI
        old_testing = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cfg_mod.DevelopmentConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'

        def _restore_cfg():
            cfg_mod.DevelopmentConfig.SQLALCHEMY_DATABASE_URI = old_dev
            cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = old_testing
        self.addCleanup(_restore_cfg)

        from anetbbs.web_app import create_app
        app = create_app('testing')
        with app.app_context():
            from anetbbs.models import db, EchomailNetwork, EchoArea, EchomailMessage
            net = EchomailNetwork(name='DOVE-Net', network_type='binkp',
                                  our_address='1:2/3')
            db.session.add(net)
            db.session.commit()
            area = EchoArea(network_id=net.id, tag='SYNCDATA', name='Synchronet Data')
            db.session.add(area)
            db.session.commit()
            for kwargs in (extra_messages or []):
                db.session.add(EchomailMessage(area_id=area.id, network_id=net.id, **kwargs))
            db.session.commit()

        # The bridge subprocess is a fresh Python process spawned by the
        # door's own Node process (child_process.spawnSync with no
        # explicit env -- inherits whatever's set here at fork time) --
        # unlike the in-process create_app('testing') call above, it
        # reads DevelopmentConfig/ProductionConfig's SQLALCHEMY_DATABASE_URI
        # freshly from DATABASE_URL at its own import time, so the plain
        # env var (not a mutated class attribute) is what actually
        # reaches it.
        old_env_db = os.environ.get('DATABASE_URL')
        old_env_flask = os.environ.get('FLASK_ENV')
        os.environ['DATABASE_URL'] = f'sqlite:///{db_path}'
        os.environ['FLASK_ENV'] = 'development'

        def _restore_env():
            if old_env_db is None:
                os.environ.pop('DATABASE_URL', None)
            else:
                os.environ['DATABASE_URL'] = old_env_db
            if old_env_flask is None:
                os.environ.pop('FLASK_ENV', None)
            else:
                os.environ['FLASK_ENV'] = old_env_flask
        self.addCleanup(_restore_env)

    def test_msgbase_open_save_and_index_round_trip_through_a_real_synthetic_door(self):
        """End-to-end verification of the new MsgBase/DOVE-Net
        score-sharing plumbing (msgbase_bridge.py + synchronet_compat.py's
        MsgBase class), run through a REAL synthetic door script executed
        by the real Node compat shim -- not an in-process shortcut --
        exactly mirroring how Minesweeper's own real get_winners()/
        post_win() code calls MsgBase. Confirms: open() reports
        last_msg=0 against an empty area; save_msg() creates a real row
        this SAME process can then read back via get_index()/
        get_msg_header()/get_msg_body(); get_index()'s CRC16 hashes
        (computed in JS via crc16_calc() -- see that function's own
        comment for why byte-for-byte C compatibility isn't required
        here) are self-consistent -- a subject posted via save_msg is
        later found by hashing that same subject text and comparing
        against the index entry's hash; and from_net_type correctly
        reads false for a message this same door just posted itself
        (not yet round-tripped through a real network), matching
        Minesweeper's own `if (!hdr.from_net_type) continue` filter
        semantics."""
        self._seed_msgbase_db()
        script = (
            "var mb = new MsgBase('SYNCDATA');\n"
            "process.stdout.write('OPEN:' + mb.open() + ';');\n"
            "process.stdout.write('LASTMSG:' + mb.last_msg + ';');\n"
            "var saved = mb.save_msg({from:'StingRay', to:'All', subject:'WinReport'}, 'the body text');\n"
            "process.stdout.write('SAVED:' + saved + ';');\n"
            "var idx = mb.get_index();\n"
            "process.stdout.write('IDXLEN:' + idx.length + ';');\n"
            "var wantSubj = crc16_calc('winreport');\n"
            "var match = idx.filter(function(e){ return e.subject === wantSubj; });\n"
            "process.stdout.write('MATCHCOUNT:' + match.length + ';');\n"
            "var hdr = mb.get_msg_header(false, match[0].number);\n"
            "process.stdout.write('HDRFROM:' + hdr.from + ';');\n"
            "process.stdout.write('HDRNETTYPE:' + hdr.from_net_type + ';');\n"
            "var body = mb.get_msg_body(hdr);\n"
            "process.stdout.write('BODY:' + body + ';');\n"
        )
        output, _status = self._run(script, run_seconds=15)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('OPEN:true;', output, msg=output)
        self.assertIn('LASTMSG:0;', output, msg=output)
        self.assertIn('SAVED:true;', output, msg=output)
        self.assertIn('IDXLEN:1;', output, msg=output)
        self.assertIn('MATCHCOUNT:1;', output, msg=output)
        self.assertIn('HDRFROM:StingRay;', output, msg=output)
        self.assertIn('HDRNETTYPE:false;', output, msg=output)
        self.assertIn('BODY:the body text;', output, msg=output)

    def test_msgbase_get_msg_header_reports_from_net_type_true_for_an_inbound_message(self):
        """A message that genuinely arrived via the network (direction=
        'inbound' -- e.g. another BBS's own win report, tossed in
        through the normal BinkP pipeline) must report
        from_net_type:true, the real Synchronet signal Minesweeper's
        own get_winners() gates on to decide whether a message counts
        as a real InterBBS win at all. Seeded directly rather than via
        save_msg (which only ever creates direction='outbound' rows)."""
        self._seed_msgbase_db(extra_messages=[dict(
            from_name='OtherSysop', to_name='All', subject='TheirWin',
            body='they won too', direction='inbound')])
        script = (
            "var mb = new MsgBase('SYNCDATA');\n"
            "mb.open();\n"
            "var idx = mb.get_index();\n"
            "var hdr = mb.get_msg_header(false, idx[0].number);\n"
            "process.stdout.write('NETTYPE:' + hdr.from_net_type + ';');\n"
            "process.stdout.write('FROM:' + hdr.from + ';');\n"
        )
        output, _status = self._run(script, run_seconds=15)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('NETTYPE:true;', output, msg=output)
        self.assertIn('FROM:OtherSysop;', output, msg=output)

    def test_get_msg_header_and_body_serve_from_cache_after_get_index(self):
        """Regression for a real report: DOVE-Net score-sharing in
        Minesweeper's own get_winners() looked like a total lockup on
        "view winners". Not an infinite loop -- get_index() itself was
        fine, but the JS shim's get_msg_header()/get_msg_body() each
        spawned a brand-new Python subprocess (fresh Flask app +
        SQLAlchemy startup) PER MATCHING MESSAGE in get_winners()'s own
        loop. With a normally-sized amount of synced InterBBS history
        that's potentially hundreds of sequential subprocess spawns with
        no progress indicator -- easily minutes of wall time,
        indistinguishable from a hang.

        Fixed by having msgbase_bridge.py's op_get_index embed each
        entry's header+body fields inline (one query already has them
        loaded) and having the JS MsgBase class cache them per message
        number, so get_msg_header()/get_msg_body() serve straight from
        memory for any message get_index() already saw.

        Proven here by deliberately breaking js.msgbase_bridge (pointing
        it at a nonexistent path) AFTER get_index() has run, for TWO
        separate messages -- if get_msg_header()/get_msg_body() still
        needed to shell out, every one of the four calls below would
        fail (spawnSync against a missing path -> {ok:false} ->
        undefined header / empty body), not just return correct,
        message-specific data for both messages."""
        self._seed_msgbase_db()
        script = (
            "var mb = new MsgBase('SYNCDATA');\n"
            "mb.open();\n"
            "mb.save_msg({from:'Alice', to:'All', subject:'Win1'}, 'alice body');\n"
            "mb.save_msg({from:'Bob', to:'All', subject:'Win2'}, 'bob body');\n"
            "var idx = mb.get_index();\n"
            "process.stdout.write('IDXLEN:' + idx.length + ';');\n"
            "js.msgbase_bridge = '/nonexistent/anetbbs_msgbase_bridge_broken.py';\n"
            "var hdr0 = mb.get_msg_header(false, idx[0].number);\n"
            "var body0 = mb.get_msg_body(hdr0);\n"
            "var hdr1 = mb.get_msg_header(false, idx[1].number);\n"
            "var body1 = mb.get_msg_body(hdr1);\n"
            "process.stdout.write('H0FROM:' + hdr0.from + ';B0:' + body0 + ';');\n"
            "process.stdout.write('H1FROM:' + hdr1.from + ';B1:' + body1 + ';');\n"
        )
        output, _status = self._run(script, run_seconds=15)
        self.assertNotIn('Error', output, msg=output)
        self.assertIn('IDXLEN:2;', output, msg=output)
        self.assertIn('H0FROM:Alice;B0:alice body;', output, msg=output)
        self.assertIn('H1FROM:Bob;B1:bob body;', output, msg=output)


if __name__ == '__main__':
    unittest.main()
