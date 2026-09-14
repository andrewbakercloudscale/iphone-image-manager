"""Read-only access to the Photos library database.

PhotoKit has no API for the source application, and it is the single most
valuable field in the library: it is what makes "clean up WhatsApp images" exact
rather than a filename guess. It lives in `Photos.sqlite`, so we read it
directly, the same technique `osxphotos` uses.

Two disciplines follow from that being an undocumented schema:

- always work on a copy, never the live file, because Photos writes to it
  constantly and holding a lock on someone's photo library is antisocial;
- a missing or renamed column degrades the classification and says so. It must
  never crash, and it must never quietly report "no WhatsApp assets found",
  which would read as a clean library rather than a broken reader.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..logs import get_logger

log = get_logger("photos.library_db")

#: Columns we need, and the table they should be on.
_SOURCE_SQL = """
    SELECT a.ZUUID AS uuid,
           attr.ZIMPORTEDBYBUNDLEIDENTIFIER AS bundle_id,
           attr.ZIMPORTEDBYDISPLAYNAME AS display_name
    FROM ZASSET a
    JOIN ZADDITIONALASSETATTRIBUTES attr ON attr.ZASSET = a.Z_PK
    WHERE a.ZTRASHEDSTATE = 0
"""


class LibraryDbError(Exception):
    """Raised when the Photos database cannot be read at all."""


@dataclass
class SourceApps:
    """Source application per asset UUID, plus how much of the library it covered."""

    by_uuid: dict[str, str | None]
    total: int
    with_bundle_id: int
    degraded_reason: str | None = None

    @property
    def coverage(self) -> float:
        return self.with_bundle_id / self.total if self.total else 0.0

    def for_local_identifier(self, local_identifier: str) -> str | None:
        """PhotoKit gives 'UUID/L0/001'; the database keys on the UUID alone."""
        return self.by_uuid.get(local_identifier.split("/", 1)[0])


def _snapshot(library: Path) -> Path:
    source = library / "database" / "Photos.sqlite"
    if not source.is_file():
        raise LibraryDbError(f"no Photos database inside {library}")
    directory = Path(tempfile.mkdtemp(prefix="iim-photosdb-"))
    target = directory / "Photos.sqlite"
    try:
        shutil.copy2(source, target)
        for suffix in ("-wal", "-shm"):
            extra = source.with_name(source.name + suffix)
            if extra.exists():
                shutil.copy2(extra, target.with_name(target.name + suffix))
    except PermissionError as exc:
        raise LibraryDbError(
            f"cannot read {source}. Grant Full Disk Access to your terminal in "
            f"System Settings > Privacy & Security, then restart it."
        ) from exc
    except OSError as exc:
        raise LibraryDbError(f"cannot copy {source}: {exc}") from exc
    return target


def read_source_apps(library: Path) -> SourceApps:
    """Map asset UUID to the bundle id of the app that imported it."""
    snapshot = _snapshot(library)
    try:
        conn = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(_SOURCE_SQL).fetchall()
        except sqlite3.OperationalError as exc:
            # The schema moved. Say so loudly: silently returning nothing would
            # read as "this library contains no imported media".
            reason = (
                f"the Photos schema does not match what this build expects ({exc}). "
                f"Source application is unavailable, so channel filters such as "
                f"--source whatsapp cannot be used until this is fixed."
            )
            log.warning("%s", reason)
            return SourceApps(by_uuid={}, total=0, with_bundle_id=0, degraded_reason=reason)
        finally:
            conn.close()
    finally:
        shutil.rmtree(snapshot.parent, ignore_errors=True)

    by_uuid: dict[str, str | None] = {}
    with_bundle = 0
    for row in rows:
        bundle = row["bundle_id"]
        by_uuid[row["uuid"]] = bundle
        if bundle:
            with_bundle += 1

    log.info(
        "read source application for %d assets, %d carry a bundle id (%.0f%%)",
        len(rows),
        with_bundle,
        100 * with_bundle / len(rows) if rows else 0,
    )
    return SourceApps(by_uuid=by_uuid, total=len(rows), with_bundle_id=with_bundle)
