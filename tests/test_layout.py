"""Saving a folder layout edits only the lines it changes."""

from __future__ import annotations

import yaml

from iphone_image.layout import edit_organization

CONFIG = """archive:
  local_path: ~/Desktop/iphone

organization:
  mode: date                  # date | location | custom
  pattern: "{source}/{year}/{event}"
  flat_channels: [whatsapp]    # the owner's choice
  event_min_photos: 10

geolocation:
  enabled: true
"""


def test_changes_values_and_keeps_comments() -> None:
    out = edit_organization(
        CONFIG, {"event_min_photos": 20, "flat_channels": ["whatsapp", "screenshot"]}
    )
    assert "  flat_channels: [whatsapp, screenshot]    # the owner's choice\n" in out
    assert "  event_min_photos: 20\n" in out
    assert "# date | location | custom" in out
    assert yaml.safe_load(out)["geolocation"] == {"enabled": True}


def test_adds_missing_key_inside_the_block() -> None:
    out = edit_organization(CONFIG, {"event_gap_days": 30, "pattern": "{year}/{event}"})
    data = yaml.safe_load(out)
    assert data["organization"]["event_gap_days"] == 30
    assert data["organization"]["pattern"] == "{year}/{event}"
    assert out.index("event_gap_days") < out.index("geolocation:")


def test_adds_block_when_missing() -> None:
    out = edit_organization("archive:\n  local_path: x\n", {"flat_channels": []})
    assert yaml.safe_load(out)["organization"] == {"flat_channels": []}
