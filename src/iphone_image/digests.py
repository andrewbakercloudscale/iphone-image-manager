"""File digests the ledger records, read once for every hash a remote may report."""

from __future__ import annotations

import hashlib
from pathlib import Path

HASH_BLOCK = 1024 * 1024


def file_digests(path: Path) -> tuple[str, str]:
    """SHA-256 and MD5 in one read: Drive is checked by the first, S3 by the second."""
    sha, md5 = hashlib.sha256(), hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        while block := handle.read(HASH_BLOCK):
            sha.update(block)
            md5.update(block)
    return sha.hexdigest(), md5.hexdigest()
