"""Bring removed items back from the cloud, checked against the ledger's hash.

For 30 days the phone's Recently Deleted album is the way back, and it keeps
everything Photos knew. After that, the cloud copy is. This downloads it into
a fresh folder, never over anything, and hashes each file: a download that does
not match the hash recorded when it left the phone is reported, not trusted.

Reads only from the cloud. Adding the files back to Photos is the user's step.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .cloud import RcloneProvider, split_cloud_path
from .config import Config
from .db.database import Database
from .logs import get_logger

log = get_logger("recover")


class RecoverError(RuntimeError):
    pass


@dataclass
class RecoverResult:
    folder: Path
    files: list[dict[str, Any]] = field(default_factory=list)

    @property
    def verified(self) -> int:
        return sum(1 for f in self.files if f["verified"])


def _sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None


def default_folder() -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d %H.%M.%S")
    return Path.home() / "Downloads" / "Photo Sentinel recovered" / stamp


def run(
    config: Config,
    db: Database,
    ids: list[int],
    *,
    to: Path | None = None,
    provider: Any = None,
) -> RecoverResult:
    if not ids:
        raise RecoverError("name at least one item with --id")
    marks = ",".join("?" for _ in ids)
    rows = db.conn.execute(
        f"SELECT id, filename, cloud_path, sha256 FROM assets WHERE id IN ({marks})", ids
    ).fetchall()
    missing = sorted(set(ids) - {int(r["id"]) for r in rows})
    if missing:
        raise RecoverError(f"no such item in the ledger: {', '.join(map(str, missing))}")
    no_copy = [r["filename"] or str(r["id"]) for r in rows if not r["cloud_path"]]
    if no_copy:
        raise RecoverError(f"no cloud copy is recorded for: {', '.join(no_copy[:5])}")

    folder = to or default_folder()
    if folder.exists() and any(folder.iterdir()):
        raise RecoverError(f"{folder} is not empty. Recovery never writes over anything.")
    folder.mkdir(parents=True, exist_ok=True)

    provider = provider or RcloneProvider(config.cloud.remote)
    by_destination: dict[str, list[tuple[str, Any]]] = {}
    for row in rows:
        destination, relative = split_cloud_path(config, row["cloud_path"])
        by_destination.setdefault(destination, []).append((relative, row))

    result = RecoverResult(folder=folder)
    for destination, items in by_destination.items():
        provider.download(destination, [relative for relative, _ in items], folder)
        for relative, row in items:
            path = folder / relative
            actual = _sha256(path) if path.exists() else None
            result.files.append(
                {
                    "id": int(row["id"]),
                    "filename": row["filename"],
                    "path": str(path),
                    "downloaded": path.exists(),
                    "verified": bool(actual) and actual == row["sha256"],
                }
            )
    log.info("recovered %d of %d into %s", result.verified, len(rows), folder)
    return result
