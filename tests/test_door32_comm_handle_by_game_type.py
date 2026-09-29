"""Regression test for a real bug found live (2026-09-29, "door32.sys
doesn't work for some [games] because of the socket issue"):
write_drop_file()'s DOOR32.SYS branch only special-cased
game_type == 'door_native' for the comm_handle=-1 "Mystic STDIO
convention" fix (telling the door to use stdin/stdout since there is no
real socket handle under ANetBBS's PTY-based door-launch architecture).

door_mystic, door_mystic_mps, and door_synchronet are launched through
the exact same PTY-forking mechanism (anetbbs/games/door_runner.py's
pty.openpty() call is unconditional across every game type) -- they
were silently falling through to the DOS/FOSSIL default (comm_type=1,
handle=0) instead, telling those doors to look for a COM1/FOSSIL
interface that doesn't exist in this architecture.

door_dos/door_dosemu are the one real exception: they run under
dosemu2's own COM1-nullmodem-bridge emulation, so the FOSSIL default is
actually correct for them.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _FakeUser:
    def __init__(self):
        self.id = 1
        self.username = 'testuser'
        self.is_admin = False


class Door32CommHandleByGameTypeTests(unittest.TestCase):
    def _write_and_read(self, game_type, tmp_path):
        from anetbbs.games.dropfile import write_drop_file
        game = MagicMock()
        game.drop_file_type = 'door32.sys'
        game.drop_file_path = str(tmp_path)
        game.game_type = game_type
        path = write_drop_file(_FakeUser(), game, node_number=1)
        # newline='' disables universal-newline translation on read --
        # without it, Python's default text mode silently normalizes the
        # file's real \r\n bytes to \n before we ever get to split on
        # \r\n ourselves, always looking like a single joined line.
        with open(path, newline='') as f:
            lines = f.read().split('\r\n')
        return lines[0], lines[1]  # comm_type, comm_handle

    def test_door_native_uses_stdio_sentinel(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            comm_type, comm_handle = self._write_and_read('door_native', Path(d) / 'x')
            self.assertEqual(comm_type, '2')
            self.assertEqual(comm_handle, '-1')

    def test_door_mystic_uses_stdio_sentinel(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            comm_type, comm_handle = self._write_and_read('door_mystic', Path(d) / 'x')
            self.assertEqual(comm_type, '2')
            self.assertEqual(comm_handle, '-1')

    def test_door_mystic_mps_uses_stdio_sentinel(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            comm_type, comm_handle = self._write_and_read('door_mystic_mps', Path(d) / 'x')
            self.assertEqual(comm_type, '2')
            self.assertEqual(comm_handle, '-1')

    def test_door_synchronet_uses_stdio_sentinel(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            comm_type, comm_handle = self._write_and_read('door_synchronet', Path(d) / 'x')
            self.assertEqual(comm_type, '2')
            self.assertEqual(comm_handle, '-1')

    def test_door_dos_still_uses_fossil_default(self):
        # door_dos/door_dosemu run under dosemu2's own COM1-nullmodem
        # bridge -- the FOSSIL default is correct for them, not a bug.
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            comm_type, comm_handle = self._write_and_read('door_dos', Path(d) / 'x')
            self.assertEqual(comm_type, '1')
            self.assertEqual(comm_handle, '0')

    def test_door_dosemu_still_uses_fossil_default(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            comm_type, comm_handle = self._write_and_read('door_dosemu', Path(d) / 'x')
            self.assertEqual(comm_type, '1')
            self.assertEqual(comm_handle, '0')


if __name__ == '__main__':
    unittest.main()
