"""Regression test for a real gap found in a security/performance audit:
anetbbs/msp/hub_selftest.py's start_hub_selftest_thread() was the only
background-thread starter in the msp/ package with no idempotency guard
-- every sibling (probe.py's start_probe_thread, hub_self_register.py's
start_hub_self_register_thread, directory.py's start_refresher,
anetbbs_directory.py's start_anetbbs_directory_refresher, server.py's
start_msp_server, systat.py's start_systat_server) tracks its thread in a
module-level global and checks `is_alive()` before spawning another one.

Without the guard, calling start_hub_selftest_thread() more than once in
the same process (e.g. web_app.create_app() being invoked again, which
is a real, supported pattern -- a dev-server reload, or anything else
that re-runs the app factory) spawned an additional `while True` daemon
thread every time, each looping forever with no way to tell it apart
from -- or stop it independently of -- the others.

Fixed by adding the same `_thread` global + `is_alive()` guard every
sibling module already has, plus a `_stop` Event / stop_hub_selftest_
thread() pair so the (now singular) thread can actually be told to
exit instead of only dying with the process -- matching every sibling's
own stop_*() shape.
"""
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask


def _make_app(tmp_path):
    app = Flask(__name__)
    # REGISTRY_URL left unset -- _run_once() logs a WARN line and
    # returns immediately without touching the DB or the network, which
    # is all these tests need from a single pass.
    app.config['INSTALL_DIR'] = str(tmp_path)
    return app


class HubSelftestThreadIdempotentTests(unittest.TestCase):
    def setUp(self):
        from anetbbs.msp import hub_selftest
        self.hub_selftest = hub_selftest
        # Clean slate regardless of what an earlier test (in this file
        # or elsewhere in a full-suite run) left running.
        hub_selftest.stop_hub_selftest_thread()
        if hub_selftest._thread is not None:
            hub_selftest._thread.join(timeout=2)
        hub_selftest._thread = None

    def tearDown(self):
        self.hub_selftest.stop_hub_selftest_thread()
        if self.hub_selftest._thread is not None:
            self.hub_selftest._thread.join(timeout=2)
        self.hub_selftest._thread = None

    def _live_hub_selftest_threads(self):
        return [t for t in threading.enumerate()
                if t.name == 'anetbbs-hub-selftest' and t.is_alive()]

    def test_start_is_idempotent_within_one_process(self):
        app = _make_app(Path('/tmp'))

        first = self.hub_selftest.start_hub_selftest_thread(
            app, interval_sec=3600)
        second = self.hub_selftest.start_hub_selftest_thread(
            app, interval_sec=3600)

        self.assertIs(
            first, second,
            'a second start_hub_selftest_thread() call while the first '
            'thread is still alive must return the SAME thread, not '
            'spawn a new one')
        self.assertEqual(
            len(self._live_hub_selftest_threads()), 1,
            'calling start_hub_selftest_thread() twice must never leave '
            'more than one anetbbs-hub-selftest thread alive at once')

    def test_stop_interrupts_the_initial_boot_delay(self):
        """The pre-fix version used a bare time.sleep(600) for the
        initial boot delay -- completely uninterruptible. stop_*() must
        make the thread exit promptly, not just eventually when the
        process itself dies."""
        app = _make_app(Path('/tmp'))
        self.hub_selftest.start_hub_selftest_thread(app, interval_sec=3600)
        self.assertEqual(len(self._live_hub_selftest_threads()), 1)

        self.hub_selftest.stop_hub_selftest_thread()
        self.hub_selftest._thread.join(timeout=2)

        self.assertFalse(
            self.hub_selftest._thread.is_alive(),
            'stop_hub_selftest_thread() must unblock the thread promptly '
            '(well under the 600s initial boot delay), not leave it '
            'sleeping until the process exits')


if __name__ == '__main__':
    unittest.main()
