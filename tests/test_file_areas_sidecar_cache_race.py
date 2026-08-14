"""Regression test: every JSON sidecar cache read-modify-write in
anetbbs/web/file_areas.py (.descriptions.json / .hashes.json) used to
be a bare load-then-save pair with no locking -- two concurrent writes
to the SAME area (two uploads racing, an upload racing a manage/desc
edit, etc; this app runs multi-threaded) could both load the same
starting snapshot, each apply their own change in memory, then each
overwrite the file in turn, silently discarding whichever change lost
the race. Found in a security/performance audit.

Fixed with _update_json_sidecar(): the read, mutate, and write now all
happen inside one fcntl.flock-held critical section, re-reading the
CURRENT on-disk content at write time rather than trusting a
possibly-stale unlocked snapshot from earlier.

This test drives real concurrent threads (not just a logical/API
check) hammering _check_and_record_dupe() (.hashes.json) and the
manage_desc-style description update (.descriptions.json) on the SAME
area at once, and confirms every single one of N distinct entries
survives -- proving the race is actually closed, not just less likely.
"""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _FakeArea:
    def __init__(self, storage_path, tag='TEST'):
        self.storage_path = storage_path
        self.tag = tag


class SidecarCacheRaceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.area = _FakeArea(self._tmp.name)

    def _write(self, name, content):
        path = os.path.join(self._tmp.name, name)
        with open(path, 'wb') as f:
            f.write(content)
        return path

    def test_concurrent_hash_cache_writes_all_survive(self):
        from anetbbs.web.file_areas import (_check_and_record_dupe,
                                            _read_json_sidecar, _hash_cache_path)

        N = 30
        dests = [self._write(f'file{i}.zip', f'unique-content-{i}'.encode())
                 for i in range(N)]

        barrier = threading.Barrier(N)
        errors = []

        def _worker(i):
            try:
                barrier.wait(timeout=5)
                _check_and_record_dupe(self.area, dests[i], f'file{i}.zip')
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(errors, [])
        cache = _read_json_sidecar(_hash_cache_path(self.area))
        self.assertEqual(len(cache), N,
                         f'expected all {N} concurrent hash-cache writes to '
                         f'survive, got {len(cache)} -- some were silently '
                         f'clobbered by a racing writer')
        self.assertEqual(set(cache.values()),
                         {f'file{i}.zip' for i in range(N)})

    def test_concurrent_description_updates_all_survive(self):
        from anetbbs.web.file_areas import (_update_json_sidecar,
                                            _read_json_sidecar, _desc_cache_path)

        N = 30
        barrier = threading.Barrier(N)
        errors = []

        def _worker(i):
            try:
                barrier.wait(timeout=5)

                def _mutate(cache, i=i):
                    cache[f'file{i}.zip'] = {'mtime': i, 'size': i,
                                             'description': f'desc-{i}'}
                    return cache
                _update_json_sidecar(_desc_cache_path(self.area), _mutate)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(errors, [])
        cache = _read_json_sidecar(_desc_cache_path(self.area))
        self.assertEqual(len(cache), N,
                         f'expected all {N} concurrent description writes to '
                         f'survive, got {len(cache)} -- some were silently '
                         f'clobbered by a racing writer')
        for i in range(N):
            self.assertEqual(cache[f'file{i}.zip']['description'], f'desc-{i}')

    def test_delete_mutation_correctly_removes_key_not_just_merges(self):
        """Guard against a naive 'merge my old snapshot back in' fix,
        which can't distinguish 'never loaded this key' from
        'deliberately removed it' -- _update_json_sidecar's mutate
        callback re-applies against fresh on-disk state, so a pop()
        genuinely removes the key rather than it silently reappearing
        via a merge."""
        from anetbbs.web.file_areas import (_check_and_record_dupe,
                                            _update_json_sidecar,
                                            _read_json_sidecar, _hash_cache_path)
        dest = self._write('gone.zip', b'will be deleted')
        _check_and_record_dupe(self.area, dest, 'gone.zip')
        cache = _read_json_sidecar(_hash_cache_path(self.area))
        self.assertEqual(len(cache), 1)

        def _mutate(cache):
            cache.pop(next(iter(cache)), None)
            return cache
        _update_json_sidecar(_hash_cache_path(self.area), _mutate)

        cache = _read_json_sidecar(_hash_cache_path(self.area))
        self.assertEqual(cache, {})


if __name__ == '__main__':
    unittest.main()
