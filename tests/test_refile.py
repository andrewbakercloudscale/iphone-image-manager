"""Re-filing cloud copies: the ledger follows the remote, never leads it."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from iphone_image import refile as refile_engine
from iphone_image.config import Config
from iphone_image.db.database import Database, utcnow

OLD = "Family Photos/Archive/WhatsApp Media"
NEW = "Family Photos/WhatsApp Media"


class FakeRemote:
    """Files by full path; `move` can be told to lose some on the way."""

    def __init__(self, files: dict[str, str], lose: set[str] | None = None) -> None:
        self.files = files
        self.lose = lose or set()

    def move(self, source: str, target: str, names: list[str]) -> None:
        for name in names:
            digest = self.files.pop(f"{source}/{name}", None)
            if digest is not None and name not in self.lose:
                self.files[f"{target}/{name}"] = digest

    def hashes(self, destination: str) -> dict[str, str]:
        prefix = destination + "/"
        return {p[len(prefix) :]: h for p, h in self.files.items() if p.startswith(prefix)}


def make_env(
    tmp_path: Path, *, pattern: str = "{source}/{year}/{month}"
) -> tuple[Config, Database]:
    config = Config(
        archive={"local_path": str(tmp_path / "archive")},
        database={"path": str(tmp_path / "db.sqlite")},
        logging={"path": str(tmp_path / "logs")},
        photos={"library_path": str(tmp_path / "lib.photoslibrary")},
        organization={
            "pattern": "{source}/{year}/{event}",
            "channel_patterns": {"whatsapp": pattern},
        },
        cloud={
            "enabled": True,
            "provider": "google_drive",
            "remote": "gdrive",
            "destination": "Family Photos/Archive",
            "whatsapp_destination": NEW,
        },
    )
    db = Database(config.database.path).connect()
    db.migrate()
    db.conn.execute(
        "INSERT INTO devices (id, udid, name, first_seen_at, last_seen_at, created_at, updated_at) "
        "VALUES (1, 'lib', 'lib', ?, ?, ?, ?)",
        (utcnow(), utcnow(), utcnow(), utcnow()),
    )
    return config, db


def whatsapp(db: Database, key: str, name: str, when: str, *, local: str | None = None) -> str:
    digest = key * 8
    db.conn.execute(
        "INSERT INTO assets (device_id, identity_key, filename, media_type, created_at_device, "
        "source_bundle_id, local_path, local_status, sha256, cloud_status, cloud_path, "
        "archive_claim, first_seen_at, last_seen_at, created_at, updated_at) "
        "VALUES (1, ?, ?, 'PHOTO', ?, 'net.whatsapp.WhatsApp', ?, ?, ?, 'CLOUD_VERIFIED', ?, ?, "
        "?, ?, ?, ?)",
        (
            key,
            name,
            when,
            local,
            "LOCAL_VERIFIED" if local else "RELEASED",
            digest,
            f"{OLD}/{name}",
            f"whatsapp/{name}",
            utcnow(),
            utcnow(),
            utcnow(),
            utcnow(),
        ),
    )
    return digest


def test_moves_into_year_month_and_updates_the_ledger(tmp_path: Path) -> None:
    config, db = make_env(tmp_path)
    a = whatsapp(db, "a", "a.jpg", "2025-03-04T10:00:00+00:00")
    b = whatsapp(db, "b", "b.jpg", "2024-12-31T10:00:00+00:00")
    plan = refile_engine.plan(config, db, ["whatsapp"])
    assert {m.new for m in plan.moves} == {f"{NEW}/2025/03/a.jpg", f"{NEW}/2024/12/b.jpg"}

    remote = FakeRemote({f"{OLD}/a.jpg": a, f"{OLD}/b.jpg": b})
    result = refile_engine.run(config, db, plan.moves, provider=remote)
    assert result.moved == 2 and result.failures == []
    row = db.conn.execute(
        "SELECT cloud_path, archive_claim FROM assets WHERE identity_key='a'"
    ).fetchone()
    assert tuple(row) == (f"{NEW}/2025/03/a.jpg", "whatsapp/2025/03/a.jpg")
    assert refile_engine.plan(config, db, ["whatsapp"]).already == 2


def test_a_file_missing_after_the_move_keeps_its_old_path(tmp_path: Path) -> None:
    config, db = make_env(tmp_path)
    a = whatsapp(db, "a", "a.jpg", "2025-03-04T10:00:00+00:00")
    plan = refile_engine.plan(config, db, ["whatsapp"])
    remote = FakeRemote({f"{OLD}/a.jpg": a}, lose={"a.jpg"})
    result = refile_engine.run(config, db, plan.moves, provider=remote)
    assert result.moved == 0 and len(result.failures) == 1
    path = db.conn.execute("SELECT cloud_path FROM assets").fetchone()[0]
    assert path == f"{OLD}/a.jpg"


def test_an_interrupted_run_is_finished_by_running_again(tmp_path: Path) -> None:
    config, db = make_env(tmp_path)
    a = whatsapp(db, "a", "a.jpg", "2025-03-04T10:00:00+00:00")
    # Moved on the remote by an earlier run that died before the ledger write.
    remote = FakeRemote({f"{NEW}/2025/03/a.jpg": a})
    plan = refile_engine.plan(config, db, ["whatsapp"])
    assert refile_engine.run(config, db, plan.moves, provider=remote).moved == 1


def test_refuses_place_layouts_and_files_still_on_the_mac(tmp_path: Path) -> None:
    config, db = make_env(tmp_path, pattern="{source}/{year}/{event}")
    whatsapp(db, "a", "a.jpg", "2025-03-04T10:00:00+00:00")
    plan = refile_engine.plan(config, db, ["whatsapp"])
    assert plan.moves == [] and sum(plan.skipped.values()) == 1

    config, db = make_env(tmp_path / "two")
    local = tmp_path / "a.jpg"
    local.write_bytes(b"x")
    whatsapp(db, "a", "a.jpg", "2025-03-04T10:00:00+00:00", local=str(local))
    plan = refile_engine.plan(config, db, ["whatsapp"])
    assert plan.moves == [] and "release it first" in next(iter(plan.skipped))


@pytest.mark.parametrize("bad", ["{year}/{month}", "{source}/{nope}"])
def test_a_source_pattern_must_start_with_the_source(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        make_env(tmp_path, pattern=bad)


def test_moves_are_grouped_by_folder(tmp_path: Path) -> None:
    config, db = make_env(tmp_path)
    whatsapp(db, "a", "a.jpg", "2025-03-04T10:00:00+00:00")
    whatsapp(db, "b", "b.jpg", "2025-03-05T10:00:00+00:00")
    plan = refile_engine.plan(config, db, ["whatsapp"])
    assert {m.folders for m in plan.moves} == {(OLD, str(PurePosixPath(NEW) / "2025/03"))}
