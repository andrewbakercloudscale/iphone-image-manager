"""Re-filing the archive after the root or the pattern changes."""

from __future__ import annotations

from pathlib import Path

import pytest

from iphone_image.config import Config
from iphone_image.db.database import Database, utcnow
from iphone_image.relocate import plan, prune_empty_directories, run


def _config(tmp_path: Path, pattern: str, root: str = "archive") -> Config:
    return Config.model_validate(
        {
            "archive": {"local_path": str(tmp_path / root)},
            "database": {"path": str(tmp_path / "db.sqlite")},
            "organization": {"pattern": pattern},
            "logging": {"path": str(tmp_path / "logs")},
        }
    )


def _asset(db: Database, path: Path, **overrides: object) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 16)
    values: dict[str, object] = {
        "device_id": 1,
        "identity_key": overrides.pop("identity_key", path.name),
        "filename": path.name,
        "media_type": "PHOTO",
        "created_at_device": "2019-03-04T10:00:00+00:00",
        "size_bytes": 16,
        "source_bundle_id": None,
        "subtypes": "[]",
        "local_path": str(path),
        "local_status": "LOCAL_VERIFIED",
        "present_on_phone": 1,
        "first_seen_at": utcnow(),
        "last_seen_at": utcnow(),
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    values.update(overrides)
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    cursor = db.conn.execute(
        f"INSERT INTO assets ({columns}) VALUES ({placeholders})", list(values.values())
    )
    return int(cursor.lastrowid or 0)


@pytest.fixture
def ledger(tmp_path: Path) -> Database:
    db = Database(tmp_path / "db.sqlite").connect()
    db.migrate()
    db.conn.execute(
        "INSERT INTO devices (id, udid, name, product_kind, first_seen_at, last_seen_at, "
        "created_at, updated_at) VALUES (1, 'lib', 'lib', 'PhotosLibrary', ?, ?, ?, ?)",
        (utcnow(), utcnow(), utcnow(), utcnow()),
    )
    yield db
    db.close()


def test_a_move_renames_the_file_and_rewrites_the_ledger(tmp_path: Path, ledger: Database) -> None:
    old = tmp_path / "archive" / "2019" / "03" / "IMG_1234.JPG"
    asset_id = _asset(ledger, old)
    config = _config(tmp_path, "{source}/{year}/{month}")

    result = run(config, db=ledger)

    new = tmp_path / "archive" / "camera" / "2019" / "03" / "IMG_1234.JPG"
    assert result.moved == 1
    assert new.exists() and not old.exists()
    recorded = ledger.conn.execute(
        "SELECT local_path FROM assets WHERE id = ?", (asset_id,)
    ).fetchone()["local_path"]
    assert recorded == str(new), "the ledger must point at the file, not where it used to be"


def test_a_new_root_is_just_another_move(tmp_path: Path, ledger: Database) -> None:
    old = tmp_path / "archive" / "2019" / "03" / "IMG_1234.JPG"
    _asset(ledger, old)
    config = _config(tmp_path, "{source}/{year}/{month}", root="elsewhere")

    result = run(config, db=ledger)

    assert result.moved == 1
    assert (tmp_path / "elsewhere" / "camera" / "2019" / "03" / "IMG_1234.JPG").exists()


def test_running_twice_moves_nothing_the_second_time(tmp_path: Path, ledger: Database) -> None:
    _asset(ledger, tmp_path / "archive" / "2019" / "03" / "IMG_1234.JPG")
    config = _config(tmp_path, "{source}/{year}/{month}")

    assert run(config, db=ledger).moved == 1
    again = run(config, db=ledger)
    assert again.moved == 0
    assert again.already_in_place == 1


def test_two_assets_that_collide_only_after_refiling_get_distinct_names(
    tmp_path: Path, ledger: Database
) -> None:
    """Both are IMG_1.JPG in different months, and both land in camera/2019.

    Planned against the filesystem alone the second would be handed the same
    free-looking name as the first, because the first has not moved yet.
    """
    _asset(
        ledger,
        tmp_path / "archive" / "2019" / "03" / "IMG_1.JPG",
        identity_key="a",
        sha256="aaaaaaaaaaaaaaaa",
        created_at_device="2019-03-04T10:00:00+00:00",
    )
    _asset(
        ledger,
        tmp_path / "archive" / "2019" / "07" / "IMG_1.JPG",
        identity_key="b",
        sha256="bbbbbbbbbbbbbbbb",
        created_at_device="2019-07-04T10:00:00+00:00",
    )
    config = _config(tmp_path, "{source}/{year}")

    result = run(config, db=ledger)

    assert result.moved == 2
    assert result.failed == 0
    landed = sorted(p.name for p in (tmp_path / "archive" / "camera" / "2019").iterdir())
    assert len(landed) == 2, landed
    assert len(set(landed)) == 2, "two different files must not be given one name"
    paths = {
        r["local_path"] for r in ledger.conn.execute("SELECT local_path FROM assets").fetchall()
    }
    assert len(paths) == 2
    assert all(Path(p).exists() for p in paths)


def test_a_recorded_file_that_is_gone_is_reported_not_invented(
    tmp_path: Path, ledger: Database
) -> None:
    missing = tmp_path / "archive" / "2019" / "03" / "IMG_1234.JPG"
    _asset(ledger, missing)
    missing.unlink()
    config = _config(tmp_path, "{source}/{year}/{month}")

    result = run(config, db=ledger)

    assert result.missing == 1
    assert result.moved == 0
    assert result.failed == 0


def test_the_plan_moves_nothing(tmp_path: Path, ledger: Database) -> None:
    old = tmp_path / "archive" / "2019" / "03" / "IMG_1234.JPG"
    _asset(ledger, old)
    config = _config(tmp_path, "{source}/{year}/{month}")

    moves, preview = plan(config, ledger)

    assert preview.planned == 1
    assert old.exists(), "planning must not touch the disk"
    assert moves[0].destination.name == "IMG_1234.JPG"


def test_whatsapp_is_filed_by_its_bundle_id(tmp_path: Path, ledger: Database) -> None:
    _asset(
        ledger,
        tmp_path / "archive" / "2024" / "11" / "IMG-20241103-WA0007.jpg",
        source_bundle_id="net.whatsapp.WhatsApp",
        created_at_device="2024-11-03T10:00:00+00:00",
    )
    config = _config(tmp_path, "{source}/{year}/{month}")

    assert run(config, db=ledger).moved == 1
    assert (tmp_path / "archive" / "whatsapp" / "2024" / "11" / "IMG-20241103-WA0007.jpg").exists()


def test_emptied_folders_go_but_a_folder_with_anything_in_it_stays(tmp_path: Path) -> None:
    emptied = tmp_path / "archive" / "2019" / "03"
    emptied.mkdir(parents=True)
    occupied = tmp_path / "archive" / "2020" / "05"
    occupied.mkdir(parents=True)
    (occupied / "something.txt").write_text("not ours")

    removed = prune_empty_directories({emptied, occupied}, keep=tmp_path / "archive")

    assert not emptied.exists()
    assert not emptied.parent.exists(), "the year folder emptied by its month going must go too"
    assert occupied.exists() and (occupied / "something.txt").exists()
    assert (tmp_path / "archive").exists(), "the archive root itself is kept"
    assert removed == 2


def test_prune_never_climbs_out_of_the_archive_into_the_home_folder(tmp_path: Path) -> None:
    """rmdir only removes empty folders, but the walk upward still needs a floor."""
    deep = tmp_path / "archive" / "2019"
    deep.mkdir(parents=True)
    prune_empty_directories({deep}, keep=tmp_path / "archive")
    assert tmp_path.exists()


def test_a_mover_never_takes_the_path_of_an_asset_that_is_staying(
    tmp_path: Path, ledger: Database
) -> None:
    """The bug that destroyed 36 files, 91 MB, on a real archive.

    Every asset holding a path was treated as about to vacate it, including the
    ones already where they belong. A mover was therefore told a stayer's path
    would be free, os.rename overwrote the file on it, and the ledger was left
    with two assets pointing at one file and no error anywhere.
    """
    config = _config(tmp_path, "{source}/{year}")
    # A is already exactly where the pattern puts it.
    stayer = tmp_path / "archive" / "camera" / "2020" / "IMG_1.JPG"
    _asset(
        ledger,
        stayer,
        identity_key="stayer",
        sha256="a" * 64,
        created_at_device="2020-05-04T10:00:00+00:00",
        source_bundle_id="com.apple.camera",
    )
    stayer.write_bytes(b"the file that must survive")

    # B is not, and wants the same name in the same folder.
    mover = tmp_path / "archive" / "camera" / "2020" / "03" / "IMG_1.JPG"
    _asset(
        ledger,
        mover,
        identity_key="mover",
        sha256="b" * 64,
        created_at_device="2020-03-04T10:00:00+00:00",
        source_bundle_id="com.apple.camera",
    )
    mover.write_bytes(b"the file that must move")

    result = run(config, db=ledger)

    assert result.failed == 0
    assert stayer.exists(), "the stayer's file was destroyed"
    assert stayer.read_bytes() == b"the file that must survive"

    paths = [
        r["local_path"]
        for r in ledger.conn.execute(
            "SELECT local_path FROM assets ORDER BY identity_key"
        ).fetchall()
    ]
    assert len(set(paths)) == 2, f"two assets share one path: {paths}"
    assert all(Path(p).exists() for p in paths)
    contents = {Path(p).read_bytes() for p in paths}
    assert contents == {b"the file that must survive", b"the file that must move"}
