"""Choosing the archive's folder layout, and seeing it before saving it.

The layout is `organization` in config.yaml: the pattern, which channels are
filed flat, and when a place earns its own folder. A change applies to the next
backup only. Files already in the archive and on the remote stay where they
are; `relocate` moves local ones.

Saving edits only the lines it changes, so the owner's comments in the file
survive. Rewriting the file through YAML would drop every one of them.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Any

from .config import Config
from .db.database import Database
from .organize.events import assign
from .photos.places import read_places
from .sync import archive_path_for

#: Keys a layout save may write, per section, in the order they are added when missing.
KEYS = {
    "organization": (
        "pattern",
        "channel_patterns",
        "flat_channels",
        "event_min_photos",
        "event_gap_days",
    ),
    "cloud": (
        "remote",
        "destination",
        "video_destination",
        "screenshot_destination",
        "whatsapp_destination",
    ),
    "archive": ("local_path",),
}


def render_value(value: Any) -> str:
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, list):
        return "[" + ", ".join(value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}: {render_value(v)}" for k, v in value.items()) + "}"
    return str(value)


def edit_section(text: str, section: str, changes: dict[str, Any]) -> str:
    """config.yaml with `<section>.<key>` set for each change, comments kept."""
    lines = text.splitlines(keepends=True)
    start = next(
        (i for i, line in enumerate(lines) if re.match(rf"^{section}:\s*(#.*)?$", line)), None
    )
    if start is None:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"\n{section}:\n")
        start = len(lines) - 1
    end = start + 1
    indent = "  "
    while end < len(lines) and (lines[end].startswith((" ", "\t")) or not lines[end].strip()):
        m = re.match(r"^(\s+)\w", lines[end])
        if m:
            indent = m.group(1)
        end += 1
    # Trailing blank lines belong to the gap before the next section.
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1

    for key in KEYS[section]:
        if key not in changes:
            continue
        value = render_value(changes[key])
        pattern = re.compile(
            rf"^(\s+){key}:\s*(\"(?:[^\"\\\\]|\\\\.)*\"|'[^']*'|\[[^\]]*\]|\{{.*\}}|[^#\s]*)(\s*#.*)?$"
        )
        for i in range(start + 1, end):
            m = pattern.match(lines[i].rstrip("\n"))
            if m:
                comment = m.group(3) or ""
                lines[i] = f"{m.group(1)}{key}: {value}{comment}\n"
                break
        else:
            lines.insert(end, f"{indent}{key}: {value}\n")
            end += 1
    return "".join(lines)


def edit_organization(text: str, changes: dict[str, Any]) -> str:
    return edit_section(text, "organization", changes)


def preview(proposed: Config, db: Database) -> dict[str, Any]:
    """Where the archived assets would sit under the proposed configuration.

    Counts the same assets `sync.event_folders` clusters over, so a place folder
    shown here is one a backup would really make. Reads a copy of the Photos
    database when the pattern names {event}, which takes a minute.
    """
    rows = [
        dict(r)
        for r in db.conn.execute(
            "SELECT * FROM assets WHERE present_on_phone = 1 "
            "AND local_status IN ('LOCAL_VERIFIED', 'RELEASED')"
        ).fetchall()
    ]
    events: dict[str, str] = {}
    organization = proposed.organization
    if organization.uses("{event}"):
        places = read_places(proposed.photos.library_path)
        events, _ = assign(
            rows,
            places.by_uuid,
            gap=timedelta(days=organization.event_gap_days),
            min_photos=organization.event_min_photos,
        )
    root = proposed.archive.local_path
    folders: Counter[str] = Counter()
    for row in rows:
        folders[str(archive_path_for(proposed, row, events=events).relative_to(root))] += 1
    # {event} gives a month ("03") or a visit ("03 Cape Town"); only visits are place folders.
    place_folders = {
        f: n
        for f, n in folders.items()
        if "{event}" in organization.pattern_for(Path(f).parts[0])
        and f not in organization.flat_channels
        and not re.fullmatch(r"\d\d", Path(f).name)
    }
    return {
        "assets": len(rows),
        "folders": len(folders),
        "placeFolders": len(place_folders),
        "biggest": [{"folder": f, "count": n} for f, n in folders.most_common(8)],
        "places": [
            {"folder": f, "count": n}
            for f, n in sorted(place_folders.items(), key=lambda kv: -kv[1])[:12]
        ],
        "smallest": min(folders.values()) if folders else 0,
    }
