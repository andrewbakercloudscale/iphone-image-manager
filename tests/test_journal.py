from __future__ import annotations

from pathlib import Path

import pytest

from iphone_image.db.database import Database
from iphone_image.journal import Journal, Op, Status


def test_successful_operation_is_recorded(db: Database) -> None:
    journal = Journal(db)
    with journal.operation(Op.SCAN_STARTED, command="scan") as op:
        op.note(assets=42)
    entry = journal.recent(1)[0]
    assert entry["status"] == Status.COMPLETED
    assert entry["duration_ms"] is not None
    assert '"assets": 42' in entry["detail"]


def test_failure_is_recorded_and_reraised(db: Database) -> None:
    journal = Journal(db)
    with pytest.raises(RuntimeError), journal.operation(Op.REMOVE) as op:
        op.retried()
        raise RuntimeError("device disconnected")
    entry = journal.recent(1)[0]
    assert entry["status"] == Status.FAILED
    assert "device disconnected" in entry["error"]
    assert entry["retry_count"] == 1


def test_clean_run_leaves_nothing_pending(db: Database) -> None:
    journal = Journal(db)
    with journal.operation(Op.VERIFY):
        pass
    assert journal.pending() == []


def test_in_flight_work_survives_a_crash(tmp_path: Path) -> None:
    """The whole point of the journal: a crash must leave evidence."""
    path = tmp_path / "j.sqlite"
    first = Database(path).connect()
    first.migrate()
    Journal(first).start(Op.REMOVE, command="remove-from-iphone --apply", detail={"batch": 9})
    first.close()  # simulates the process dying before finish()

    second = Database(path).connect()
    pending = Journal(second).pending()
    second.close()

    assert len(pending) == 1
    assert pending[0]["operation"] == Op.REMOVE
    assert '"batch": 9' in pending[0]["detail"]


def test_recent_can_filter_by_operation(db: Database) -> None:
    journal = Journal(db)
    for name in (Op.SCAN_STARTED, Op.VERIFY, Op.SCAN_STARTED):
        with journal.operation(name):
            pass
    assert len(journal.recent(operation=Op.SCAN_STARTED)) == 2
    assert len(journal.recent()) == 3
