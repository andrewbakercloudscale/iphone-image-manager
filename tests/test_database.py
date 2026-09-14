from __future__ import annotations

from pathlib import Path

import pytest

from iphone_image.db.database import Database, DatabaseError, discover_migrations

EXPECTED_TABLES = {
    "asset_groups",
    "asset_resources",
    "assets",
    "campaign_assets",
    "campaigns",
    "classifications",
    "cloud_objects",
    "deletion_events",
    "devices",
    "duplicate_groups",
    "locations",
    "operations",
    "recycle_bin_entries",
    "scans",
    "settings",
}


def test_migrations_are_contiguous() -> None:
    versions = [v for v, _, _ in discover_migrations()]
    assert versions == list(range(1, len(versions) + 1))


def test_migrate_creates_every_table(db: Database) -> None:
    assert set(db.counts()) == EXPECTED_TABLES


def test_migrate_is_idempotent(db: Database) -> None:
    assert db.migrate() == [], "a second migrate must do nothing"


def test_pragmas_are_set(db: Database) -> None:
    assert db.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_fresh_database_is_clean(db: Database) -> None:
    assert db.integrity_check() == []
    assert all(n == 0 for n in db.counts().values())


def test_settings_round_trip(db: Database) -> None:
    assert db.get_setting("absent", "fallback") == "fallback"
    db.set_setting("k", "v")
    assert db.get_setting("k") == "v"
    db.set_setting("k", "v2")
    assert db.get_setting("k") == "v2"


def test_a_newer_schema_is_refused(db: Database) -> None:
    """Never let an old build alter a database it does not understand."""
    db.conn.execute("PRAGMA user_version = 9999")
    with pytest.raises(DatabaseError, match="only knows up to"):
        db.migrate()


def test_a_failed_migration_leaves_nothing_behind(tmp_path: Path) -> None:
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "0001_broken.sql").write_text(
        "CREATE TABLE t (id INTEGER);\nCREATE TABLE t (id INTEGER);\n"
    )
    database = Database(tmp_path / "b.sqlite").connect()
    with pytest.raises(DatabaseError):
        database.migrate(directory=bad)
    assert database.schema_version == 0
    assert database.counts() == {}
    database.close()


def test_gapped_migration_numbering_is_fatal(tmp_path: Path) -> None:
    d = tmp_path / "gap"
    d.mkdir()
    (d / "0001_a.sql").write_text("CREATE TABLE a (id INTEGER);")
    (d / "0003_c.sql").write_text("CREATE TABLE c (id INTEGER);")
    with pytest.raises(DatabaseError, match="gap"):
        discover_migrations(d)


def test_unparseable_migration_name_is_fatal(tmp_path: Path) -> None:
    d = tmp_path / "odd"
    d.mkdir()
    (d / "initial.sql").write_text("CREATE TABLE a (id INTEGER);")
    with pytest.raises(DatabaseError):
        discover_migrations(d)


def test_transaction_rolls_back(db: Database) -> None:
    with pytest.raises(RuntimeError), db.transaction() as conn:
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES ('x', 'y', '2026-01-01')"
        )
        raise RuntimeError("boom")
    assert db.get_setting("x") is None


def test_foreign_keys_are_enforced(db: Database) -> None:
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute(
            "INSERT INTO scans (device_id, started_at, status) "
            "VALUES (999, '2026-01-01', 'RUNNING')"
        )
