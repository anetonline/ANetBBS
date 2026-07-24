"""Tests for anetbbs/core/tz.py -- the shared UTC-to-Eastern display
conversion added because every timestamp in the app was previously
shown to sysops/users as raw UTC with zero conversion anywhere,
despite repeated requests for Eastern display. Storage stays UTC; this
is purely a display-layer helper.
"""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.core.tz import to_eastern, fmt_eastern


class ToEasternTests(unittest.TestCase):
    def test_none_returns_none(self):
        self.assertIsNone(to_eastern(None))

    def test_naive_datetime_treated_as_utc(self):
        # 2026-07-24 14:26 UTC (summer -> EDT, UTC-4) -> 10:26 EDT.
        naive_utc = datetime(2026, 7, 24, 14, 26)
        result = to_eastern(naive_utc)
        self.assertEqual((result.hour, result.minute), (10, 26))
        self.assertEqual(result.strftime('%Z'), 'EDT')

    def test_winter_date_uses_est_not_edt(self):
        # 2026-01-15 14:26 UTC (winter -> EST, UTC-5) -> 09:26 EST.
        naive_utc = datetime(2026, 1, 15, 14, 26)
        result = to_eastern(naive_utc)
        self.assertEqual((result.hour, result.minute), (9, 26))
        self.assertEqual(result.strftime('%Z'), 'EST')

    def test_aware_utc_datetime_also_converts_correctly(self):
        aware_utc = datetime(2026, 7, 24, 14, 26, tzinfo=timezone.utc)
        result = to_eastern(aware_utc)
        self.assertEqual((result.hour, result.minute), (10, 26))

    def test_iso_string_with_z_suffix_also_converts(self):
        """A few JSON-facing modules (msp/registry_client.py,
        web/upgrades.py) store a heartbeat/published timestamp as a
        pre-formatted ISO-8601 string rather than a real datetime
        column -- must still convert correctly for display."""
        result = to_eastern('2026-07-24T14:26:00Z')
        self.assertEqual((result.hour, result.minute), (10, 26))

    def test_unparseable_string_returned_unchanged(self):
        """Fails open -- this helper's job is display, not validation."""
        self.assertEqual(to_eastern('not a date'), 'not a date')


class FmtEasternTests(unittest.TestCase):
    def test_formats_in_eastern(self):
        naive_utc = datetime(2026, 7, 24, 14, 26)
        self.assertEqual(fmt_eastern(naive_utc, '%Y-%m-%d %H:%M'),
                         '2026-07-24 10:26')

    def test_percent_z_gives_correct_dst_aware_abbreviation(self):
        summer = datetime(2026, 7, 24, 14, 26)
        winter = datetime(2026, 1, 15, 14, 26)
        self.assertEqual(fmt_eastern(summer, '%H:%M %Z'), '10:26 EDT')
        self.assertEqual(fmt_eastern(winter, '%H:%M %Z'), '09:26 EST')

    def test_none_returns_default(self):
        self.assertEqual(fmt_eastern(None, default='never'), 'never')
        self.assertEqual(fmt_eastern(None), '')


class JinjaFilterTests(unittest.TestCase):
    """Confirm the `eastern` Jinja filter is registered and behaves
    the same as the underlying fmt_eastern() helper."""

    def test_filter_registered_and_matches_helper(self):
        import os
        import anetbbs.config as cfg_mod
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        app = create_app('testing')
        naive_utc = datetime(2026, 7, 24, 14, 26)
        with app.test_request_context():
            from flask import render_template_string
            rendered = render_template_string(
                "{{ dt|eastern('%Y-%m-%d %H:%M') }}", dt=naive_utc)
        self.assertEqual(rendered, '2026-07-24 10:26')

    def test_filter_default_for_none(self):
        import os
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        app = create_app('testing')
        with app.test_request_context():
            from flask import render_template_string
            rendered = render_template_string(
                "{{ dt|eastern('%Y-%m-%d %H:%M', 'never') }}", dt=None)
        self.assertEqual(rendered, 'never')


if __name__ == '__main__':
    unittest.main()
