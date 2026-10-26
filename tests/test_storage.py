"""Choosing the backup account and the archive folder."""

from __future__ import annotations

from pathlib import Path

import yaml

from iphone_image.layout import edit_section
from iphone_image.storage import archive_problem, parse_remotes


def test_only_accounts_whose_listings_carry_a_recorded_hash_can_hold_backups() -> None:
    found = parse_remotes("gdrive: drive\nwasabi: s3\n\nod: onedrive\n")
    assert [(a.name, a.title, a.can_verify) for a in found] == [
        ("gdrive", "Google Drive", True),
        ("wasabi", "S3 compatible", True),
        ("od", "OneDrive", False),
    ]


def test_remote_and_archive_are_saved_with_comments_kept() -> None:
    text = 'archive:\n  local_path: "~/Desktop/iphone"\n\ncloud:\n  remote: "gdrive"   # rclone\n'
    out = edit_section(
        edit_section(text, "cloud", {"remote": "work"}),
        "archive",
        {"local_path": "/Volumes/T7/iphone"},
    )
    assert '  remote: "work"   # rclone\n' in out
    assert yaml.safe_load(out) == {
        "archive": {"local_path": "/Volumes/T7/iphone"},
        "cloud": {"remote": "work"},
    }


def test_an_archive_folder_must_be_a_writable_folder(tmp_path: Path) -> None:
    assert archive_problem(tmp_path / "new" / "archive") is None
    (tmp_path / "file").write_text("x")
    assert "is a file" in (archive_problem(tmp_path / "file") or "")
