"""Where backups go: the rclone account, and the archive folder on this Mac.

Accounts live in the user's rclone config; this tool only names one. An account
can hold backups only if it reports each file's SHA-256, because that hash is
the evidence removal from the phone rests on. Google Drive does. S3 reports MD5
(and not even that for multipart uploads), OneDrive and Dropbox their own
hashes, so those are listed but cannot be chosen until verification learns
their hash.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

#: rclone backend types whose listings carry a hash the ledger also records, and
#: which one. Drive and a local folder report SHA-256; S3 reports only MD5 (its
#: ETag, which rclone also stores as metadata on multipart uploads so large files
#: keep one). A type missing here cannot verify, so nothing is ever released or
#: removed against it.
HASH_TYPES = {"drive": "sha256", "local": "sha256", "s3": "md5"}


def hash_for(kind: str) -> str | None:
    """The ledger column whose value this backend's listing can be checked against."""
    return HASH_TYPES.get(kind)


KIND_TITLES = {
    "drive": "Google Drive",
    "s3": "S3 compatible",
    "onedrive": "OneDrive",
    "dropbox": "Dropbox",
    "box": "Box",
    "b2": "Backblaze B2",
    "local": "A folder on this Mac",
    "google photos": "Google Photos",
    "googlephotos": "Google Photos",
    "gphotos": "Google Photos",
}

#: rclone's Google Photos backend, by every name rclone accepts for it (its name,
#: file name and prefix). Never in HASH_TYPES: Google Photos re-encodes what it is
#: sent and reports no hash, so it can hold an extra copy but never a backup.
GOOGLE_PHOTOS_KINDS = frozenset({"google photos", "googlephotos", "gphotos"})


def is_google_photos(kind: str) -> bool:
    return kind.strip().lower() in GOOGLE_PHOTOS_KINDS


@dataclass(frozen=True)
class Account:
    name: str
    kind: str

    @property
    def title(self) -> str:
        return KIND_TITLES.get(self.kind, self.kind)

    @property
    def can_verify(self) -> bool:
        return self.kind in HASH_TYPES

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


def shared_client_id(remote: str, binary: str = "rclone") -> bool | None:
    """True when a Google Drive remote has no client_id of its own. It then signs
    in through rclone's one shared Google project, whose quota every rclone user
    draws on, and Drive answers with rateLimitExceeded. None when unknown."""
    try:
        out = subprocess.run(
            [binary, "config", "show", remote],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_shared_client_id(out)


def parse_shared_client_id(text: str) -> bool:
    """`rclone config show <remote>`: `key = value` lines."""
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "client_id" and value.strip():
            return False
    return True


def drive_folder_url(remote: str, folder: str, binary: str = "rclone") -> str | None:
    """The Google Drive web page of `folder` in `remote`, or None when it cannot be found.

    Drive addresses folders by id, not path, so the parent is listed for the folder's id.
    """
    path = PurePosixPath(folder.strip("/"))
    if not path.name:
        return "https://drive.google.com/drive/my-drive"
    parent = "" if str(path.parent) == "." else str(path.parent)
    try:
        out = subprocess.run(
            [binary, "lsjson", "--dirs-only", f"{remote.rstrip(':')}:{parent}"],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_folder_url(out, path.name)


def parse_folder_url(listing: str, name: str) -> str | None:
    """`rclone lsjson --dirs-only` of a Drive folder: the page of its child `name`."""
    try:
        entries = json.loads(listing)
    except ValueError:
        return None
    for e in entries if isinstance(entries, list) else []:
        if e.get("IsDir") and e.get("Name") == name and e.get("ID"):
            return f"https://drive.google.com/drive/folders/{e['ID']}"
    return None


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
