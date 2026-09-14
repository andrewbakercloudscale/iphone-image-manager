from __future__ import annotations

from datetime import UTC, datetime

import pytest

from iphone_image.selector import (
    Selector,
    SelectorError,
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


def test_proxy_suspects_are_excluded_by_default() -> None:
    """docs/SAFETY.md section 2: suspected proxies are never acted on silently."""
    sql, _ = Selector().where()
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
