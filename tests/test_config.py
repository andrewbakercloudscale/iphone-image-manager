from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from iphone_image.config import Config, ConfigError, RemovalPolicy, load_config


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "c.yaml"
    path.write_text(body)
    return path


def test_defaults_keep_everything_on_the_phone() -> None:
    """The single most important default in the product."""
    assert Config().remove_from_iphone.policy is RemovalPolicy.NEVER


def test_defaults_are_safe() -> None:
    c = Config()
    assert c.safety.block_suspected_proxies is True
    assert c.safety.require_final_scan is True
    assert c.recycle_bin.enabled is True
    assert c.recycle_bin.use_macos_trash is True
    assert c.geolocation.reverse_geocode is False, "nothing should leave the Mac by default"
    assert c.cloud.enabled is False


def test_missing_file_falls_back_to_defaults() -> None:
    assert load_config().source_path is None


def test_named_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.yaml")


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("misspelled key", "retention:\n  screenshot: 60d\n"),
        ("unknown token", 'organization:\n  pattern: "{yr}/{month}"\n'),
        ("absolute pattern", 'organization:\n  pattern: "/{year}"\n'),
        ("cloud without remote", "cloud:\n  enabled: true\n  provider: google_drive\n"),
        ("cloud_verified without cloud", "remove_from_iphone:\n  policy: cloud_verified\n"),
        (
            "location mode without geo",
            'organization:\n  mode: location\n  pattern: "{country}"\n'
            "geolocation:\n  enabled: false\n",
        ),
        (
            "place token without geocoding",
            'organization:\n  mode: custom\n  pattern: "{city}/{year}"\n',
        ),
        ("proxy block disabled", "safety:\n  block_suspected_proxies: false\n"),
        ("final scan disabled", "safety:\n  require_final_scan: false\n"),
        ("near dupes enabled", "deduplication:\n  near_duplicates:\n    enabled: true\n"),
        ("bad duration", "retention:\n  screenshots: soonish\n"),
        ("unsupported version", "version: 2\n"),
        ("workers out of range", "performance:\n  hash_workers: 999\n"),
        ("batch limit zero", "remove_from_iphone:\n  default_batch_limit: 0\n"),
        ("not a mapping", "- a\n- b\n"),
        ("broken yaml", "archive:\n  local_path: [oops\n"),
    ],
)
def test_invalid_configs_are_rejected(tmp_path: Path, name: str, body: str) -> None:
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, body))


def test_error_message_names_the_offending_key(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as exc:
        load_config(write(tmp_path, "retention:\n  screenshot: 60d\n"))
    assert "retention.screenshot" in str(exc.value)


def test_a_valid_config_loads(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "version: 1\n"
        "retention: {screenshots: 60d, whatsapp: 30d}\n"
        "cloud: {enabled: true, provider: google_drive, remote: gdrive}\n",
    )
    c = load_config(path)
    assert c.retention.screenshots == timedelta(days=60)
    assert c.retention.whatsapp == timedelta(days=30)
    assert c.cloud.remote == "gdrive"
    assert c.source_path == path


def test_paths_are_expanded() -> None:
    c = Config()
    assert "~" not in str(c.archive.local_path)
    assert "~" not in str(c.database.path)


def test_durations_serialise_back_to_strings() -> None:
    dumped = Config(retention={"screenshots": "60d"}).model_dump(mode="json")
    assert dumped["retention"]["screenshots"] == "60d"
    assert dumped["retention"]["whatsapp"] == "never"
