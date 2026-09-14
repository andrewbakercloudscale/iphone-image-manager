from __future__ import annotations

from pathlib import Path

import pytest

from iphone_image.organize.paths import (
    PatternError,
    render_pattern,
    sanitize_segment,
    unique_filename,
    validate_pattern,
)

FULL = {"year": "2026", "month": "09", "country": "South Africa", "city": "Cape Town"}


@pytest.mark.parametrize(
    ("pattern", "values", "expected"),
    [
        ("{year}/{month}", FULL, "2026/09"),
        ("{country}/{city}/{year}/{month}", FULL, "South Africa/Cape Town/2026/09"),
        ("{year}/{country}/{city}/{month}", FULL, "2026/South Africa/Cape Town/09"),
        ("{year}-{month}", FULL, "2026-09"),
    ],
)
def test_renders_known_patterns(pattern: str, values: dict, expected: str) -> None:
    assert str(render_pattern(pattern, values)) == expected


def test_missing_date_collapses_to_one_unknown_folder() -> None:
    """Not Unknown Date/Unknown Date."""
    assert str(render_pattern("{year}/{month}", {})) == "Unknown Date"


def test_missing_location_uses_the_documented_names() -> None:
    assert str(render_pattern("{country}/{city}/{year}", {"year": "2026"})) == (
        "Unknown Location/Unknown City/2026"
    )


@pytest.mark.parametrize(
    "pattern",
    ["/{year}", "{year}/../{month}", "{nope}/{year}", "static/path", "", "{year}\\{month}"],
)
def test_rejects_bad_patterns(pattern: str) -> None:
    assert validate_pattern(pattern)
    with pytest.raises(PatternError):
        render_pattern(pattern, FULL)


@pytest.mark.parametrize(
    "hostile",
    ["../../../etc", "..", ".", "", "   ", "a/b/c", "C:evil", "name\x00null", "....."],
)
def test_metadata_cannot_escape_the_archive(hostile: str) -> None:
    """Metadata comes off a device and is untrusted input."""
    segment = sanitize_segment(hostile)
    assert "/" not in segment
    assert "\\" not in segment
    assert segment not in ("", ".", "..")
    assert "\x00" not in segment


def test_traversal_in_a_rendered_path_is_contained() -> None:
    rendered = render_pattern("{country}/{city}", {"country": "../../../..", "city": "etc"})
    assert ".." not in rendered.parts


def test_segments_are_length_capped() -> None:
    assert len(sanitize_segment("x" * 500)) <= 100


def test_unique_filename_leaves_a_free_name_alone(tmp_path: Path) -> None:
    assert unique_filename(tmp_path, "IMG_1234.HEIC", "abc123") == "IMG_1234.HEIC"


def test_unique_filename_suffixes_on_collision(tmp_path: Path) -> None:
    (tmp_path / "IMG_1234.HEIC").touch()
    name = unique_filename(tmp_path, "IMG_1234.HEIC", "a1b2c3d4e5f6")
    assert name == "IMG_1234__A1B2C3D4.HEIC"
    assert not (tmp_path / name).exists(), "must never name an existing file"


def test_unique_filename_never_returns_an_existing_path(tmp_path: Path) -> None:
    """The whole point: never overwrite an unrelated file."""
    digest = "a1b2c3d4e5f6a7b8"
    for _ in range(6):
        name = unique_filename(tmp_path, "IMG_1234.HEIC", digest)
        assert not (tmp_path / name).exists()
        (tmp_path / name).touch()


def test_unique_filename_handles_no_extension(tmp_path: Path) -> None:
    (tmp_path / "Makefile").touch()
    assert unique_filename(tmp_path, "Makefile", "deadbeef") == "Makefile__DEADBEEF"
