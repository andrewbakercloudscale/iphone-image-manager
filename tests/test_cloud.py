"""Mirroring to the cloud, and refusing to believe it happened."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from iphone_image import cloud as cloud_engine
from iphone_image.config import Config
from iphone_image.db.database import Database, utcnow
from iphone_image.selector import Selector

MB = 1024 * 1024


class FakeCloud:
    """A remote that can be told to lose or corrupt what it was given."""

    name = "fake"

    def __init__(self, *, lose: set[str] | None = None, corrupt: set[str] | None = None) -> None:
        self.stored: dict[str, str] = {}
        self.lose = lose or set()
        self.corrupt = corrupt or set()
        self.checked = False
        self.calls: list[tuple[Path, int, str]] = []

    def check(self) -> None:
        self.checked = True

    def upload(self, root: Path, relatives: list[str], destination: str) -> None:
        self.calls.append((root, len(relatives), destination))
        for relative in relatives:
            if relative in self.lose:
                continue
            data = (root / relative).read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if relative in self.corrupt:
                digest = "0" * 64
            self.stored[relative] = digest

    def hashes(self, destination: str) -> dict[str, str]:
        return dict(self.stored)


@pytest.fixture
def env(tmp_path: Path):
    config = Config(
        archive={"local_path": str(tmp_path / "archive")},
        database={"path": str(tmp_path / "db.sqlite")},
        logging={"path": str(tmp_path / "logs")},
        photos={"library_path": str(tmp_path / "lib.photoslibrary")},
        cloud={
            "enabled": True,
            "provider": "google_drive",
            "remote": "gdrive",
            "destination": "Family Photos/Andrew iPhone Archive",
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


def add_archived(config: Config, db: Database, key: str, relative: str, body: bytes = b"photo"):
    path = config.archive.local_path / "camera" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()
    db.conn.execute(
        "INSERT INTO assets (device_id, identity_key, filename, media_type, created_at_device, "
        "size_bytes, source_bundle_id, present_on_phone, subtypes, local_path, local_status, "
        "sha256, first_seen_at, last_seen_at, created_at, updated_at) "
        "VALUES (1, ?, ?, 'PHOTO', '2019-11-04T10:00:00+00:00', ?, 'com.apple.camera', 1, '[]', "
        "?, 'LOCAL_VERIFIED', ?, ?, ?, ?, ?)",
        (
            key,
            Path(relative).name,
            len(body),
            str(path),
            digest,
            utcnow(),
            utcnow(),
            utcnow(),
            utcnow(),
        ),
    )
    return digest


def test_the_remote_layout_drops_the_channel_level(env) -> None:
    """Locally camera/2019/..., in Drive 2019/... under the destination."""
    config, db = env
    add_archived(config, db, "a", "2019/11-12 Cape Town/IMG_1.JPG")
    uploads = cloud_engine.plan(config, Selector(), db)
    assert [u.relative for u in uploads] == ["2019/11-12 Cape Town/IMG_1.JPG"]


def test_a_verified_upload_is_recorded_with_where_it_went(env) -> None:
    config, db = env
    add_archived(config, db, "a", "2019/11 Cape Town/IMG_1.JPG")
    fake = FakeCloud()

    result = cloud_engine.run(config, Selector(), provider=fake, db=db)

    assert fake.checked, "a provider must be checked before it is trusted"
    assert (result.uploaded, result.verified, result.failed) == (1, 1, 0)
    row = db.conn.execute("SELECT cloud_status, cloud_path FROM assets").fetchone()
    assert row["cloud_status"] == "CLOUD_VERIFIED"
    assert row["cloud_path"] == "Family Photos/Andrew iPhone Archive/2019/11 Cape Town/IMG_1.JPG"


def test_a_file_the_remote_does_not_have_is_never_marked_verified(env) -> None:
    """The failure that would matter: the local copy is released on this record."""
    config, db = env
    add_archived(config, db, "a", "2019/11 Cape Town/IMG_1.JPG")
    fake = FakeCloud(lose={"2019/11 Cape Town/IMG_1.JPG"})

    result = cloud_engine.run(config, Selector(), provider=fake, db=db)

    assert result.verified == 0 and result.failed == 1
    assert "not found" in result.failures[0]["error"]
    # The schema defaults this to 'NONE', so the meaningful assertion is that it
    # is not verified -- 'is None' would have passed for the wrong reason.
    status = db.conn.execute("SELECT cloud_status FROM assets").fetchone()["cloud_status"]
    assert status != "CLOUD_VERIFIED"


def test_a_hash_that_does_not_match_is_a_failure_not_a_pass(env) -> None:
    config, db = env
    add_archived(config, db, "a", "2019/11 Cape Town/IMG_1.JPG")
    fake = FakeCloud(corrupt={"2019/11 Cape Token/IMG_1.JPG"} | {"2019/11 Cape Town/IMG_1.JPG"})

    result = cloud_engine.run(config, Selector(), provider=fake, db=db)

    assert result.verified == 0 and result.failed == 1
    assert "hash mismatch" in result.failures[0]["error"]


def test_an_upload_that_exits_zero_is_not_evidence(env) -> None:
    """rclone returning 0 proves it ran, not that the bytes arrived."""
    config, db = env
    for i in range(3):
        add_archived(config, db, f"a{i}", f"2019/11 Cape Town/IMG_{i}.JPG", body=f"x{i}".encode())
    fake = FakeCloud(lose={"2019/11 Cape Town/IMG_1.JPG"})

    result = cloud_engine.run(config, Selector(), provider=fake, db=db)

    assert result.uploaded == 3, "the transfer reported three"
    assert result.verified == 2, "but only two are actually there"
    assert result.failed == 1


def test_already_verified_assets_are_not_uploaded_again(env) -> None:
    config, db = env
    add_archived(config, db, "a", "2019/11 Cape Town/IMG_1.JPG")
    cloud_engine.run(config, Selector(), provider=FakeCloud(), db=db)

    second = FakeCloud()
    result = cloud_engine.run(config, Selector(), provider=second, db=db)

    assert result.planned == 0
    assert second.calls == [], "a resumed run must not re-send what is verified"


def test_cloud_disabled_refuses_rather_than_doing_nothing_quietly(env) -> None:
    config, db = env
    config = config.model_copy(update={"cloud": config.cloud.model_copy(update={"enabled": False})})
    with pytest.raises(cloud_engine.CloudError, match="enabled"):
        cloud_engine.run(config, Selector(), provider=FakeCloud(), db=db)


def test_an_asset_with_no_hash_is_skipped_rather_than_uploaded_unverifiable(env) -> None:
    config, db = env
    add_archived(config, db, "a", "2019/11 Cape Town/IMG_1.JPG")
    db.conn.execute("UPDATE assets SET sha256 = NULL")
    assert cloud_engine.plan(config, Selector(), db) == []


def test_limit_is_honoured(env) -> None:
    """The CLI offers --limit for every verb; one that ignored it silently
    would be worse than one that did not offer it."""
    config, db = env
    for i in range(5):
        add_archived(config, db, f"a{i}", f"2019/11/IMG_{i}.JPG", body=f"x{i}".encode())
    assert len(cloud_engine.plan(config, Selector(limit=2), db)) == 2
    assert len(cloud_engine.plan(config, Selector(), db)) == 5
