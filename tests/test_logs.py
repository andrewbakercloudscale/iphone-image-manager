from __future__ import annotations

import logging
from pathlib import Path

import pytest

from iphone_image.config import Config
from iphone_image.logs import LOGGER_NAME, get_logger, setup_logging


@pytest.fixture(autouse=True)
def _clean_handlers():
    yield
    logging.getLogger(LOGGER_NAME).handlers.clear()


def configure(tmp_path: Path, level: str = "debug") -> Path:
    config = Config(logging={"level": level, "path": str(tmp_path / "logs")})
    target = setup_logging(config, command="test")
    assert target is not None
    return target


def read(target: Path) -> str:
    for handler in logging.getLogger(LOGGER_NAME).handlers:
        handler.flush()
    return target.read_text()


def test_a_log_file_is_actually_written(tmp_path: Path) -> None:
    """The config carried these settings for a while and controlled nothing."""
    target = configure(tmp_path)
    get_logger("test").info("hello from the tool")
    assert "hello from the tool" in read(target)


def test_the_configured_level_is_honoured(tmp_path: Path) -> None:
    target = configure(tmp_path, level="warning")
    get_logger("t").info("should not appear")
    get_logger("t").warning("should appear")
    contents = read(target)
    assert "should not appear" not in contents
    assert "should appear" in contents


def test_the_command_is_recorded(tmp_path: Path) -> None:
    """Spec section 44 asks for the command on every line."""
    target = configure(tmp_path)
    get_logger("t").info("anything")
    assert "test" in read(target)


@pytest.mark.parametrize(
    "message",
    [
        "connecting with token=ya29.SUPERSECRET",
        "password: hunter2",
        "api_key = NOT-A-REAL-KEY-0000",
        "Authorization: Bearer abc.def.ghi",
        'rclone said {"token": "abc123XYZ"}',
        "callback?access_token=zzz&state=1",
        "refresh_token=rrrrr",
    ],
)
def test_credentials_never_reach_the_log(tmp_path: Path, message: str) -> None:
    """Spec section 46: authentication secrets must never be written."""
    target = configure(tmp_path)
    get_logger("cloud").info(message)
    contents = read(target)
    assert "[redacted]" in contents
    for secret in (
        "ya29.SUPERSECRET",
        "hunter2",
        "NOT-A-REAL-KEY-0000",
        "abc.def.ghi",
        "abc123XYZ",
        "zzz",
        "rrrrr",
    ):
        assert secret not in contents


def test_ordinary_messages_are_not_mangled(tmp_path: Path) -> None:
    target = configure(tmp_path)
    get_logger("t").info("scanned 1,234 assets in 5.6s from /Users/me/Pictures")
    assert "scanned 1,234 assets in 5.6s from /Users/me/Pictures" in read(target)


def test_an_unwritable_log_path_does_not_stop_the_tool(tmp_path: Path) -> None:
    """Losing logs is bad. Refusing to run because of it is worse."""
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    blocked.chmod(0o500)
    try:
        config = Config(logging={"path": str(blocked / "logs")})
        assert setup_logging(config, command="test") is None
        get_logger("t").info("this must not raise")
    finally:
        blocked.chmod(0o700)


def test_setup_is_idempotent(tmp_path: Path) -> None:
    """Repeated setup must not duplicate every line."""
    target = configure(tmp_path)
    configure(tmp_path)
    get_logger("t").info("once")
    assert read(target).count("once") == 1


def test_rotation_is_bounded(tmp_path: Path) -> None:
    configure(tmp_path)
    handlers = [
        h
        for h in logging.getLogger(LOGGER_NAME).handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert handlers, "no rotating file handler was installed"
    assert handlers[0].maxBytes > 0
    assert handlers[0].backupCount > 0
