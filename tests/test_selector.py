from __future__ import annotations

from datetime import UTC, datetime

import pytest

from iphone_image.selector import (
    PROXY_SUSPICION_BLOCK,
    Selector,
    SelectorError,
    channel_of,
    parse_size,
    plan_chunk,
    resolve_channel,
)

GB = 1024**3
MB = 1024**2


def asset(identifier: int, size: int, created: str = "2020-01-01T00:00:00+00:00") -> dict:
    return {"id": identifier, "size_bytes": size, "created_at_device": created}


# -- sizes -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("50GB", 50 * GB),
        ("10MB", 10 * MB),
        ("500KB", 512000),
        ("1024", 1024),
        ("1.5GB", int(1.5 * GB)),
        ("2 TB", 2 * 1024**4),
    ],
)
def test_parses_sizes(text: str, expected: int) -> None:
    assert parse_size(text) == expected


@pytest.mark.parametrize("text", ["", "big", "-5GB", "GB", "50 gigabytes"])
def test_rejects_bad_sizes(text: str) -> None:
    with pytest.raises(SelectorError):
        parse_size(text)


# -- channels ----------------------------------------------------------------


def test_camera_has_its_own_bundle_id() -> None:
    """Measured, not assumed: the camera identifies itself like any other app."""
    assert resolve_channel("camera") == "com.apple.camera"


def test_screenshots_have_a_channel_of_their_own() -> None:
    """Screenshots come from springboard, which cross-checks the subtype flag."""
    assert resolve_channel("screenshot") == "com.apple.springboard"


def test_unattributed_selects_assets_with_no_source_app() -> None:
    assert resolve_channel("unattributed") is None


def test_whatsapp_resolves_to_its_bundle_id() -> None:
    assert resolve_channel("whatsapp") == "net.whatsapp.WhatsApp"


def test_a_raw_bundle_id_passes_through() -> None:
    assert resolve_channel("com.example.app") == "com.example.app"


def test_an_unknown_channel_is_rejected_with_the_known_list() -> None:
    with pytest.raises(SelectorError, match="whatsapp"):
        resolve_channel("whatsupp")


# -- compilation -------------------------------------------------------------


def test_filters_compile_to_bound_parameters() -> None:
    """Never interpolate: metadata reaches here from a device."""
    sql, params = Selector(source="whatsapp", media_type="video", min_size="10MB").where()
    assert "?" in sql
    assert "net.whatsapp.WhatsApp" in params
    assert 10 * MB in params
    assert "net.whatsapp.WhatsApp" not in sql


def test_unattributed_compiles_to_a_null_check() -> None:
    sql, params = Selector(source="unattributed").where()
    assert "source_bundle_id IS NULL" in sql
    assert params == []


def test_camera_compiles_to_a_bound_bundle_id() -> None:
    sql, params = Selector(source="camera").where()
    assert "source_bundle_id = ?" in sql
    assert params == ["com.apple.camera"]


def test_older_than_becomes_a_timestamp() -> None:
    now = datetime(2026, 9, 14, tzinfo=UTC)
    _, params = Selector(older_than="1y").where(now=now)
    assert "2025-09-14" in params[0]


def test_proxy_suspects_are_not_excluded_by_default() -> None:
    """This test used to assert the opposite, citing docs/SAFETY.md section 2.

    That section says suspected proxies "are still backed up locally and to the
    cloud, normally" and are blocked from *removal*. Excluding them from every
    selector put the block on the wrong verb, and because sync uses the default
    selector, none of the 4,224 in the real library were ever backed up. The
    protection is in `for_removal` now, where no flag can waive it.
    """
    sql, _ = Selector().where()
    assert "proxy_suspicion" not in sql
    assert "proxy_suspicion" in Selector().for_removal().where()[0]


def test_proxy_suspects_can_still_be_excluded_on_request() -> None:
    sql, _ = Selector(include_proxy_suspects=False).where()
    assert "proxy_suspicion" in sql


def test_favourites_can_be_protected() -> None:
    sql, _ = Selector(include_favourites=False).where()
    assert "is_favourite = 0" in sql


def test_a_selector_always_restricts_to_present_assets() -> None:
    sql, _ = Selector().where()
    assert "present_on_phone = 1" in sql


@pytest.mark.parametrize(
    "kwargs",
    [
        {"media_type": "sideways"},
        {"order": "random"},
        {"source": "nope"},
        {"older_than": "soon"},
        {"min_size": "huge"},
    ],
)
def test_invalid_selectors_are_rejected_at_construction(kwargs: dict) -> None:
    with pytest.raises(SelectorError):
        Selector(**kwargs)


def test_describe_is_readable() -> None:
    text = Selector(
        source="whatsapp", media_type="video", older_than="1y", min_size="10MB"
    ).describe()
    assert "whatsapp" in text and "video" in text and "10MB" in text


# -- chunk planning ----------------------------------------------------------


def test_budget_stops_selection_before_it_is_exceeded() -> None:
    candidates = [asset(i, 10 * GB) for i in range(10)]
    plan = plan_chunk(candidates, budget_bytes=50 * GB)
    assert plan.count == 5
    assert plan.total_bytes == 50 * GB
    assert plan.remaining_assets == 5


def test_budget_is_never_exceeded_even_by_a_partial_fit() -> None:
    candidates = [asset(0, 30 * GB), asset(1, 30 * GB)]
    plan = plan_chunk(candidates, budget_bytes=50 * GB)
    assert plan.count == 1
    assert plan.total_bytes <= 50 * GB


def test_an_asset_larger_than_the_whole_budget_is_reported_not_dropped() -> None:
    """A 60GB video silently skipped every run would look like completion."""
    candidates = [asset(0, 60 * GB), asset(1, 1 * GB)]
    plan = plan_chunk(candidates, budget_bytes=50 * GB)
    assert plan.skipped_too_large and plan.skipped_too_large[0]["id"] == 0
    assert plan.count == 1, "the smaller asset should still be taken"


def test_count_limit_applies_independently_of_budget() -> None:
    candidates = [asset(i, 1 * MB) for i in range(100)]
    plan = plan_chunk(candidates, budget_bytes=50 * GB, limit=25)
    assert plan.count == 25
    assert plan.remaining_assets == 75


def test_whichever_limit_bites_first_wins() -> None:
    candidates = [asset(i, 10 * GB) for i in range(100)]
    plan = plan_chunk(candidates, budget_bytes=25 * GB, limit=50)
    assert plan.count == 2


def test_no_budget_takes_everything() -> None:
    candidates = [asset(i, 1 * GB) for i in range(7)]
    plan = plan_chunk(candidates)
    assert plan.count == 7
    assert not plan.exhausted_budget


def test_an_empty_candidate_list_plans_nothing() -> None:
    plan = plan_chunk([], budget_bytes=50 * GB)
    assert plan.count == 0 and plan.total_bytes == 0


def test_remaining_bytes_are_reported_for_the_next_chunk() -> None:
    candidates = [asset(i, 10 * GB) for i in range(10)]
    plan = plan_chunk(candidates, budget_bytes=50 * GB)
    assert plan.remaining_bytes == 50 * GB


def test_plan_is_deterministic() -> None:
    candidates = [asset(i, 7 * GB) for i in range(20)]
    first = plan_chunk(candidates, budget_bytes=50 * GB)
    second = plan_chunk(candidates, budget_bytes=50 * GB)
    assert [a["id"] for a in first.included] == [a["id"] for a in second.included]


def test_assets_with_unknown_size_do_not_break_the_budget() -> None:
    candidates = [{"id": 1}, asset(2, 1 * GB)]
    plan = plan_chunk(candidates, budget_bytes=2 * GB)
    assert plan.count == 2


def test_camera_also_matches_older_untagged_photos() -> None:
    """iOS only began recording com.apple.camera recently.

    On a real library the bundle id covered 2024-08 onwards while 13,787 older
    camera photos carried no source app. Matching the bundle id alone would miss
    39 GB of the user's own photographs.
    """
    sql, params = Selector(source="camera").where()
    assert "com.apple.camera" in params
    assert "source_bundle_id IS NULL" in sql, "older untagged photos must be included"
    assert "IMG" in sql, "untagged assets are narrowed by filename"
    assert "screenshot" in sql, "untagged screenshots must not count as camera"


def test_other_channels_stay_exact() -> None:
    sql, params = Selector(source="whatsapp").where()
    assert "OR" not in sql.split("source_bundle_id")[1][:40]
    assert params == ["net.whatsapp.WhatsApp"]


# ---------------------------------------------------------------------------
# channel_of, and its agreement with what --source actually selects
# ---------------------------------------------------------------------------


def test_channel_of_names_the_importing_app() -> None:
    assert channel_of({"source_bundle_id": "net.whatsapp.WhatsApp"}) == "whatsapp"
    assert channel_of({"source_bundle_id": "com.apple.camera"}) == "camera"
    assert channel_of({"source_bundle_id": "com.apple.springboard"}) == "screenshot"


def test_channel_of_keeps_an_unknown_bundle_id_because_it_is_still_a_selector() -> None:
    assert channel_of({"source_bundle_id": "com.example.app"}) == "com.example.app"


def test_channel_of_reads_the_camera_heuristic_the_same_way_the_sql_writes_it() -> None:
    """iOS only attributed the camera from ~2024-08; older originals have no app."""
    assert channel_of({"source_bundle_id": None, "filename": "IMG_1234.JPG"}) == "camera"
    # SQLite's LIKE is case-insensitive over ASCII, so this must be too.
    assert channel_of({"source_bundle_id": None, "filename": "img_1234.jpg"}) == "camera"
    # A screenshot with no source app is not a camera original.
    assert (
        channel_of(
            {"source_bundle_id": None, "filename": "IMG_1234.PNG", "subtypes": '["screenshot"]'}
        )
        == "unattributed"
    )
    assert channel_of({"source_bundle_id": None, "filename": "9F2C-8821.HEIC"}) == "unattributed"


CHANNEL_CASES = [
    {"identity_key": "a", "filename": "IMG_1.JPG", "source_bundle_id": "com.apple.camera"},
    {"identity_key": "b", "filename": "IMG_2.JPG", "source_bundle_id": None},
    {"identity_key": "c", "filename": "img_3.jpg", "source_bundle_id": None},
    {"identity_key": "d", "filename": "9F2C-8821.HEIC", "source_bundle_id": None},
    {
        "identity_key": "e",
        "filename": "IMG_5.PNG",
        "source_bundle_id": None,
        "subtypes": '["screenshot"]',
    },
    {"identity_key": "f", "filename": "WA0007.jpg", "source_bundle_id": "net.whatsapp.WhatsApp"},
    {"identity_key": "g", "filename": "IMG_7.PNG", "source_bundle_id": "com.apple.springboard"},
    {"identity_key": "h", "filename": "IMG_8.JPG", "source_bundle_id": "com.example.app"},
    # No filename and no source app. Without COALESCE in the SQL this asset
    # falls out of every channel and is filed where nothing can find it.
    {"identity_key": "i", "filename": None, "source_bundle_id": None},
]


def test_the_folder_an_asset_is_filed_in_is_the_folder_its_selector_finds(db) -> None:
    """The archive is laid out by channel_of and searched by --source.

    If those two ever disagree, an asset sits in camera/ that `--source camera`
    does not return, and the only symptom is photographs that appear to be
    missing. So this asserts the agreement against the real schema rather than
    against a second copy of the rule.
    """
    from iphone_image.db.database import utcnow

    db.conn.execute(
        "INSERT INTO devices (id, udid, name, product_kind, first_seen_at, last_seen_at, "
        "created_at, updated_at) VALUES (1, 'lib', 'lib', 'PhotosLibrary', ?, ?, ?, ?)",
        (utcnow(), utcnow(), utcnow(), utcnow()),
    )
    for case in CHANNEL_CASES:
        values = {
            "device_id": 1,
            "media_type": "PHOTO",
            "created_at_device": "2019-03-04T10:00:00+00:00",
            "size_bytes": 16,
            "subtypes": "[]",
            "present_on_phone": 1,
            "first_seen_at": utcnow(),
            "last_seen_at": utcnow(),
            "created_at": utcnow(),
            "updated_at": utcnow(),
            **case,
        }
        columns = ", ".join(values)
        db.conn.execute(
            f"INSERT INTO assets ({columns}) VALUES ({', '.join('?' for _ in values)})",
            list(values.values()),
        )

    rows = [dict(r) for r in db.conn.execute("SELECT * FROM assets").fetchall()]
    filed = {row["identity_key"]: channel_of(row) for row in rows}
    assert set(filed.values()) == {
        "camera",
        "unattributed",
        "whatsapp",
        "screenshot",
        "com.example.app",
    }

    for channel in sorted(set(filed.values())):
        where, params = Selector(source=channel).where()
        selected = {
            r["identity_key"]
            for r in db.conn.execute(
                f"SELECT identity_key FROM assets a WHERE {where}", params
            ).fetchall()
        }
        expected = {key for key, value in filed.items() if value == channel}
        assert selected == expected, (
            f"--source {channel} selects {sorted(selected)} but channel_of files "
            f"{sorted(expected)} there"
        )


# ---------------------------------------------------------------------------
# Proxies are backed up; the block is on removal
# ---------------------------------------------------------------------------


def test_a_backup_selection_includes_suspected_proxies() -> None:
    """docs/SAFETY.md section 2: they are archived normally.

    A proxy is the copy most likely to be the only one left if the original is
    ever lost, so leaving it out of the backup is the wrong way to protect it.
    """
    where, _ = Selector(source="camera").where()
    assert "proxy_suspicion" not in where


def test_removal_blocks_proxies_however_the_selector_was_built() -> None:
    """No flag may waive it: detection is heuristic and a miss is irreversible."""
    asked_for_them = Selector(source="camera", include_proxy_suspects=True)
    where, _ = asked_for_them.for_removal().where()
    assert f"a.proxy_suspicion < {PROXY_SUSPICION_BLOCK}" in where


def test_removal_protects_favourites_however_the_selector_was_built() -> None:
    where, _ = Selector(include_favourites=True).for_removal().where()
    assert "a.is_favourite = 0" in where


def test_for_removal_narrows_and_changes_nothing_else() -> None:
    original = Selector(source="whatsapp", media_type="photo", older_than="1y", order="largest")
    narrowed = original.for_removal()
    assert (narrowed.source, narrowed.media_type, narrowed.older_than, narrowed.order) == (
        "whatsapp",
        "photo",
        "1y",
        "largest",
    )
    assert original.include_proxy_suspects is True, "for_removal must not mutate its caller"


def test_the_scanner_flags_what_removal_blocks(db) -> None:
    """One threshold. Two constants would make "flagged" and "blocked" drift."""
    from iphone_image.db.database import utcnow
    from iphone_image.scanner import proxy_suspicion

    # A 4000x3000 JPEG at 409 KB, the real one from docs/SAFETY.md section 2.
    score, evidence = proxy_suspicion({"width": 4000, "height": 3000, "sizeBytes": 409_000})
    assert score >= PROXY_SUSPICION_BLOCK and evidence

    db.conn.execute(
        "INSERT INTO devices (id, udid, name, product_kind, first_seen_at, last_seen_at, "
        "created_at, updated_at) VALUES (1, 'lib', 'lib', 'PhotosLibrary', ?, ?, ?, ?)",
        (utcnow(), utcnow(), utcnow(), utcnow()),
    )
    db.conn.execute(
        "INSERT INTO assets (device_id, identity_key, filename, media_type, size_bytes, "
        "proxy_suspicion, present_on_phone, subtypes, first_seen_at, last_seen_at, "
        "created_at, updated_at) VALUES (1, 'p', 'IMG_1.JPG', 'PHOTO', 409000, ?, 1, '[]', "
        "?, ?, ?, ?)",
        (score, utcnow(), utcnow(), utcnow(), utcnow()),
    )

    backup, params = Selector(source="camera").where()
    assert (
        db.conn.execute(f"SELECT COUNT(*) FROM assets a WHERE {backup}", params).fetchone()[0] == 1
    )

    removal, params = Selector(source="camera").for_removal().where()
    assert (
        db.conn.execute(f"SELECT COUNT(*) FROM assets a WHERE {removal}", params).fetchone()[0] == 0
    )
