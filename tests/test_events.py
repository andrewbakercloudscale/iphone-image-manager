"""Event folders: "08-09 Pringle Bay"."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from iphone_image.organize.events import assign, month_folder
from iphone_image.photos.places import tidy


def shots(place_key: str, start: str, n: int, *, every_days: int = 1) -> list[dict]:
    begin = datetime.fromisoformat(start).replace(tzinfo=UTC)
    return [
        {
            "identity_key": f"{place_key}-{i}",
            "created_at_device": (begin + timedelta(days=i * every_days)).isoformat(),
        }
        for i in range(n)
    ]


def places_for(assets: list[dict], name: str) -> dict[str, str | None]:
    return dict.fromkeys((a["identity_key"] for a in assets), name)


def test_a_visit_becomes_a_named_folder() -> None:
    assets = shots("a", "2019-08-28", 12)
    folders, events = assign(assets, places_for(assets, "Pringle Bay"))
    assert len(events) == 1
    assert events[0].folder == "08-09 Pringle Bay"
    assert set(folders.values()) == {"08-09 Pringle Bay"}


def test_a_visit_inside_one_month_is_not_given_a_range() -> None:
    assets = shots("a", "2019-08-02", 12)
    _, events = assign(assets, places_for(assets, "Pringle Bay"))
    assert events[0].folder == "08 Pringle Bay"


def test_a_cluster_never_crosses_a_year() -> None:
    """Home photos have no gap, so an unbounded run merged five years into
    one folder called "09-06 Home". The folder lives under a year."""
    assets = shots("a", "2019-09-20", 400, every_days=4)  # spans into 2023
    _, events = assign(assets, places_for(assets, "Home"))
    years = {e.year for e in events}
    assert len(years) > 1, "it must split per year"
    for event in events:
        assert event.start.year == event.end.year == event.year


def test_a_return_visit_is_a_separate_folder() -> None:
    """Cape Town in March and again in November is two trips."""
    march = shots("m", "2021-03-01", 12)
    november = shots("n", "2021-11-01", 12)
    assets = march + november
    _, events = assign(assets, places_for(assets, "Cape Town"))
    assert sorted(e.folder for e in events) == ["03 Cape Town", "11 Cape Town"]


def test_a_place_below_the_minimum_falls_back_to_its_month() -> None:
    """One photo taken in passing is not a trip, and a folder holding it is
    worse than no folder: 46 of them appeared before the threshold counted
    the right set of assets."""
    assets = shots("a", "2019-11-04", 3)
    folders, events = assign(assets, places_for(assets, "London"), min_photos=10)
    assert events == []
    assert set(folders.values()) == {"11"}


def test_an_asset_with_no_place_gets_its_month() -> None:
    assets = shots("a", "2019-11-04", 3)
    folders, events = assign(assets, dict.fromkeys((a["identity_key"] for a in assets), None))
    assert events == []
    assert set(folders.values()) == {"11"}


def test_month_buckets_sort_beside_the_named_folders() -> None:
    """The reason months beat an "Unsorted" folder: the tree stays chronological."""
    named = shots("a", "2019-11-01", 12)
    loose = shots("b", "2019-12-04", 2)
    places: dict[str, str | None] = places_for(named, "Cape Town")
    places.update(dict.fromkeys((a["identity_key"] for a in loose), None))
    folders, _ = assign(named + loose, places)
    assert sorted(set(folders.values())) == ["11 Cape Town", "12"]


def test_an_asset_with_no_date_is_skipped_rather_than_guessed() -> None:
    folders, events = assign([{"identity_key": "x", "created_at_device": None}], {})
    assert folders == {} and events == []


def test_month_folder_is_zero_padded() -> None:
    assert month_folder(datetime(2019, 3, 4, tzinfo=UTC)) == "03"


def test_place_names_that_differ_only_in_punctuation_become_one_folder() -> None:
    """Photos writes both "Constantia & Hout Bay" and "Constantia - Hout Bay",
    which split one visit into folders of 19 and 23."""
    assert tidy("Constantia & Hout\xa0Bay") == tidy("Constantia \u2013 Hout Bay")
    assert tidy("Constantia & Hout\xa0Bay") == "Constantia and Hout Bay"
