"""Places from Photos into the ledger, and selecting on them."""

from __future__ import annotations

import plistlib

from iphone_image import locate
from iphone_image.db.database import utcnow
from iphone_image.photos.places import Address, _address_from_blob
from iphone_image.selector import Selector


def _asset(db, key: str, lat: float | None, lon: float | None, size: int = 100) -> None:
    db.conn.execute(
        "INSERT OR IGNORE INTO devices (id, udid, name, product_kind, first_seen_at, "
        "last_seen_at, created_at, updated_at) VALUES (1, 'lib', 'lib', 'PhotosLibrary', "
        "?, ?, ?, ?)",
        (utcnow(), utcnow(), utcnow(), utcnow()),
    )
    db.conn.execute(
        "INSERT INTO assets (device_id, identity_key, filename, media_type, size_bytes, "
        "latitude, longitude, present_on_phone, first_seen_at, last_seen_at, created_at, "
        "updated_at) VALUES (1, ?, 'a.jpg', 'PHOTO', ?, ?, ?, 1, ?, ?, ?, ?)",
        (key, size, lat, lon, utcnow(), utcnow(), utcnow(), utcnow()),
    )


CAPE_TOWN = Address(country="South Africa", region="Western Cape", city="Cape Town", suburb=None)
ATHENS = Address(country="Greece", region="Attica", city="Athens", suburb="Plaka")


def test_assets_are_placed_and_selectable_by_country_and_city(db) -> None:
    _asset(db, "U1/L0/001", -33.9249, 18.4241)
    _asset(db, "U2/L0/001", -33.92491, 18.42412)  # same 100 m cell
    _asset(db, "U3/L0/001", 37.97, 23.72)
    _asset(db, "U4/L0/001", None, None)  # address but no coordinates in the ledger
    _asset(db, "U5/L0/001", 1.0, 1.0)  # coordinates but no address

    result = locate.run(
        None, db, addresses={"U1": CAPE_TOWN, "U2": CAPE_TOWN, "U3": ATHENS, "U4": ATHENS}
    )
    assert (result.located, result.places, result.no_coordinates) == (3, 2, 1)

    def count(selector: Selector) -> int:
        where, params = selector.where()
        return db.conn.execute(f"SELECT COUNT(*) FROM assets a WHERE {where}", params).fetchone()[0]

    assert count(Selector(country="south africa")) == 2, "case is ignored"
    assert count(Selector(city="Athens")) == 1
    assert count(Selector(country="Greece", city="Cape Town")) == 0
    assert count(Selector(country="Nowhere")) == 0

    summary = locate.summary(db)
    assert summary["countries"][0] == {
        "country": "South Africa",
        "count": 2,
        "bytes": 200,
        "verifiedBytes": 0,
    }
    assert summary["noLocation"]["count"] == 2


def test_rerunning_changes_nothing(db) -> None:
    _asset(db, "U1/L0/001", -33.9249, 18.4241)
    locate.run(None, db, addresses={"U1": CAPE_TOWN})
    again = locate.run(None, db, addresses={"U1": CAPE_TOWN})
    assert again.located == 1
    assert db.conn.execute("SELECT COUNT(*) FROM locations").fetchone()[0] == 1


def test_the_address_is_read_by_field_not_by_position() -> None:
    """No postcode put the street where the city belongs when read by position."""
    uid = plistlib.UID
    archive = {
        "$archiver": "NSKeyedArchiver",
        "$top": {"root": uid(1)},
        "$objects": [
            "$null",
            {"_city": uid(2), "_country": uid(3), "_state": uid(4), "_subLocality": uid(0)},
            "Cape\xa0Town",
            "South Africa",
            "Western Cape",
        ],
        "$version": 100000,
    }
    found = _address_from_blob(plistlib.dumps(archive, fmt=plistlib.FMT_BINARY))
    assert found == Address("South Africa", "Western Cape", "Cape Town", None)
    assert _address_from_blob(b"not a plist") is None


def test_ids_narrow_to_the_items_picked(db) -> None:
    for n in range(3):
        _asset(db, f"U{n}/L0/001", None, None)
    where, params = Selector(ids=(1, 3)).where()
    rows = db.conn.execute(f"SELECT a.id FROM assets a WHERE {where}", params).fetchall()
    assert [r[0] for r in rows] == [1, 3]


def test_recovery_checks_each_download_against_its_recorded_hash(db, config, tmp_path) -> None:
    import hashlib

    from iphone_image import recover

    _asset(db, "U1/L0/001", None, None)
    _asset(db, "U2/L0/001", None, None)
    good, bad = b"original", b"tampered"
    dest = config.cloud.destination
    db.conn.execute(
        "UPDATE assets SET cloud_path = ?, sha256 = ? WHERE id = 1",
        (f"{dest}/2019/a.jpg", hashlib.sha256(good).hexdigest()),
    )
    db.conn.execute(
        "UPDATE assets SET cloud_path = ?, sha256 = ? WHERE id = 2",
        (f"{dest}/2019/b.jpg", hashlib.sha256(good).hexdigest()),
    )

    class Fake:
        def download(self, destination, relatives, to):
            assert destination == dest
            for rel in relatives:
                (to / rel).parent.mkdir(parents=True, exist_ok=True)
                (to / rel).write_bytes(good if rel.endswith("a.jpg") else bad)

    out = tmp_path / "out"
    result = recover.run(config, db, [1, 2], to=out, provider=Fake())
    assert [f["verified"] for f in sorted(result.files, key=lambda f: f["id"])] == [True, False]

    import pytest

    with pytest.raises(recover.RecoverError, match="not empty"):
        recover.run(config, db, [1], to=out, provider=Fake())


def test_settle_uses_only_a_scan_taken_after_the_request(db) -> None:
    from iphone_image import remove

    _asset(db, "U1/L0/001", None, None)
    _asset(db, "U2/L0/001", None, None)
    db.conn.execute(
        "INSERT INTO scans (id, device_id, started_at, finished_at, status) VALUES "
        "(6, 1, '2026-09-20T00:00:00+00:00', '2026-09-20T00:05:00+00:00', 'COMPLETED'), "
        "(7, 1, '2026-09-22T00:00:00+00:00', '2026-09-22T00:05:00+00:00', 'COMPLETED')"
    )
    db.conn.execute("UPDATE assets SET last_seen_scan_id = 6 WHERE id = 1")  # gone since
    db.conn.execute("UPDATE assets SET last_seen_scan_id = 7 WHERE id = 2")  # still there
    db.conn.execute(
        "INSERT INTO operations (id, operation, status, command, started_at) "
        "VALUES (9, 'REMOVE', 'STARTED', 'remove-from-iphone', '2026-09-21T00:00:00+00:00')"
    )
    for asset in (1, 2):
        db.conn.execute(
            "INSERT INTO deletion_events (asset_id, device_id, operation_id, requested_at, "
            "status, policy, evidence, size_bytes) VALUES (?, 1, 9, "
            "'2026-09-21T00:00:00+00:00', 'REQUESTED', 'cloud_verified', '{}', 10)",
            (asset,),
        )

    preview = remove.settle(db)
    assert (preview.confirmed_gone, preview.still_on_phone) == (1, 1)
    assert (
        db.conn.execute(
            "SELECT COUNT(*) FROM deletion_events WHERE status = 'REQUESTED'"
        ).fetchone()[0]
        == 2
    ), "without apply nothing is written"

    done = remove.settle(db, apply=True)
    assert done.operations_closed == 1
    rows = dict(db.conn.execute("SELECT asset_id, status FROM deletion_events").fetchall())
    assert rows == {1: "REMOVED", 2: "FAILED"}
    present = db.conn.execute("SELECT present_on_phone FROM assets WHERE id = 1").fetchone()[0]
    assert present == 0
