"""The check that does not trust the ledger.

Every case here is a state the ledger reports as healthy. That is the point:
the 2026-09-17 collision was invisible to every other check in the tool.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from iphone_image import audit
from iphone_image.config import Config
from iphone_image.db.database import Database, utcnow

DEST = "Family Photos/Andrew iPhone Archive"
VIDEO_DEST = "Family Videos"


class FakeCloud:
    """A remote that holds exactly what it is told to hold."""

    name = "fake"
    last_transfer_bytes = None

    def __init__(self, holds: dict[str, dict[str, str]] | None = None) -> None:
        self.holds = holds or {}
        self.checked = False

    def check(self) -> None:
        self.checked = True

    def upload(self, root: Path, relatives: list[str], destination: str) -> None:
        raise AssertionError("audit must never upload")

    def hashes(self, destination: str, subdir: str | None = None) -> dict[str, str]:
        return dict(self.holds.get(destination, {}))


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
            "video_destination": VIDEO_DEST,
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


def verified(db, key, relative, *, body=b"photo", destination=DEST, media_type="PHOTO", claim=None):
    digest = hashlib.sha256(body).hexdigest()
    db.conn.execute(
        "INSERT INTO assets (device_id, identity_key, filename, media_type, created_at_device, "
        "size_bytes, source_bundle_id, present_on_phone, subtypes, local_status, sha256, "
        "cloud_status, cloud_path, archive_claim, first_seen_at, last_seen_at, created_at, "
        "updated_at) VALUES (1, ?, ?, ?, '2020-01-01T10:00:00+00:00', ?, 'com.apple.camera', 1, "
        "'[]', 'RELEASED', ?, 'CLOUD_VERIFIED', ?, ?, ?, ?, ?, ?)",
        (
            key,
            Path(relative).name,
            media_type,
            len(body),
            digest,
            f"{destination}/{relative}",
            claim if claim is not None else f"camera/{relative}",
            utcnow(),
            utcnow(),
            utcnow(),
            utcnow(),
        ),
    )
    return digest


# -- the offline half --------------------------------------------------------


def test_a_clean_ledger_reports_how_much_it_examined(env) -> None:
    """A check reporting OK over nothing must not look like one that covered everything."""
    _config, db = env
    verified(db, "a", "2020/01/IMG_1.JPG")
    report = audit.conflicts(db)
    assert report.ok
    assert report.rows_examined == 1


def test_two_assets_at_one_remote_path_is_a_conflict(env) -> None:
    """The exact shape of 2026-09-17, and the cheapest thing that finds it."""
    _config, db = env
    verified(db, "first", "2020/01-12 Home/IMG_0083.HEIC", body=b"one", claim="camera/a")
    verified(db, "second", "2020/01-12 Home/IMG_0083.HEIC", body=b"two", claim="camera/b")

    report = audit.conflicts(db)

    assert not report.ok
    kinds = [c.kind for c in report.conflicts]
    assert "duplicate cloud path" in kinds
    conflict = next(c for c in report.conflicts if c.kind == "duplicate cloud path")
    assert len(conflict.asset_ids) == 2


def test_two_assets_claiming_one_archive_name_is_a_conflict(env) -> None:
    """The same defect one step earlier, before anything has been uploaded."""
    _config, db = env
    verified(db, "a", "2020/01/IMG_1.JPG", claim="camera/2020/01/IMG_1.JPG")
    verified(db, "b", "2020/02/IMG_2.JPG", claim="camera/2020/01/IMG_1.JPG")
    report = audit.conflicts(db)
    assert "duplicate archive name" in [c.kind for c in report.conflicts]


def test_verified_with_nothing_to_verify_against_is_a_conflict(env) -> None:
    """No later run can ever check such a row, so it stays wrong while reading safe."""
    _config, db = env
    verified(db, "a", "2020/01/IMG_1.JPG")
    db.conn.execute("UPDATE assets SET sha256 = NULL")
    assert "verified with no hash" in [c.kind for c in audit.conflicts(db).conflicts]


# -- the remote half ---------------------------------------------------------


def test_everything_present_and_matching_is_a_pass(env) -> None:
    config, db = env
    digest = verified(db, "a", "2020/01/IMG_1.JPG")
    fake = FakeCloud({DEST: {"2020/01/IMG_1.JPG": digest}})

    report = audit.remote(config, db, provider=fake)

    assert fake.checked
    assert report.ok
    assert (report.claimed, report.matched, report.remote_files) == (1, 1, 1)
    assert report.disagreements == 0


def test_a_verified_row_whose_file_is_not_there_is_reported(env) -> None:
    config, db = env
    verified(db, "a", "2020/01/IMG_1.JPG")
    report = audit.remote(config, db, provider=FakeCloud({DEST: {}}))
    assert not report.ok
    assert [m["why"] for m in report.missing] == ["not at the remote"]


def test_a_file_whose_bytes_changed_is_reported(env) -> None:
    """Two photographs sharing one remote file looks exactly like this."""
    config, db = env
    verified(db, "a", "2020/01-12 Home/IMG_0083.HEIC", body=b"mine")
    other = hashlib.sha256(b"someone else's photograph").hexdigest()
    fake = FakeCloud({DEST: {"2020/01-12 Home/IMG_0083.HEIC": other}})

    report = audit.remote(config, db, provider=fake)

    assert not report.ok
    assert len(report.hash_mismatch) == 1
    assert report.matched == 0


def test_a_hash_the_remote_will_not_report_is_neither_a_match_nor_a_pass(env) -> None:
    """An unanswered question is not a yes."""
    config, db = env
    verified(db, "a", "2020/01/IMG_1.JPG")
    report = audit.remote(config, db, provider=FakeCloud({DEST: {"2020/01/IMG_1.JPG": ""}}))
    assert report.unhashed == 1
    assert report.matched == 0


def test_video_is_audited_against_its_own_archive(env) -> None:
    """Photos and videos live in separate remotes; one listing cannot cover both."""
    config, db = env
    photo = verified(db, "p", "2020/01/IMG_1.JPG")
    video = verified(
        db, "v", "2020/01/IMG_2.MOV", body=b"movie", destination=VIDEO_DEST, media_type="VIDEO"
    )
    fake = FakeCloud({DEST: {"2020/01/IMG_1.JPG": photo}, VIDEO_DEST: {"2020/01/IMG_2.MOV": video}})

    report = audit.remote(config, db, provider=fake)

    assert report.ok
    assert report.matched == 2
    assert sorted(report.destinations_listed) == sorted([DEST, VIDEO_DEST])


def test_files_at_the_remote_that_no_row_claims_are_counted_not_hidden(env) -> None:
    """The other half of the arithmetic. An interrupted upload leaves these."""
    config, db = env
    digest = verified(db, "a", "2020/01/IMG_1.JPG")
    fake = FakeCloud({DEST: {"2020/01/IMG_1.JPG": digest, "2020/01/IMG_ORPHAN.JPG": "0" * 64}})

    report = audit.remote(config, db, provider=fake)

    assert report.ok, "an orphan at the remote is not a failure"
    assert report.unclaimed == 1
    assert report.remote_files == 2 and report.claimed == 1


# -- a destination nested inside another ---------------------------------------

SHOTS = f"{DEST}/screenshots"


def test_nested_destination_files_are_not_double_counted(env) -> None:
    """A recursive listing of the photo archive contains every screenshot too.

    Left in, each screenshot is one file at the remote for the screenshot
    destination *and* an unclaimed file at the photo one, and the two counts the
    audit exists to reconcile stop adding up for a reason nobody can see.
    """
    config, db = env
    config = config.model_copy(
        update={
            "cloud": config.cloud.model_copy(update={"screenshot_destination": SHOTS}),
        }
    )
    photo = verified(db, "p", "2020/01/IMG_1.JPG")
    shot = verified(db, "s", "2020/01/IMG_2.PNG", body=b"png", destination=SHOTS)
    fake = FakeCloud(
        {
            DEST: {
                "2020/01/IMG_1.JPG": photo,
                # What the recursive listing of DEST also returns:
                "screenshots/2020/01/IMG_2.PNG": shot,
            },
            SHOTS: {"2020/01/IMG_2.PNG": shot},
        }
    )

    report = audit.remote(config, db, provider=fake)

    assert report.ok
    assert report.matched == 2
    assert report.remote_files == 2, "each file counted once, at the destination that owns it"
    assert report.unclaimed == 0, "a screenshot must not look like an orphan in the photo archive"
