"""Where backups go: the rclone account, and the archive folder on this Mac.

Accounts live in the user's rclone config; this tool only names one. An account
can hold backups only if it reports each file's SHA-256, because that hash is
the evidence removal from the phone rests on. Google Drive does. S3 reports MD5
(and not even that for multipart uploads), OneDrive and Dropbox their own
hashes, so those are listed but cannot be chosen until verification learns
their hash.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

#: rclone backend types whose listings carry a SHA-256.
SHA256_TYPES = {"drive", "local"}

KIND_TITLES = {
    "drive": "Google Drive",
    "s3": "S3 compatible",
    "onedrive": "OneDrive",
    "dropbox": "Dropbox",
    "box": "Box",
    "b2": "Backblaze B2",
    "local": "A folder on this Mac",
}


@dataclass(frozen=True)
class Account:
    name: str
    kind: str

    @property
    def title(self) -> str:
        return KIND_TITLES.get(self.kind, self.kind)

    @property
    def can_verify(self) -> bool:
        return self.kind in SHA256_TYPES

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "title": self.title,
            "canVerify": self.can_verify,
        }


def parse_remotes(text: str) -> list[Account]:
    """`rclone listremotes --long`: one `name: type` per line."""
    found = []
    for line in text.splitlines():
        name, sep, kind = line.partition(":")
        if sep and name.strip():
            found.append(Account(name.strip(), kind.strip()))
    return found


def accounts(binary: str = "rclone") -> list[Account]:
    try:
        out = subprocess.run(
            [binary, "listremotes", "--long"],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_remotes(out)


def archive_problem(path: Path) -> str | None:
    """Why `path` cannot be the archive folder, or None."""
    if path.exists() and not path.is_dir():
        return f"{path} is a file, not a folder"
    probe = path if path.exists() else next((p for p in path.parents if p.exists()), None)
    if probe is None or not probe.is_dir():
        return f"no part of {path} exists"
    if not os.access(probe, os.W_OK):
        return f"{probe} cannot be written to"
    return None
