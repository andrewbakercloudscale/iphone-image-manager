"""Archive path construction.

Every path segment here is derived from metadata that came off a device, which
is untrusted input. A filename or a reverse-geocoded city name must never be
able to escape the archive root, so sanitising happens per segment and the
result is always a relative path.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

#: Tokens a user may write in an organization pattern.
PATTERN_TOKENS: frozenset[str] = frozenset(
    {
        "year",
        "month",
        "day",
        "country",
        "region",
        "city",
        "suburb",
        "type",
        "device",
        "camera_make",
        "camera_model",
    }
)

#: What to use when the metadata for a token is missing. The specification names
#: "Unknown Date", "Unknown Location" and "Unknown City" explicitly.
FALLBACKS: dict[str, str] = {
    "year": "Unknown Date",
    "month": "Unknown Date",
    "day": "Unknown Date",
    "country": "Unknown Location",
    "region": "Unknown Location",
    "city": "Unknown City",
    "suburb": "Unknown City",
    "type": "Unknown Type",
    "device": "Unknown Device",
    "camera_make": "Unknown Camera",
    "camera_model": "Unknown Camera",
}

_TOKEN_RE = re.compile(r"\{([a-z_]+)\}")
_UNSAFE_RE = re.compile(r'[\x00-\x1f<>:"/\\|?*]')
_MAX_SEGMENT = 100


class PatternError(ValueError):
    """Raised when an organization pattern cannot be used."""


def validate_pattern(pattern: str) -> list[str]:
    """Return a list of problems with a pattern. Empty means it is usable."""
    problems: list[str] = []
    if not pattern or not pattern.strip():
        problems.append("pattern is empty")
        return problems
    if pattern.startswith("/"):
        problems.append("pattern must be relative, it cannot start with /")
    if "\\" in pattern:
        problems.append("pattern must use / as its separator, not \\")

    for token in _TOKEN_RE.findall(pattern):
        if token not in PATTERN_TOKENS:
            known = ", ".join(sorted(PATTERN_TOKENS))
            problems.append(f"unknown token {{{token}}}. Known tokens: {known}")

    for segment in pattern.split("/"):
        if segment in ("", ".", ".."):
            problems.append(f"pattern contains an invalid path segment: {segment!r}")

    if not _TOKEN_RE.search(pattern):
        problems.append("pattern contains no tokens, so every asset would land in one folder")

    return problems


def sanitize_segment(value: str, *, fallback: str = "Unknown") -> str:
    """Make one path segment safe to join under the archive root."""
    text = _UNSAFE_RE.sub("-", str(value)).strip()
    text = re.sub(r"\s+", " ", text)
    # Leading dots hide files; trailing dots and spaces are hostile on other
    # filesystems and survive a round trip through a sync tool badly.
    text = text.strip(". ")
    if text in ("", ".", ".."):
        return fallback
    if len(text) > _MAX_SEGMENT:
        text = text[:_MAX_SEGMENT].rstrip(". ")
    return text or fallback


def _render_segment(raw_segment: str, values: dict[str, str | None]) -> str:
    """Substitute the tokens in one path segment, applying fallbacks."""
    used_fallbacks: list[str] = []

    def substitute(match: re.Match[str]) -> str:
        token = match.group(1)
        value = values.get(token)
        if value is None or not str(value).strip():
            fallback = FALLBACKS.get(token, "Unknown")
            used_fallbacks.append(fallback)
            return fallback
        return str(value)

    rendered = _TOKEN_RE.sub(substitute, raw_segment)

    # "Unknown Date-Unknown Date" helps nobody. If every token in the segment
    # fell back to the same word, the segment is just that word.
    token_count = len(_TOKEN_RE.findall(raw_segment))
    if token_count and len(used_fallbacks) == token_count and len(set(used_fallbacks)) == 1:
        return used_fallbacks[0]
    return rendered


def render_pattern(pattern: str, values: dict[str, str | None]) -> PurePosixPath:
    """Turn a pattern plus metadata into a relative archive subpath."""
    problems = validate_pattern(pattern)
    if problems:
        raise PatternError("; ".join(problems))

    segments = [sanitize_segment(_render_segment(s, values)) for s in pattern.split("/")]

    # "{year}/{month}" with no date at all would otherwise give
    # "Unknown Date/Unknown Date". One unknown folder, not a nest of them.
    collapsed: list[str] = []
    fallback_words = set(FALLBACKS.values())
    for segment in segments:
        if collapsed and segment == collapsed[-1] and segment in fallback_words:
            continue
        collapsed.append(segment)

    return PurePosixPath(*collapsed)


def unique_filename(directory: Path, filename: str, content_hash: str) -> str:
    """A filename that does not collide with an unrelated file in `directory`.

    The suffix comes from the content hash, so two different assets that happen
    to share a filename get stable, distinct names rather than __1, __2 that
    shift between runs.

    Caller contract: only call this for an asset that has no archive path
    recorded yet. An asset already in the database uses its stored path, so a
    resumed sync overwrites nothing and creates no second copy. Assets with
    identical content share one archive file and never reach this function.
    """
    candidate = sanitize_segment(filename, fallback="unnamed")
    if not (directory / candidate).exists():
        return candidate

    stem, dot, ext = candidate.rpartition(".")
    if not dot:
        stem, ext = candidate, ""
    suffix = "." + ext if ext else ""

    digest = (content_hash or "").upper()
    for length in (8, 16, len(digest)):
        if length == 0:
            break
        probe = f"{stem}__{digest[:length]}{suffix}"
        if not (directory / probe).exists():
            return probe

    # Content hash exhausted, which means a genuinely different file is already
    # parked on every name we would choose. Count rather than overwrite.
    counter = 1
    while True:
        probe = f"{stem}__{digest[:8]}-{counter}{suffix}"
        if not (directory / probe).exists():
            return probe
        counter += 1
