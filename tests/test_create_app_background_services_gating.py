"""Regression test for a real live incident: running the new anetbbs-cfg
terminal tool (v1.0.20) on a live install started a SECOND full copy of
every persistent background service (echomail/RSS pollers, MSP/SYSTAT
listeners, the ANetBBS directory refresher, the metrics sampler, the
scheduled-events runner) alongside the already-running anetbbs-web
process -- double-polling echomail, double-firing scheduled events, all
just to open a local config screen. `create_app()` only ever gated these
on `app.config.get('TESTING', False)`, so any one-shot CLI use of
create_app() outside pytest (anetbbs-cfg, but also update.sh's own
schema-migration step) got the full production service stack whether it
wanted it or not.

Fixed by extending the ANETBBS_SCHEMA_MIGRATE_ONLY env var (already used
to bypass the SECRET_KEY production check for one-shot commands) to also
gate every one of these background-service starts. This test verifies
both directions: the flag actually suppresses all of them, AND a normal
boot (flag unset) still starts all of them -- an overly-broad fix that
silently skipped these in real production would be just as bad as the
original bug.

All the underlying start_*() functions are mocked in both directions:
even if the gating logic were completely broken, this test must never
actually spin up real threads/sockets/network calls.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# One representative target per unconditionally-gated background service
# in web_app.py's create_app() (the block gated only by
# `not (TESTING or _one_shot_cli)`, not also behind a separate
# REGISTRY_MODE_ENABLED/REGISTRY_SELF_REGISTER off-by-default flag).
PATCH_TARGETS = [
    "anetbbs.echomail.poller.start_poller",
    "anetbbs.rss.poller.start_poller",
    "anetbbs.msp.server.start_msp_server",
    "anetbbs.msp.systat.start_systat_server",
    "anetbbs.msp.directory.start_refresher",
    "anetbbs.msp.anetbbs_directory.start_anetbbs_directory_refresher",
    "anetbbs.web.metrics.start_sampler",
    "anetbbs.events.runner.start_event_scheduler",
    "anetbbs.events.runner.ensure_default_events",
]


class BackgroundServicesGatingTests(unittest.TestCase):
    def setUp(self):
        self.tmp_db = Path(__file__).resolve().parent / ".bg_services_gate_test.db"
        if self.tmp_db.exists():
            self.tmp_db.unlink()
        self._old_env = {
            k: os.environ.get(k)
            for k in ("DATABASE_URL", "ANETBBS_SCHEMA_MIGRATE_ONLY", "FLASK_ENV")
        }
        os.environ["DATABASE_URL"] = f"sqlite:///{self.tmp_db}"
        os.environ.pop("ANETBBS_SCHEMA_MIGRATE_ONLY", None)

    def tearDown(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if self.tmp_db.exists():
            self.tmp_db.unlink()

    def test_schema_migrate_only_skips_all_background_services(self):
        os.environ["ANETBBS_SCHEMA_MIGRATE_ONLY"] = "1"
        patchers = [mock.patch(t) for t in PATCH_TARGETS]
        mocks = [p.start() for p in patchers]
        try:
            from anetbbs.web_app import create_app
            create_app("development")
        finally:
            for p in patchers:
                p.stop()
        for target, m in zip(PATCH_TARGETS, mocks):
            self.assertFalse(m.called, f"{target} should NOT have been called")

    def test_normal_boot_still_starts_all_background_services(self):
        os.environ.pop("ANETBBS_SCHEMA_MIGRATE_ONLY", None)
        patchers = [mock.patch(t) for t in PATCH_TARGETS]
        mocks = [p.start() for p in patchers]
        try:
            from anetbbs.web_app import create_app
            create_app("development")
        finally:
            for p in patchers:
                p.stop()
        for target, m in zip(PATCH_TARGETS, mocks):
            self.assertTrue(m.called, f"{target} SHOULD have been called")


if __name__ == "__main__":
    unittest.main()
