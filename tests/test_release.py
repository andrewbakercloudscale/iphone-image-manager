"""Releasing the Mac copy. The code that deletes the user's only local file."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from iphone_image import release as release_engine
from iphone_image import sync as sync_engine
from iphone_image.config import Config
from iphone_image.db.database import Database, utcnow
from iphone_image.selector import Selector

DEST = "Family Photos/Andrew iPhone Archive"


class FakeCloud:
    """A remote that can be told to have lost or changed what it holds."""

    name = "fake"

    def __init__(self, holds: dict[str, str] | None = None) -> None:
        self.holds = holds or {}
        self.checked = False

    def check(self) -> None:
        self.checked = True

    def upload(
        self, root: Path, relatives: list[str], destination: str
    ) -> None:  # pragma: no cover
        raise AssertionError("release must never upload")

    def hashes(self, destination: str) -> dict[str, str]:
        return dict(self.holds)


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
            "destination": DEST,
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


def archived(config, db, key, relative, *, cloud=True, body=b"photo", on_phone=True):
    """One archived asset.

    `on_phone=False` is the state every fixture here was missing: archived,
    verified, and already deleted from the phone. Release excluded that state
    for months because no test ever produced it -- see
    `Selector.for_release`.
    """
    path = config.archive.local_path / "camera" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()
    db.conn.execute(
        "INSERT INTO assets (device_id, identity_key, filename, media_type, created_at_device, "
        "size_bytes, source_bundle_id, present_on_phone, subtypes, local_path, local_status, "
        "sha256, cloud_status, cloud_path, removed_from_phone_at, "
        "first_seen_at, last_seen_at, created_at, updated_at) "
        "VALUES (1, ?, ?, 'PHOTO', '2019-11-04T10:00:00+00:00', ?, 'com.apple.camera', ?, '[]', "
        "?, 'LOCAL_VERIFIED', ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            key,
            Path(relative).name,
            len(body),
            1 if on_phone else 0,
            str(path),
            digest,
            "CLOUD_VERIFIED" if cloud else "NONE",
            f"{DEST}/{relative}" if cloud else None,
            None if on_phone else utcnow(),
            utcnow(),
            utcnow(),
            utcnow(),
            utcnow(),
        ),
    )
    return path, digest


# -- what it refuses --------------------------------------------------------


def test_an_asset_not_in_the_cloud_is_never_released(env) -> None:
    config, db = env
    path, _ = archived(config, db, "a", "2019/11/IMG_1.JPG", cloud=False)
    eligible, result = release_engine.plan(config, db, Selector(), provider=FakeCloud())
    assert eligible == []
    assert result.blocks == {"not verified in the cloud": 1}
    assert path.exists()


def test_a_ledger_claim_is_not_believed_without_asking_the_remote(env) -> None:
    """The row says CLOUD_VERIFIED. The remote no longer has the file."""
    config, db = env
    path, _ = archived(config, db, "a", "2019/11/IMG_1.JPG")
    eligible, result = release_engine.plan(config, db, Selector(), provider=FakeCloud(holds={}))
    assert eligible == []
    assert result.blocks == {"not at the remote right now": 1}
    assert path.exists(), "the only local copy was deleted on a stale record"


def test_a_remote_copy_with_a_different_hash_blocks_the_release(env) -> None:
    config, db = env
    path, _ = archived(config, db, "a", "2019/11/IMG_1.JPG")
    fake = FakeCloud(holds={"2019/11/IMG_1.JPG": "0" * 64})
    eligible, result = release_engine.plan(config, db, Selector(), provider=fake)
    assert eligible == []
    assert "does not match" in next(iter(result.blocks))
    assert path.exists()


def test_release_refuses_when_cloud_verification_is_switched_off(env) -> None:
    config, db = env
    archived(config, db, "a", "2019/11/IMG_1.JPG")
    config = config.model_copy(
        update={"safety": config.safety.model_copy(update={"require_cloud_verification": False})}
    )
    with pytest.raises(release_engine.ReleaseError, match="require_cloud_verification"):
        release_engine.run(config, Selector(), provider=FakeCloud(), db=db)


# -- what it does -----------------------------------------------------------


def test_a_verified_asset_is_trashed_and_the_row_says_so(env, monkeypatch) -> None:
    config, db = env
    path, digest = archived(config, db, "a", "2019/11 Cape Town/IMG_1.JPG")
    fake = FakeCloud(holds={"2019/11 Cape Town/IMG_1.JPG": digest})

    trashed: list[str] = []

    def fake_trash(paths, *, binary=None):
        trashed.extend(str(p) for p in paths)
        return {str(p): p.stat().st_size for p in paths}

    # monkeypatch, not manual assignment: the hand-rolled restore put the fake
    # back instead of the original and would have leaked into every later test.
    monkeypatch.setattr(release_engine, "_trash", fake_trash)
    result = release_engine.run(config, Selector(), provider=fake, db=db)

    assert fake.checked, "the remote must be checked before anything is deleted"
    assert result.released == 1 and result.failed == 0
    assert trashed == [str(path)]
    row = db.conn.execute("SELECT local_status, local_path FROM assets").fetchone()
    assert row["local_status"] == release_engine.RELEASED
    assert row["local_path"] is None


def test_an_asset_already_off_the_phone_is_still_released(env, monkeypatch) -> None:
    """The state that was stranding 14.16 GB on 2026-09-17.

    Deleted from the phone, verified in Drive, Mac copy pure surplus -- and
    invisible to release because the shared selector required phone presence.
    """
    config, db = env
    path, digest = archived(config, db, "a", "2019/11 Cape Town/IMG_1.JPG", on_phone=False)
    fake = FakeCloud(holds={"2019/11 Cape Town/IMG_1.JPG": digest})

    trashed: list[str] = []
    monkeypatch.setattr(
        release_engine,
        "_trash",
        lambda paths, *, binary=None: (
            trashed.extend(str(p) for p in paths),
            {str(p): p.stat().st_size for p in paths},
        )[1],
    )
    result = release_engine.run(config, Selector(), provider=fake, db=db)

    assert result.released == 1 and result.failed == 0
    assert trashed == [str(path)]


def test_release_still_refuses_an_off_phone_asset_the_cloud_lost(env) -> None:
    """Widening the selector must not widen what release will delete."""
    config, db = env
    path, _ = archived(config, db, "a", "2019/11/IMG_1.JPG", on_phone=False)
    eligible, result = release_engine.plan(config, db, Selector(), provider=FakeCloud(holds={}))
    assert eligible == []
    assert result.blocks == {"not at the remote right now": 1}
    assert path.exists(), "an asset with no phone copy and no cloud copy lost its last one"


def test_the_phone_presence_clause_survives_for_every_other_verb(env) -> None:
    """`for_release` must not leak into the verbs that touch the device."""
    config, db = env
    archived(config, db, "a", "2019/11/IMG_1.JPG", on_phone=False)
    where, params = Selector().where()
    assert "a.present_on_phone = 1" in where
    assert not db.conn.execute(f"SELECT 1 FROM assets a WHERE {where}", params).fetchall()


def test_a_released_asset_is_never_fetched_again(env) -> None:
    """Otherwise the cycle is fetch, release, fetch, release, forever."""
    _config, db = env
    db.conn.execute(
        "INSERT INTO assets (device_id, identity_key, filename, media_type, created_at_device, "
        "size_bytes, source_bundle_id, present_on_phone, subtypes, local_status, "
        "first_seen_at, last_seen_at, created_at, updated_at) "
        "VALUES (1, 'gone', 'IMG_9.JPG', 'PHOTO', '2019-11-04T10:00:00+00:00', 100, "
        "'com.apple.camera', 1, '[]', ?, ?, ?, ?, ?)",
        (release_engine.RELEASED, utcnow(), utcnow(), utcnow(), utcnow()),
    )
    candidates = sync_engine._candidates(db, Selector())
    assert [c["identity_key"] for c in candidates] == []


def test_a_released_asset_is_not_reported_as_a_missing_archive_file(env) -> None:
    """reconcile_missing must not resurrect a file that was deleted on purpose."""
    _config, db = env
    db.conn.execute(
        "INSERT INTO assets (device_id, identity_key, filename, media_type, created_at_device, "
        "size_bytes, source_bundle_id, present_on_phone, subtypes, local_status, "
        "first_seen_at, last_seen_at, created_at, updated_at) "
        "VALUES (1, 'gone', 'IMG_9.JPG', 'PHOTO', '2019-11-04T10:00:00+00:00', 100, "
        "'com.apple.camera', 1, '[]', ?, ?, ?, ?, ?)",
        (release_engine.RELEASED, utcnow(), utcnow(), utcnow(), utcnow()),
    )
    assert sync_engine.reconcile_missing(db, Selector()) == 0
