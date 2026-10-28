"""File integrity helpers shared by collection recovery and exports."""

import hashlib
from pathlib import Path


def file_sha256(path: str | Path) -> str:
    """Return a file's SHA-256 digest without loading the whole file in memory."""
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def matches_sha256(path: str | Path, expected: str | None) -> bool:
    """Return whether a regular file matches a recorded SHA-256 digest."""
    if not expected:
        return False
    try:
        file_path = Path(path)
        return file_path.is_file() and file_sha256(file_path) == expected
    except OSError:
        return False
