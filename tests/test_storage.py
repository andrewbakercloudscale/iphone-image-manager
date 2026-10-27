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


def test_a_drive_remote_without_its_own_client_id_is_on_the_shared_quota() -> None:
    from iphone_image.storage import parse_shared_client_id

    assert parse_shared_client_id("[gdrive]\ntype = drive\nscope = drive\ntoken = {}\n")
    assert parse_shared_client_id("[gdrive]\ntype = drive\nclient_id = \n")
    own = "[gdrive]\ntype = drive\nclient_id = 123.apps.googleusercontent.com\n"
    assert not parse_shared_client_id(own)


def test_a_drive_folder_is_opened_by_the_id_its_parent_lists() -> None:
    from iphone_image.storage import parse_folder_url

    listing = (
        '[{"Path":"2026","Name":"2026","IsDir":true,"ID":"a1"},\n'
        '{"Path":"screenshots","Name":"screenshots","IsDir":true,"ID":"b2"}]'
    )
    assert parse_folder_url(listing, "screenshots") == "https://drive.google.com/drive/folders/b2"
    assert parse_folder_url(listing, "missing") is None
    assert parse_folder_url("not json", "screenshots") is None
