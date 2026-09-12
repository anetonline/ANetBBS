"""Regression test for a real gap found in a security/performance
audit: EchomailLastRead.last_message_id and QWKNodeLastSent.last_message_id
are the exact same shape -- both store "the highest EchomailMessage.id
this consumer has seen/sent" -- and QWKNodeLastSent correctly declares
a real db.ForeignKey('echomail_messages.id') on it, but its sibling
column on EchomailLastRead was left a bare Integer with no FK at all.
Confirms the column now carries the same ForeignKey as its sibling.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.models import EchomailLastRead, QWKNodeLastSent


class EchomailLastReadForeignKeyTests(unittest.TestCase):
    def test_last_message_id_has_a_foreign_key_to_echomail_messages(self):
        col = EchomailLastRead.__table__.c.last_message_id
        target_tables = {fk.column.table.name for fk in col.foreign_keys}
        self.assertIn('echomail_messages', target_tables,
                       'EchomailLastRead.last_message_id should reference '
                       'echomail_messages.id, same as its sibling column '
                       'QWKNodeLastSent.last_message_id')

    def test_matches_the_sibling_columns_target(self):
        """Both columns represent the identical concept -- confirm they
        now point at the same target table, not just that each has
        *some* FK."""
        echo_targets = {fk.column.table.name
                        for fk in EchomailLastRead.__table__.c.last_message_id.foreign_keys}
        qwk_targets = {fk.column.table.name
                       for fk in QWKNodeLastSent.__table__.c.last_message_id.foreign_keys}
        self.assertEqual(echo_targets, qwk_targets)

    def test_column_stays_nullable(self):
        """NULL still means 'tracked but nothing read yet' -- the FK
        addition must not turn this into a NOT NULL column."""
        self.assertTrue(EchomailLastRead.__table__.c.last_message_id.nullable)


if __name__ == '__main__':
    unittest.main()
