"""A removal plan may use a scan finished minutes ago; anything else scans again."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from iphone_image.cli.main import _last_scan_size, _recent_scan


def _scan(db, status: str, finished_minutes_ago: float | None, seen: int = 0) -> int:
    db.conn.execute(
        "INSERT OR IGNORE INTO devices (id, name, first_seen_at, last_seen_at, created_at, "
        "updated_at) VALUES (1, 'lib', 'x', 'x', 'x', 'x')"
    )
    finished = (
        None
        if finished_minutes_ago is None
        else (datetime.now(UTC) - timedelta(minutes=finished_minutes_ago)).isoformat()
    )
    cur = db.conn.execute(
        "INSERT INTO scans (device_id, started_at, finished_at, status, assets_seen) "
        "VALUES (1, 'x', ?, ?, ?)",
        (finished, status, seen),
    )
    return int(cur.lastrowid)


def test_a_scan_from_minutes_ago_is_reused(db) -> None:
    sid = _scan(db, "COMPLETED", 3, seen=75854)
    assert _recent_scan(db) == sid
    assert _last_scan_size(db) == 75854


def test_an_old_scan_is_not_reused(db) -> None:
    _scan(db, "COMPLETED", 20)
    assert _recent_scan(db) is None


def test_a_running_or_failed_latest_scan_is_not_reused(db) -> None:
    _scan(db, "COMPLETED", 2, seen=10)
    _scan(db, "RUNNING", None)
    assert _recent_scan(db) is None
    _scan(db, "FAILED", 1)
    assert _recent_scan(db) is None
    assert _last_scan_size(db) == 10


def test_no_scan_at_all(db) -> None:
    assert _recent_scan(db) is None
    assert _last_scan_size(db) == 0
