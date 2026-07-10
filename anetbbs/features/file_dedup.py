# anetbbs/features/file_dedup.py
"""
Shared content-hashing helper for duplicate-upload detection.

Before this existed, nothing in any of the four upload code paths
(anetbbs/web/files.py's upload(), anetbbs/web/file_areas.py's upload(),
manage_upload(), and smart_upload()) computed a hash of an uploaded
file's contents at all -- a user could upload the exact same bytes
under a different filename with no warning.

files.py has a real per-file DB row (FileUpload.content_hash) to query
against. file_areas.py's three routes have no per-file DB row at all
(files are listed by scanning the directory at request time) -- they use
a per-area sidecar cache instead, same pattern as the existing
.descriptions.json cache in file_areas.py.
"""
import hashlib


def hash_file(path, chunk_size=65536):
    """Return the sha256 hex digest of the file at *path*, streamed in
    chunks so large uploads don't need to fit in memory at once."""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()
