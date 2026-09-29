"""WhatsApp media: one flat Drive folder, numbered name clashes, albums kept.

The owner's decision (2026-09-29): WhatsApp media older than 18 months goes to
a single `WhatsApp Media` folder with no year/month tree, a clashing name is
indexed `IMG_1 (1).jpg`, and anything favourited or in an album of their own
stays on the phone. Removal is still gated on the Drive copy, as for every
other channel.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from iphone_image import cloud as cloud_engine
from iphone_image.config import Config
from iphone_image.db.database import Database, utcnow
from iphone_image.organize.paths import unique_filename
from iphone_image.remove import _user_album
from iphone_image.selector import Selector
from iphone_image.sync import archive_path_for

PHOTOS = "Family Photos/Andrew iPhone Archive"
VIDEOS = "Family Videos"
WA = "Family Photos/Andrew iPhone Archive/WhatsApp Media"
WA_BUNDLE = "net.whatsapp.WhatsApp"


@pytest.fixture
def env(tmp_path: Path):
    config = Config(
        archive={"local_path": str(tmp_path / "archive")},
        database={"path": str(tmp_path / "db.sqlite")},
        logging={"path": str(tmp_path / "logs")},
        photos={"library_path": str(tmp_path / "lib.photoslibrary")},
        organization={"pattern": "{source}/{year}/{month}", "flat_channels": ["whatsapp"]},
        remove_from_iphone={"keep_user_album_channels": ["whatsapp"]},
        cloud={
            "enabled": True,
            "provider": "google_drive",
            "remote": "gdrive",
            "destination": PHOTOS,
            "video_destination": VIDEOS,
            "whatsapp_destination": WA,
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


def _asset(bundle: str = WA_BUNDLE, albums: list[str] | None = None, **extra) -> dict:
    return {
        "identity_key": "k",
        "filename": "IMG_1.jpg",
        "media_type": "PHOTO",
        "created_at_device": "2023-02-14T10:00:00+00:00",
        "source_bundle_id": bundle,
        "subtypes": "[]",
        "album_names": json.dumps(albums or []),
        **extra,
    }


# -- routing ---------------------------------------------------------------


def test_whatsapp_photos_and_videos_both_go_to_the_one_folder(env) -> None:
    """By channel, before media type: a WhatsApp video must not land in Family Videos."""
    config, _db = env
    assert cloud_engine.destination_for(config, "PHOTO", "whatsapp") == WA
    assert cloud_engine.destination_for(config, "VIDEO", "whatsapp") == WA
    assert cloud_engine.destination_for(config, "VIDEO", "camera") == VIDEOS
    assert cloud_engine.destination_for(config, "PHOTO", "camera") == PHOTOS


def test_the_whatsapp_folder_is_a_known_destination_and_wins_the_prefix(env) -> None:
    """It sits inside the photo archive, so release must split its paths correctly."""
    config, _db = env
    assert WA in cloud_engine.destinations(config)
    assert cloud_engine.split_cloud_path(config, f"{WA}/IMG_1 (1).jpg") == (WA, "IMG_1 (1).jpg")


def test_without_a_whatsapp_destination_nothing_changes(env) -> None:
    config, _db = env
    bare = config.model_copy(
        update={"cloud": config.cloud.model_copy(update={"whatsapp_destination": ""})}
    )
    assert cloud_engine.destination_for(bare, "PHOTO", "whatsapp") == PHOTOS
    assert cloud_engine.destination_for(bare, "VIDEO", "whatsapp") == VIDEOS


# -- flat layout -----------------------------------------------------------


def test_a_flat_channel_ignores_the_pattern(env) -> None:
    config, _db = env
    root = config.archive.local_path
    assert archive_path_for(config, _asset()) == root / "whatsapp"
    assert archive_path_for(config, _asset(bundle="com.apple.camera")) == root / "camera/2023/02"


def test_the_remote_path_of_a_flat_file_is_just_its_name(env) -> None:
    """cloud.plan mirrors the local layout, so flat locally is flat on Drive."""
    config, db = env
    body = b"wa photo"
    path = config.archive.local_path / "whatsapp" / "IMG_1 (1).jpg"
    path.parent.mkdir(parents=True)
    path.write_bytes(body)
    db.conn.execute(
        "INSERT INTO assets (device_id, identity_key, filename, media_type, created_at_device, "
        "size_bytes, source_bundle_id, present_on_phone, subtypes, local_path, local_status, "
        "sha256, first_seen_at, last_seen_at, created_at, updated_at) "
        "VALUES (1, 'wa1', 'IMG_1.jpg', 'VIDEO', '2023-02-14T10:00:00+00:00', ?, ?, 1, '[]', "
        "?, 'LOCAL_VERIFIED', ?, ?, ?, ?, ?)",
        (len(body), WA_BUNDLE, str(path), hashlib.sha256(body).hexdigest(),
         utcnow(), utcnow(), utcnow(), utcnow()),
    )
    [upload] = cloud_engine.plan(config, Selector(source="whatsapp"), db)
    assert upload.destination == WA
    assert str(upload.relative) == "IMG_1 (1).jpg"


# -- numbered names ----------------------------------------------------------


def test_a_clash_in_a_flat_folder_is_numbered(tmp_path: Path) -> None:
    (tmp_path / "IMG_1.jpg").write_bytes(b"a")
    assert unique_filename(tmp_path, "IMG_1.jpg", "abc", numbered=True) == "IMG_1 (1).jpg"
    (tmp_path / "IMG_1 (1).jpg").write_bytes(b"b")
    assert unique_filename(tmp_path, "IMG_1.jpg", "abc", numbered=True) == "IMG_1 (2).jpg"


def test_a_free_name_is_left_alone_when_numbered(tmp_path: Path) -> None:
    assert unique_filename(tmp_path, "IMG_1.jpg", "abc", numbered=True) == "IMG_1.jpg"


def test_numbering_respects_claims_not_just_files(tmp_path: Path) -> None:
    """A released asset's file is gone from the Mac but its name is still on Drive."""
    claimed = {tmp_path / "IMG_1.jpg", tmp_path / "IMG_1 (1).jpg"}
    name = unique_filename(tmp_path, "IMG_1.jpg", "abc", taken=claimed.__contains__, numbered=True)
    assert name == "IMG_1 (2).jpg"


def test_hash_suffixes_are_unchanged_for_other_channels(tmp_path: Path) -> None:
    (tmp_path / "IMG_1.jpg").write_bytes(b"a")
    assert unique_filename(tmp_path, "IMG_1.jpg", "abcdef0123456789") == "IMG_1__ABCDEF01.jpg"


# -- what stays on the phone -------------------------------------------------


def test_automatic_albums_and_the_whatsapp_album_do_not_keep_an_asset(env) -> None:
    """The real album set seen on old WhatsApp items, 2026-09-29."""
    config, _db = env
    seen = ["Recents", "Recently Saved", "WhatsApp", "Videos", "Selfies", "Panoramas", "Slo-mo"]
    assert _user_album(config, _asset(albums=seen)) is None


def test_an_album_of_the_users_own_keeps_it(env) -> None:
    config, _db = env
    assert _user_album(config, _asset(albums=["Recents", "Wedding"])) == "Wedding"


def test_an_unrecognised_album_name_keeps_it(env) -> None:
    """The list is of automatic albums, so the unknown direction is the safe one."""
    config, _db = env
    assert _user_album(config, _asset(albums=["Some New Smart Album"])) is not None


def test_an_unreadable_album_list_keeps_it(env) -> None:
    config, _db = env
    assert _user_album(config, _asset(album_names="not json")) is not None


def test_album_keeping_is_scoped_to_the_configured_channels(env) -> None:
    """Camera removal ran for weeks without this rule; it must not change under it."""
    config, _db = env
    assert _user_album(config, _asset(bundle="com.apple.camera", albums=["Wedding"])) is None
