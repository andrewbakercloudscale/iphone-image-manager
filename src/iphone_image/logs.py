"""File logging.

The configuration has carried `logging.level` and `logging.path` since P1 and
nothing read either of them, so a user could set them, watch `config validate`
pass, and get no logs. That is the failure this project keeps warning about: a
setting that reads as configured and controls nothing.

Spec section 44 asks that log lines carry the command, device, asset, operation,
result, error, retry count and duration. The structured side of that lives in the
`operations` table; this is the narrative side, and the two are joined by the
operation id.

Spec section 44 also says authentication secrets must never be written. That is
enforced here by a filter rather than by remembering, because remembering fails.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
from pathlib import Path

from .config import Config

LOGGER_NAME = "iphone_image"

#: Patterns whose *value* must never reach a log file. Matched case-insensitively
#: against the formatted message.
_SECRET_PATTERNS = [
    # An auth header carries a scheme word before the credential
    # ("Bearer abc.def.ghi"), so redacting only the first token after the colon
    # leaves the credential itself in the file. Partial redaction is worse than
    # none, because it reads as handled. Take the rest of the line.
    re.compile(r"((?:proxy-)?authorization\s*[:=]\s*).*", re.IGNORECASE),
    re.compile(r"((?:bearer|basic)\s+)\S+", re.IGNORECASE),
    # Quoted JSON values, such as an rclone config blob.
    re.compile(
        r"""(["'](?:token|access_token|refresh_token|client_secret|password)["']"""
        r"""\s*:\s*["'])[^"']+""",
        re.IGNORECASE,
    ),
    # key=value and key: value, stopping at a delimiter so surrounding text lives.
    re.compile(
        r"((?:token|secret|password|passwd|pwd|api[_-]?key|access_token"
        r"""|refresh_token|client_secret)\s*[:=]\s*)[^\s&,;"']+""",
        re.IGNORECASE,
    ),
]

_REDACTED = "[redacted]"


class RedactSecrets(logging.Filter):
    """Strip anything that looks like a credential before it is written."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = message
        for pattern in _SECRET_PATTERNS:
            cleaned = pattern.sub(lambda m: m.group(1) + _REDACTED, cleaned)
        if cleaned != message:
            record.msg = cleaned
            record.args = ()
        return True


class FileOnly(logging.Filter):
    """Drops records marked for the file, so the console says a thing once.

    The last-resort handler prints its own sentence to the user; without this
    the same error would also arrive via the console log handler.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return not getattr(record, "file_only", False)


class ContextFilter(logging.Filter):
    """Carry the running command onto every record, per spec section 44."""

    def __init__(self, command: str) -> None:
        super().__init__()
        self.command = command

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "command"):
            record.command = self.command
        return True


FORMAT = "%(asctime)s %(levelname)-7s %(command)s %(name)s: %(message)s"


class NoTraceback(logging.Formatter):
    """Console formatter that keeps the message and drops the stack trace.

    The trace belongs in the log file, where it can be read at leisure. Dumping
    it at the user is the wall of text the last-resort handler exists to avoid.
    """

    def format(self, record: logging.LogRecord) -> str:
        exc_info, exc_text, stack = record.exc_info, record.exc_text, record.stack_info
        record.exc_info = record.exc_text = record.stack_info = None
        try:
            return super().format(record)
        finally:
            record.exc_info, record.exc_text, record.stack_info = exc_info, exc_text, stack


def setup_logging(config: Config, *, command: str = "-", verbose: bool = False) -> Path | None:
    """Configure file logging. Returns the log file, or None if it is unusable.

    A logging failure must never stop the tool: losing logs is bad, refusing to
    run because logs cannot be written is worse. The caller is told instead.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG if verbose else getattr(logging, config.logging.level.upper()))
    logger.propagate = False

    redact = RedactSecrets()
    context = ContextFilter(command)

    # Warnings and above also go to stderr, so a problem is visible without
    # anyone having to know a log file exists.
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.WARNING)
    console.setFormatter(NoTraceback("%(levelname)s: %(message)s"))
    console.addFilter(redact)
    console.addFilter(context)
    console.addFilter(FileOnly())
    logger.addHandler(console)

    try:
        directory = config.logging.path
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "iphone-image.log"
        handler = logging.handlers.RotatingFileHandler(
            target, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter(FORMAT))
        handler.addFilter(redact)
        handler.addFilter(context)
        logger.addHandler(handler)
    except OSError as exc:
        logger.warning("file logging is unavailable at %s: %s", config.logging.path, exc)
        return None

    logger.debug("logging started, level=%s", config.logging.level)
    return target


def get_logger(name: str = "") -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)
