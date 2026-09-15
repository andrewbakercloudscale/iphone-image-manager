from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from iphone_image import sync as sync_engine
from iphone_image.config import Config
from iphone_image.db.database import Database, utcnow
from iphone_image.selector import Selector

MB = 1024**2


class FakeHelper:
    """Stands in for the Swift helper, so every path is testable without hardware."""

    def __init__(
        self,
        *,
        sizes: dict[str, int] | None = None,
        fail: set[str] | None = None,
        short: set[str] | None = None,
    ) -> None:
        self.sizes = sizes or {}
        self.fail = fail or set()
        self.short = short or set()
        self.requested: list[str] = []

    def check(self) -> None:
        return None

    def export(self, work: list[tuple[str, Path]], **_: Any) -> Any:
        for identifier, destination in work:
            self.requested.append(identifier)
            if identifier in self.fail:
                yield {
                    "event": "exported",
                    "localIdentifier": identifier,
                    "error": "network went away",
                    "code": "TRANSFER",
                }
                continue
            size = self.sizes.get(identifier, 1024)
            if identifier in self.short:
                size = max(1, size // 2)  # a truncated transfer
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"x" * size)
            yield {
                "event": "exported",
                "localIdentifier": identifier,
                "bytes": size,
                "seconds": 0.5,
            }
        yield {"event": "exportComplete", "written": len(work)}


@pytest.fixture
def env(tmp_path: Path):
    config = Config(
        archive={"local_path": str(tmp_path / "archive")},
        database={"path": str(tmp_path / "db.sqlite")},
        logging={"path": str(tmp_path / "logs")},
        photos={"library_path": str(tmp_path / "lib.photoslibrary")},
        chunking={"chunk_bytes": 10 * MB, "free_space_floor": 0},
    )
    db = Database(config.database.path).connect()
    db.migrate()
    db.conn.execute(
        "INSERT INTO devices (id, udid, name, first_seen_at, last_seen_at, "
        "created_at, updated_at) VALUES (1, 'lib', 'lib', ?, ?, ?, ?)",
        (utcnow(), utcnow(), utcnow(), utcnow()),
    )
    return config, db


def add_asset(
    db: Database,
    key: str,
    *,
    size: int,
    created: str = "2021-07-04T10:00:00+00:00",
    media_type: str = "PHOTO",
    source: str | None = "com.apple.camera",
    filename: str | None = None,
    favourite: int = 0,
) -> None:
    db.conn.execute(
        "INSERT INTO assets (device_id, identity_key, filename, media_type, "
        "created_at_device, size_bytes, source_bundle_id, present_on_phone, "
        "is_favourite, subtypes, first_seen_at, last_seen_at, created_at, updated_at) "
        "VALUES (1, ?, ?, ?, ?, ?, ?, 1, ?, '[]', ?, ?, ?, ?)",
        (
            key,
            filename or f"{key}.JPG",
            media_type,
            created,
            size,
            source,
            favourite,
            utcnow(),
            utcnow(),
            utcnow(),
            utcnow(),
        ),
    )


# -- budgets -----------------------------------------------------------------


def test_the_budget_is_respected(env) -> None:
    config, db = env
    for i in range(10):
        add_asset(db, f"a{i}", size=3 * MB)
    helper = FakeHelper(sizes={f"a{i}": 3 * MB for i in range(10)})
    result = sync_engine.run(config, Selector(), budget_bytes=10 * MB, helper=helper)
    assert result.fetched == 3, "a fourth asset would have exceeded the budget"
    assert result.bytes_fetched <= 10 * MB


def test_video_is_not_fetched_unless_asked_for(env) -> None:
    """Video is most of the bytes in a real library, so it never arrives by default."""
    config, db = env
    add_asset(db, "photo", size=MB, media_type="PHOTO")
    add_asset(db, "video", size=MB, media_type="VIDEO")
    helper = FakeHelper(sizes={"photo": MB, "video": MB})
    sync_engine.run(config, Selector(media_type="photo"), helper=helper)
    assert helper.requested == ["photo"]


# -- verification ------------------------------------------------------------


def test_a_short_transfer_is_rejected_not_archived(env) -> None:
    """The failure that matters: a truncated file hashed and called a backup."""
    config, db = env
    add_asset(db, "a", size=4 * MB)
    helper = FakeHelper(sizes={"a": 4 * MB}, short={"a"})
    result = sync_engine.run(config, Selector(), helper=helper)
    assert result.fetched == 0 and result.failed == 1
    assert "size mismatch" in result.failures[0]["error"]
    assert list(config.archive.local_path.rglob("*")) == [] or not any(
        p.is_file() for p in config.archive.local_path.rglob("*")
    )


def test_no_partial_files_are_left_behind(env) -> None:
    config, db = env
    add_asset(db, "ok", size=MB)
    add_asset(db, "bad", size=MB)
    helper = FakeHelper(sizes={"ok": MB, "bad": MB}, fail={"bad"})
    sync_engine.run(config, Selector(), helper=helper)
    assert list(config.archive.local_path.rglob("*.partial")) == []


def test_a_verified_asset_records_its_hash_and_path(env) -> None:
    config, db = env
    add_asset(db, "a", size=MB)
    sync_engine.run(config, Selector(), helper=FakeHelper(sizes={"a": MB}))
    row = db.conn.execute(
        "SELECT local_status, sha256, local_path FROM assets WHERE identity_key = 'a'"
    ).fetchone()
    assert row["local_status"] == "LOCAL_VERIFIED"
    assert len(row["sha256"]) == 64
    assert Path(row["local_path"]).is_file()


def test_a_failure_marks_the_asset_not_the_run(env) -> None:
    config, db = env
    add_asset(db, "a", size=MB)
    result = sync_engine.run(config, Selector(), helper=FakeHelper(fail={"a"}))
    assert result.failed == 1
    row = db.conn.execute("SELECT local_status FROM assets WHERE identity_key = 'a'").fetchone()
    assert row["local_status"] == "FAILED"


# -- resume and idempotence --------------------------------------------------


def test_a_second_run_does_not_refetch(env) -> None:
    config, db = env
    add_asset(db, "a", size=MB)
    helper = FakeHelper(sizes={"a": MB})
    sync_engine.run(config, Selector(), helper=helper)
    sync_engine.run(config, Selector(), helper=helper)
    assert helper.requested == ["a"], "the asset was fetched twice"


def test_a_second_run_continues_with_the_next_assets(env) -> None:
    config, db = env
    for i in range(6):
        add_asset(db, f"a{i}", size=3 * MB)
    helper = FakeHelper(sizes={f"a{i}": 3 * MB for i in range(6)})
    first = sync_engine.run(config, Selector(), budget_bytes=6 * MB, helper=helper)
    second = sync_engine.run(config, Selector(), budget_bytes=6 * MB, helper=helper)
    assert first.fetched == 2 and second.fetched == 2
    assert len(set(helper.requested)) == 4, "the same asset was fetched twice"


def test_a_missing_archive_file_is_fetched_again(env) -> None:
    """Spec section 9: sync must identify missing local files.

    Without this a row marked verified is excluded from every future chunk, so
    the ledger would keep claiming a backup that no longer exists.
    """
    config, db = env
    add_asset(db, "a", size=MB)
    helper = FakeHelper(sizes={"a": MB})
    sync_engine.run(config, Selector(), helper=helper)
    archived = Path(
        db.conn.execute("SELECT local_path FROM assets WHERE identity_key='a'").fetchone()[0]
    )
    archived.unlink()

    result = sync_engine.run(config, Selector(), helper=helper)
    assert result.missing_recovered == 1
    assert result.fetched == 1
    assert archived.is_file()


# -- layout ------------------------------------------------------------------


def test_assets_land_in_the_configured_pattern(env) -> None:
    config, db = env
    add_asset(db, "a", size=MB, created="2019-11-23T09:00:00+00:00")
    sync_engine.run(config, Selector(), helper=FakeHelper(sizes={"a": MB}))
    assert (config.archive.local_path / "2019" / "11" / "a.JPG").is_file()


def test_an_asset_with_no_date_still_lands_somewhere_safe(env) -> None:
    config, db = env
    db.conn.execute(
        "INSERT INTO assets (device_id, identity_key, filename, media_type, size_bytes, "
        "source_bundle_id, present_on_phone, subtypes, first_seen_at, last_seen_at, "
        "created_at, updated_at) VALUES (1, 'a', 'a.JPG', 'PHOTO', ?, "
        "'com.apple.camera', 1, '[]', ?, ?, ?, ?)",
        (MB, utcnow(), utcnow(), utcnow(), utcnow()),
    )
    sync_engine.run(config, Selector(), helper=FakeHelper(sizes={"a": MB}))
    assert (config.archive.local_path / "Unknown Date" / "a.JPG").is_file()


# -- storage safety ----------------------------------------------------------


def test_a_chunk_that_would_breach_the_floor_refuses_to_start(env, monkeypatch) -> None:
    """Refuse up front rather than filling the disk part way through."""
    config, db = env
    config.chunking.free_space_floor = 100 * MB
    add_asset(db, "a", size=MB)
    monkeypatch.setattr(sync_engine, "free_bytes", lambda _p: 50 * MB)
    with pytest.raises(sync_engine.SyncError, match="floor"):
        sync_engine.run(config, Selector(), helper=FakeHelper(sizes={"a": MB}))


def test_nothing_is_written_when_the_floor_would_be_breached(env, monkeypatch) -> None:
    config, db = env
    config.chunking.free_space_floor = 100 * MB
    add_asset(db, "a", size=MB)
    monkeypatch.setattr(sync_engine, "free_bytes", lambda _p: 50 * MB)
    with pytest.raises(sync_engine.SyncError):
        sync_engine.run(config, Selector(), helper=FakeHelper(sizes={"a": MB}))
    assert not any(p.is_file() for p in config.archive.local_path.rglob("*"))


# -- journalling -------------------------------------------------------------


def test_the_run_is_journalled(env) -> None:
    config, db = env
    add_asset(db, "a", size=MB)
    sync_engine.run(config, Selector(), helper=FakeHelper(sizes={"a": MB}))
    row = db.conn.execute(
        "SELECT operation, status, detail FROM operations ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["status"] == "COMPLETED"
    assert json.loads(row["detail"])["fetched"] == 1


# -- estimates ---------------------------------------------------------------


def test_the_rate_estimate_uses_this_installs_own_history(env) -> None:
    """A hardcoded constant was wrong by nearly three times within a day."""
    _config, db = env
    db.conn.execute(
        "INSERT INTO operations (operation, status, started_at, duration_ms, detail) "
        "VALUES ('DOWNLOAD', 'COMPLETED', ?, 10000, ?)",
        (utcnow(), json.dumps({"networkBytes": 50 * MB, "networkSeconds": 10.0})),
    )
    rate = sync_engine.observed_rate(db)
    assert rate is not None
    assert 4.9 < rate < 5.1, "50 MB downloaded in 10 s is 5 MB/s"


def test_tiny_runs_do_not_pollute_the_estimate(env) -> None:
    """Three small local files say nothing about network throughput."""
    _config, db = env
    db.conn.execute(
        "INSERT INTO operations (operation, status, started_at, duration_ms, detail) "
        "VALUES ('DOWNLOAD', 'COMPLETED', ?, 10, ?)",
        (utcnow(), json.dumps({"networkBytes": 1024, "networkSeconds": 0.01})),
    )
    assert sync_engine.observed_rate(db) is None


def test_with_no_history_the_estimate_falls_back(env) -> None:
    _config, db = env
    assert sync_engine.observed_rate(db) is None
    hours = sync_engine.estimate_hours(int(3600 * 1_048_576 * sync_engine.FALLBACK_MB_S), None)
    assert 0.99 < hours < 1.01


def test_local_disk_reads_are_excluded_from_the_rate(env) -> None:
    """A 500 MB chunk of already-resident assets read in seconds produced
    3.77 MB/s where the real network rate was 1.24, understating every
    estimate that used it."""
    _config, db = env
    db.conn.execute(
        "INSERT INTO operations (operation, status, started_at, duration_ms, detail) "
        "VALUES ('DOWNLOAD', 'COMPLETED', ?, 2000, ?)",
        (
            utcnow(),
            json.dumps(
                {
                    "bytes": 500 * MB,
                    "transferSeconds": 0.5,
                    "networkBytes": 0,
                    "networkSeconds": 0.0,
                    "localCount": 6170,
                }
            ),
        ),
    )
    assert sync_engine.observed_rate(db) is None, "a disk read was counted as a download"


def test_a_mixed_history_reports_only_the_download_rate(env) -> None:
    _config, db = env
    for detail in (
        # a chunk that was entirely local
        {"networkBytes": 0, "networkSeconds": 0.0, "localBytes": 500 * MB, "localCount": 900},
        # a real download, 1 MB/s
        {"networkBytes": 200 * MB, "networkSeconds": 200.0},
    ):
        db.conn.execute(
            "INSERT INTO operations (operation, status, started_at, duration_ms, detail) "
            "VALUES ('DOWNLOAD', 'COMPLETED', ?, 1000, ?)",
            (utcnow(), json.dumps(detail)),
        )
    rate = sync_engine.observed_rate(db)
    assert rate is not None and 0.9 < rate < 1.1


def test_one_chunk_that_mixed_both_reports_the_download_rate_not_the_blend(env) -> None:
    """The failure this split exists for, and it was live when it was found.

    A chunk fetched mostly-resident 2023 photos and then reached genuinely
    remote ones. Its aggregate read 10.12 MB/s and was still falling, while the
    instantaneous network rate was 2.6. The run-level threshold of 25 MB/s only
    ever caught a chunk that was *entirely* local: a mixed one sails under it
    and is recorded as though every byte came over the network, which would
    have predicted the next all-iCloud chunk at 0.4 hours instead of 2.8.
    """
    _config, db = env
    db.conn.execute(
        "INSERT INTO operations (operation, status, started_at, duration_ms, detail) "
        "VALUES ('DOWNLOAD', 'COMPLETED', ?, 1000, ?)",
        (
            utcnow(),
            json.dumps(
                {
                    # The real shape, from the live run: 6.8 GB came off local
                    # disk in about no time, 2.4 GB was downloaded over 930 s.
                    # The blend is 9.2 GB / 930 s = 10.1 MB/s, which sits under
                    # the old 25 MB/s rule and so was taken for a download rate.
                    # The download rate was 2.6.
                    "bytes": 9416 * MB,
                    "transferSeconds": 930.0,
                    "networkBytes": 2400 * MB,
                    "networkSeconds": 930.0,
                    "localBytes": 7016 * MB,
                    "localCount": 2700,
                }
            ),
        ),
    )
    rate = sync_engine.observed_rate(db)
    assert rate is not None
    assert 2.5 < rate < 2.7, f"the blend is 10.1 MB/s; the download rate is 2.6, got {rate}"


def test_a_legacy_row_is_ignored_rather_than_approximated(env) -> None:
    """Its bytes cover both kinds and the proportion cannot be recovered."""
    _config, db = env
    db.conn.execute(
        "INSERT INTO operations (operation, status, started_at, duration_ms, detail) "
        "VALUES ('DOWNLOAD', 'COMPLETED', ?, 1000, ?)",
        (utcnow(), json.dumps({"bytes": 16 * 1024**3, "transferSeconds": 1600.0})),
    )
    assert sync_engine.observed_rate(db) is None


def test_a_whole_chunk_of_local_reads_does_not_report_a_network_rate(env) -> None:
    """A real run reported "4696.44 MB/s", which is a disk read wearing a
    network rate's clothes."""
    result = sync_engine.SyncResult(
        fetched=6170, bytes_fetched=15 * 1024**3, seconds=3.4, local_count=6170
    )
    assert "no download" in result.rate_text
    assert "MB/s" not in result.rate_text


def test_a_genuine_download_still_reports_its_rate() -> None:
    result = sync_engine.SyncResult(
        fetched=61,
        bytes_fetched=198 * MB,
        seconds=160.0,
        network_bytes=198 * MB,
        network_seconds=160.0,
    )
    assert "MB/s" in result.rate_text


def test_a_mixed_chunk_shows_the_download_rate_and_says_how_many_were_local() -> None:
    result = sync_engine.SyncResult(
        fetched=2781,
        bytes_fetched=9 * 1024**3 + 200 * MB,
        seconds=203.0,
        network_bytes=200 * MB,
        network_seconds=200.0,
        local_bytes=9 * 1024**3,
        local_count=2700,
    )
    assert result.rate_text.startswith("1.00 MB/s")
    assert "2,700 of 2,781 were already local" in result.rate_text


def test_leftover_partials_from_a_kill_are_swept(env) -> None:
    """A hard kill leaves the in-flight .partial behind. Left alone they
    accumulate one per interruption and quietly consume disk."""
    config, db = env
    stale = config.archive.local_path / "2020" / "01" / "IMG_1.JPG.partial"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"x" * 4096)
    add_asset(db, "a", size=MB)

    result = sync_engine.run(config, Selector(), helper=FakeHelper(sizes={"a": MB}))
    assert result.partials_swept == 1
    assert not stale.exists()


def test_sweeping_never_touches_a_real_archive_file(env) -> None:
    config, _db = env
    keeper = config.archive.local_path / "2020" / "01" / "IMG_1.JPG"
    keeper.parent.mkdir(parents=True)
    keeper.write_bytes(b"real")
    count, _ = sync_engine.sweep_partials(config.archive.local_path)
    assert count == 0
    assert keeper.read_bytes() == b"real"
