"""Removal from the device: what it refuses, and why.

`docs/PLAN.md` gates P10 on this directory existing and passing, and the gate
is pointed at the right thing. Every other engine here can be wrong and cost a
re-run. This one deletes photographs from the user's phone, so the tests are
written as a list of things that must never happen, and the happy path comes
last because it is the least interesting.

Nothing here touches a real Photos library. The helper is faked at the process
boundary, and every refusal test asserts that the fake was never asked to
delete anything -- a test that only checked the return value would pass while
the assets went.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from iphone_image import remove as remove_engine
from iphone_image.config import Config
from iphone_image.db.database import Database, utcnow
from iphone_image.scanner import ScanResult
from iphone_image.selector import Selector

DEST = "Family Photos/Andrew iPhone Archive"
SCAN = 7


class FakeHelper:
    """The Photos library, faked at the process boundary.

    `asked` is the point of it: a refusal that still called delete would pass
    a test that only looked at counts.
    """

    def __init__(self, *, lose: set[str] | None = None, survive: set[str] | None = None) -> None:
        self.asked: list[str] = []
        self.lose = lose or set()
        self.survive = survive or set()

    def check(self) -> None:
        return None

    def delete(self, identifiers: list[str], *, timeout: float = 3600) -> Any:
        self.asked.extend(identifiers)
        for identifier in identifiers:
            if identifier in self.lose:
                yield {"event": "deleteSkipped", "localIdentifier": identifier}
            elif identifier in self.survive:
                yield {
                    "event": "deleteFailed",
                    "localIdentifier": identifier,
                    "error": "still present after deleteAssets reported success",
                }
            else:
                yield {"event": "deleted", "localIdentifier": identifier}


class FakeCloud:
    name = "fake"
    last_transfer_bytes: int | None = None

    def __init__(self, stored: dict[str, str] | None = None) -> None:
        self.stored = stored if stored is not None else {}

    def check(self) -> None:
        return None

    def upload(self, root: Path, relatives: list[str], destination: str) -> None:
        return None

    def hashes(self, destination: str, subdir: str | None = None) -> dict[str, str]:
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
            "destination": DEST,
        },
        remove_from_iphone={"policy": "cloud_verified"},
    )
    db = Database(config.database.path).connect()
    db.migrate()
    db.conn.execute(
        "INSERT INTO devices (id, udid, name, first_seen_at, last_seen_at, created_at, updated_at) "
        "VALUES (1, 'lib', 'lib', ?, ?, ?, ?)",
        (utcnow(), utcnow(), utcnow(), utcnow()),
    )
    # A real scan row: last_seen_scan_id is a foreign key, and the whole point
    # of this engine is that it checks against a scan that actually happened.
    db.conn.execute(
        "INSERT INTO scans (id, device_id, started_at, status, media_presentation) "
        "VALUES (?, 1, ?, 'COMPLETED', 'original')",
        (SCAN, utcnow()),
    )
    db.conn.execute(
        "INSERT INTO scans (id, device_id, started_at, status, media_presentation) "
        "VALUES (?, 1, ?, 'COMPLETED', 'original')",
        (SCAN - 1, utcnow()),
    )
    return config, db


def on_phone(
    config,
    db,
    key,
    relative,
    *,
    body=b"photo",
    cloud=True,
    subtypes="[]",
    scan_id=SCAN,
    proxy=0.0,
    burst=None,
    local_verified=True,
):
    """One asset, archived and on the phone, ready to be refused or removed."""
    path = config.archive.local_path / "camera" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()
    db.conn.execute(
        "INSERT INTO assets (device_id, identity_key, device_asset_id, filename, media_type, "
        "created_at_device, size_bytes, source_bundle_id, present_on_phone, subtypes, "
        "local_path, local_status, sha256, cloud_status, cloud_path, proxy_suspicion, "
        "burst_uuid, last_seen_scan_id, first_seen_at, last_seen_at, created_at, updated_at) "
        "VALUES (1, ?, ?, ?, 'PHOTO', '2019-11-04T10:00:00+00:00', ?, 'com.apple.camera', 1, ?, "
        "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            key,
            f"{key}/L0/001",
            Path(relative).name,
            len(body),
            subtypes,
            str(path),
            "LOCAL_VERIFIED" if local_verified else "DISCOVERED",
            digest,
            "CLOUD_VERIFIED" if cloud else "NONE",
            f"{DEST}/{relative}" if cloud else None,
            proxy,
            burst,
            scan_id,
            utcnow(),
            utcnow(),
            utcnow(),
            utcnow(),
        ),
    )
    return path, digest


def plan(config, db, cloud: FakeCloud | None = None, **kw):
    return remove_engine.plan(
        config, db, Selector(), scan_id=SCAN, provider=cloud or FakeCloud(), **kw
    )


# -- what it must never do --------------------------------------------------


def test_the_default_policy_refuses_outright(env) -> None:
    """`never` is the default and means what it says."""
    config, db = env
    config = config.model_copy(
        update={
            "remove_from_iphone": config.remove_from_iphone.model_copy(update={"policy": "never"})
        }
    )
    on_phone(config, db, "a", "2019/11/IMG_1.JPG")
    with pytest.raises(remove_engine.RemoveError, match="never"):
        plan(config, db)


def test_a_live_photo_whose_motion_half_was_never_archived_is_never_removed(env) -> None:
    """The finding that prompted this gate.

    Measured on the real archive: 528 Live Photos archived, zero `.MOV` files
    anywhere in the tree. The still image passes every other check, so without
    this the motion half of 528 photographs would have been deleted from the
    phone and never existed anywhere else.
    """
    config, db = env
    on_phone(config, db, "a", "2019/11/IMG_1.HEIC", subtypes=json.dumps(["live"]))
    remote = {"2019/11/IMG_1.HEIC": hashlib.sha256(b"photo").hexdigest()}

    eligible, result = plan(config, db, FakeCloud(remote))

    assert eligible == []
    assert result.blocks == {"Live Photo whose motion half is not archived": 1}


def test_a_burst_with_unfetched_frames_is_never_removed(env) -> None:
    config, db = env
    on_phone(config, db, "a", "2019/11/IMG_1.JPG", burst="burst-1")
    on_phone(config, db, "b", "2019/11/IMG_2.JPG", burst="burst-1", local_verified=False)

    eligible, result = plan(config, db)

    assert eligible == []
    assert any("burst" in reason for reason in result.blocks)


def test_a_suspected_proxy_is_never_removed(env) -> None:
    """SAFETY section 2: deleting a proxy destroys the only full-resolution original."""
    config, db = env
    on_phone(config, db, "a", "2019/11/IMG_1.JPG", proxy=0.9)
    eligible, result = plan(config, db)
    assert eligible == []
    assert result.examined == 0, (
        "selector.for_removal() excludes it before eligibility is even considered, "
        "and that exclusion cannot be overridden by any flag"
    )


def test_an_asset_missing_from_the_fresh_scan_is_never_removed(env) -> None:
    """SAFETY section 7: the plan is computed against a scan taken in this run."""
    config, db = env
    on_phone(config, db, "a", "2019/11/IMG_1.JPG", scan_id=SCAN - 1)
    eligible, result = plan(config, db)
    assert eligible == []
    assert result.blocks == {"not present in the scan taken just now": 1}


def test_a_local_copy_that_no_longer_matches_its_hash_is_never_removed(env) -> None:
    """A verified copy that has since been corrupted is not a copy."""
    config, db = env
    path, _ = on_phone(config, db, "a", "2019/11/IMG_1.JPG")
    path.write_bytes(b"something else entirely")

    eligible, result = plan(config, db)

    assert eligible == []
    assert result.blocks == {"the local copy no longer matches its recorded hash": 1}


def test_a_missing_local_copy_is_never_removed(env) -> None:
    config, db = env
    path, _ = on_phone(config, db, "a", "2019/11/IMG_1.JPG")
    path.unlink()
    eligible, result = plan(config, db)
    assert eligible == []
    assert result.blocks == {"the local copy is missing": 1}


def test_an_asset_the_remote_has_lost_is_never_removed(env) -> None:
    """The ledger says CLOUD_VERIFIED; the remote is asked anyway."""
    config, db = env
    on_phone(config, db, "a", "2019/11/IMG_1.JPG")
    eligible, result = plan(config, db, FakeCloud({}))
    assert eligible == []
    assert result.blocks == {"not at the remote right now": 1}


def test_a_remote_copy_with_the_wrong_hash_is_never_removed(env) -> None:
    config, db = env
    on_phone(config, db, "a", "2019/11/IMG_1.JPG")
    eligible, result = plan(config, db, FakeCloud({"2019/11/IMG_1.JPG": "0" * 64}))
    assert eligible == []
    assert result.blocks == {"the remote copy does not match the recorded hash": 1}


def test_an_asset_with_no_cloud_copy_is_never_removed_under_cloud_policy(env) -> None:
    config, db = env
    on_phone(config, db, "a", "2019/11/IMG_1.JPG", cloud=False)
    eligible, result = plan(config, db)
    assert eligible == []
    assert result.blocks == {"not verified in the cloud": 1}


def test_every_refusal_reaches_the_device_never(env) -> None:
    """The one that would catch a plan/apply mismatch.

    Each of the above proves `plan` refuses. This proves `run` does not delete
    anyway: the fake records every identifier it was asked to delete, and for
    a library of nothing-but-blocked assets that list must be empty.
    """
    config, db = env
    on_phone(config, db, "a", "2019/11/IMG_1.HEIC", subtypes=json.dumps(["live"]))
    on_phone(config, db, "b", "2019/11/IMG_2.JPG", cloud=False)
    on_phone(config, db, "c", "2019/11/IMG_3.JPG", scan_id=SCAN - 1)

    helper = FakeHelper()
    result = remove_engine.run(
        config,
        Selector(),
        provider=FakeCloud(),
        db=db,
        helper=helper,
        scanner=lambda _config: ScanResult(scan_id=SCAN),
    )

    assert result.removed == 0
    assert helper.asked == [], "nothing may reach the library"


# -- what it does, and what it records --------------------------------------


def test_a_fully_proved_asset_is_removed_and_recorded(env) -> None:
    config, db = env
    on_phone(config, db, "a", "2019/11/IMG_1.JPG")
    remote = {"2019/11/IMG_1.JPG": hashlib.sha256(b"photo").hexdigest()}

    _eligible, preview = plan(config, db, FakeCloud(remote))
    assert preview.eligible == 1

    helper = FakeHelper()
    result = remove_engine.run(
        config,
        Selector(),
        provider=FakeCloud(remote),
        db=db,
        helper=helper,
        scanner=lambda _config: ScanResult(scan_id=SCAN),
    )

    assert result.removed == 1
    assert helper.asked == ["a/L0/001"]
    row = db.conn.execute("SELECT present_on_phone, removed_from_phone_at FROM assets").fetchone()
    assert row[0] == 0
    assert row[1] is not None
    event = db.conn.execute("SELECT status, policy, evidence FROM deletion_events").fetchone()
    assert event[0] == "REMOVED"
    assert event[1] == "cloud_verified"
    assert json.loads(event[2])["remote_hash_confirmed"] is True


def test_an_asset_the_library_no_longer_holds_is_recorded_as_already_gone(env) -> None:
    """Not an error, but it must not be recorded as this run's doing."""
    config, db = env
    on_phone(config, db, "a", "2019/11/IMG_1.JPG")
    remote = {"2019/11/IMG_1.JPG": hashlib.sha256(b"photo").hexdigest()}

    result = remove_engine.run(
        config,
        Selector(),
        provider=FakeCloud(remote),
        db=db,
        helper=FakeHelper(lose={"a/L0/001"}),
        scanner=lambda _config: ScanResult(scan_id=SCAN),
    )

    assert result.removed == 0
    assert result.already_gone == 1
    assert db.conn.execute("SELECT status FROM deletion_events").fetchone()[0] == "ALREADY_GONE"


def test_an_asset_that_survives_deletion_is_a_failure_not_a_success(env) -> None:
    """deleteAssets returning is a claim; the re-fetch is the evidence."""
    config, db = env
    on_phone(config, db, "a", "2019/11/IMG_1.JPG")
    remote = {"2019/11/IMG_1.JPG": hashlib.sha256(b"photo").hexdigest()}

    result = remove_engine.run(
        config,
        Selector(),
        provider=FakeCloud(remote),
        db=db,
        helper=FakeHelper(survive={"a/L0/001"}),
        scanner=lambda _config: ScanResult(scan_id=SCAN),
    )

    assert result.removed == 0
    assert result.failed == 1
    row = db.conn.execute("SELECT present_on_phone, removed_from_phone_at FROM assets").fetchone()
    assert row[0] == 1, "it is still on the phone and the ledger must still say so"
    assert row[1] is None
    assert db.conn.execute("SELECT status FROM deletion_events").fetchone()[0] == "FAILED"


def test_blocked_assets_are_individually_listable(env) -> None:
    """SAFETY section 6: "a count alone is not acceptable output"."""
    config, db = env
    on_phone(config, db, "a", "2019/11/IMG_1.HEIC", subtypes=json.dumps(["live"]))

    _, result = plan(config, db)

    assert len(result.blocked_assets) == 1
    assert result.blocked_assets[0]["filename"] == "IMG_1.HEIC"
    assert "Live Photo" in result.blocked_assets[0]["reason"]


# -- the selector's bounds are the caller's only brake ----------------------


def test_limit_is_honoured(env) -> None:
    """`--limit` is how a caller bounds how much gets deleted.

    It was dropped from the query in the first cut of this module, so a plan
    asked to cover 6,831 assets covered all 24,481 and offered to delete
    22.4 GB where 15 GB had been requested. Nothing warned, because a silently
    ignored flag looks exactly like a flag that worked.
    """
    config, db = env
    for i in range(6):
        on_phone(config, db, f"a{i}", f"2019/11/IMG_{i}.JPG", body=f"photo-{i}".encode())

    _, all_of_them = plan(config, db)
    assert all_of_them.examined == 6

    _, limited = remove_engine.plan(
        config, db, Selector(limit=2), scan_id=SCAN, provider=FakeCloud()
    )
    assert limited.examined == 2, "the limit must reach the query, not be applied afterwards"


def test_order_is_honoured(env) -> None:
    """--order decides *which* assets a limit keeps, so ignoring it picks the wrong ones."""
    config, db = env
    for i, size in enumerate([b"x" * 10, b"x" * 500, b"x" * 50]):
        on_phone(config, db, f"a{i}", f"2019/11/IMG_{i}.JPG", body=size)

    _, biggest = remove_engine.plan(
        config, db, Selector(order="largest", limit=1), scan_id=SCAN, provider=FakeCloud()
    )
    _, smallest = remove_engine.plan(
        config, db, Selector(order="smallest", limit=1), scan_id=SCAN, provider=FakeCloud()
    )
    assert biggest.examined == 1
    assert smallest.examined == 1
    assert biggest.blocked_assets[0]["filename"] == "IMG_1.JPG"
    assert smallest.blocked_assets[0]["filename"] == "IMG_0.JPG"


def test_run_does_not_plan_a_second_time_when_given_a_plan(env) -> None:
    """Planning twice is how the numbers came apart.

    `release` printed "released 7,138 of 6,869" and a blocked count that
    disagreed with its own reason list by 269, because the CLI planned for the
    preview and `run` planned again -- and between the two, an upload had
    verified more files. For `remove-from-iphone` the second plan also meant a
    second full scan of a 95,000-asset library, and a confirmation phrase
    bound to the first count meeting a second, different one.
    """
    config, db = env
    on_phone(config, db, "a", "2019/11/IMG_1.JPG")
    remote = {"2019/11/IMG_1.JPG": hashlib.sha256(b"photo").hexdigest()}

    scans: list[int] = []

    def counting_scanner(_config):
        scans.append(1)
        return ScanResult(scan_id=SCAN)

    prepared = remove_engine.plan(config, db, Selector(), scan_id=SCAN, provider=FakeCloud(remote))
    result = remove_engine.run(
        config,
        Selector(),
        provider=FakeCloud(remote),
        db=db,
        helper=FakeHelper(),
        scanner=counting_scanner,
        prepared=prepared,
    )

    assert scans == [], "a plan made in this invocation must not trigger another scan"
    assert result.removed == 1
    assert result is prepared[1], "the numbers reported are the ones that were approved"


def test_without_a_prepared_plan_it_still_scans_for_itself(env) -> None:
    """The freshness rule is unchanged for every other caller."""
    config, db = env
    on_phone(config, db, "a", "2019/11/IMG_1.JPG")
    scans: list[int] = []

    def counting_scanner(_config):
        scans.append(1)
        return ScanResult(scan_id=SCAN)

    remove_engine.run(
        config,
        Selector(),
        provider=FakeCloud({"2019/11/IMG_1.JPG": hashlib.sha256(b"photo").hexdigest()}),
        db=db,
        helper=FakeHelper(),
        scanner=counting_scanner,
    )
    assert scans == [1], "no plan handed in means it must take its own scan"
