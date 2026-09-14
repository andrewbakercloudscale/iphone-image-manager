"""The operations journal.

Every significant action writes a STARTED row and commits it *before* doing the
work, then updates it afterwards. A crash therefore leaves a row that says what
was in flight, which is the difference between a recoverable interruption and a
mystery.

This matters most for removal: `deletion_events` and this journal are what let a
later run prove which assets were already gone rather than re-issuing deletes.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from .db.database import Database, utcnow


class Op:
    """Operation names. Spec section 33, extended where the plan needed it."""

    SCAN_STARTED = "SCAN_STARTED"
    SCAN_COMPLETED = "SCAN_COMPLETED"
    DOWNLOAD = "DOWNLOAD"
    DOWNLOAD_VERIFIED = "DOWNLOAD_VERIFIED"
    CLOUD_UPLOAD = "CLOUD_UPLOAD"
    CLOUD_UPLOAD_VERIFIED = "CLOUD_UPLOAD_VERIFIED"
    VERIFY = "VERIFY"
    CAMPAIGN_START = "CAMPAIGN_START"
    CAMPAIGN_CLOSE = "CAMPAIGN_CLOSE"
    RECYCLE_BIN_WRITE = "RECYCLE_BIN_WRITE"
    REMOVE = "REMOVE"
    TRASH = "TRASH"
    RELOCATE = "RELOCATE"
    MIGRATE = "MIGRATE"


class Status:
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class OperationHandle:
    """Live handle to an open journal row, so a caller can attach findings."""

    id: int
    operation: str
    _detail: dict[str, Any] = field(default_factory=dict)
    retry_count: int = 0

    def note(self, **values: Any) -> None:
        self._detail.update(values)

    def retried(self) -> None:
        self.retry_count += 1


class Journal:
    def __init__(self, db: Database) -> None:
        self.db = db

    def start(
        self,
        operation: str,
        *,
        command: str | None = None,
        device_id: int | None = None,
        asset_id: int | None = None,
        campaign_id: int | None = None,
        scan_id: int | None = None,
        detail: dict[str, Any] | None = None,
    ) -> OperationHandle:
        cursor = self.db.conn.execute(
            "INSERT INTO operations "
            "(operation, status, device_id, asset_id, campaign_id, scan_id, "
            "command, started_at, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                operation,
                Status.STARTED,
                device_id,
                asset_id,
                campaign_id,
                scan_id,
                command,
                utcnow(),
                json.dumps(detail) if detail else None,
            ),
        )
        row_id = int(cursor.lastrowid or 0)
        return OperationHandle(id=row_id, operation=operation, _detail=dict(detail or {}))

    def finish(
        self,
        handle: OperationHandle,
        *,
        status: str,
        error: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        self.db.conn.execute(
            "UPDATE operations SET status = ?, finished_at = ?, duration_ms = ?, "
            "retry_count = ?, detail = ?, error = ? WHERE id = ?",
            (
                status,
                utcnow(),
                duration_ms,
                handle.retry_count,
                json.dumps(handle._detail) if handle._detail else None,
                error,
                handle.id,
            ),
        )

    @contextmanager
    def operation(self, operation: str, **kwargs: Any) -> Iterator[OperationHandle]:
        """Journal an operation around a block of work.

        The STARTED row is committed before the block runs. The connection is in
        autocommit mode, so this is durable the moment the INSERT returns.
        """
        handle = self.start(operation, **kwargs)
        began = time.monotonic()
        try:
            yield handle
        except BaseException as exc:
            self.finish(
                handle,
                status=Status.FAILED,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=int((time.monotonic() - began) * 1000),
            )
            raise
        self.finish(
            handle,
            status=Status.COMPLETED,
            duration_ms=int((time.monotonic() - began) * 1000),
        )

    # -- recovery ----------------------------------------------------------

    def pending(self) -> list[dict[str, Any]]:
        """Operations that started and never finished. Evidence of a crash."""
        rows = self.db.conn.execute(
            "SELECT * FROM operations WHERE status = ? ORDER BY started_at", (Status.STARTED,)
        ).fetchall()
        return [dict(r) for r in rows]

    def recent(self, limit: int = 20, *, operation: str | None = None) -> list[dict[str, Any]]:
        if operation:
            rows = self.db.conn.execute(
                "SELECT * FROM operations WHERE operation = ? ORDER BY id DESC LIMIT ?",
                (operation, limit),
            ).fetchall()
        else:
            rows = self.db.conn.execute(
                "SELECT * FROM operations ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
