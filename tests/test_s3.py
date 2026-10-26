"""S3 is checked by MD5, the only hash its listings carry; Drive stays on SHA-256."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from test_cloud import FakeCloud, _fake_rclone, add_archived, env  # noqa: F401

from iphone_image import cloud as cloud_engine
from iphone_image.selector import Selector


class FakeS3(FakeCloud):
    """Reports MD5 and nothing else, like `rclone lsjson --hash` against S3."""

    hash_name = "md5"

    def upload(self, root: Path, relatives: list[str], destination: str) -> None:
        self.calls.append((root, len(relatives), destination))
        for relative in relatives:
            if relative in self.lose:
                continue
            digest = hashlib.md5((root / relative).read_bytes()).hexdigest()
            self.stored[relative] = "0" * 32 if relative in self.corrupt else digest


def _remotes(tmp_path: Path, listing: str, lsjson: str = "[]") -> str:
    return _fake_rclone(
        tmp_path,
        f'if [ "$1" = listremotes ]; then printf "{listing}"; else echo \'{lsjson}\'; fi\nexit 0\n',
    )


def test_an_s3_remote_is_checked_by_md5(tmp_path: Path) -> None:
    rows = [{"Path": "2019/IMG_1.JPG", "Hashes": {"md5": "ab" * 16}}]
    provider = cloud_engine.RcloneProvider(
        "bucket", binary=_remotes(tmp_path, "gdrive: drive\\nbucket: s3\\n", json.dumps(rows))
    )
    provider.check()
    assert provider.hash_name == "md5"
    assert provider.hashes("Dest") == {"2019/IMG_1.JPG": "ab" * 16}


def test_drive_is_still_checked_by_sha256(tmp_path: Path) -> None:
    rows = [{"Path": "IMG_1.JPG", "Hashes": {"sha256": "cd" * 32, "md5": "ab" * 16}}]
    provider = cloud_engine.RcloneProvider(
        "gdrive", binary=_remotes(tmp_path, "gdrive: drive\\n", json.dumps(rows))
    )
    provider.check()
    assert provider.hash_name == "sha256"
    assert provider.hashes("Dest") == {"IMG_1.JPG": "cd" * 32}


def test_a_remote_with_no_usable_hash_is_refused(tmp_path: Path) -> None:
    provider = cloud_engine.RcloneProvider("od", binary=_remotes(tmp_path, "od: onedrive\\n"))
    with pytest.raises(cloud_engine.CloudError, match="no hash"):
        provider.check()


def test_an_upload_to_s3_records_md5_and_verifies_by_it(env) -> None:  # noqa: F811
    config, db = env
    add_archived(config, db, "a", "2019/11/IMG_1.JPG", b"photo one")
    result = cloud_engine.run(config, Selector(), provider=FakeS3(), db=db)
    assert (result.verified, result.failed) == (1, 0)
    row = db.conn.execute("SELECT cloud_status, md5 FROM assets").fetchone()
    assert row["cloud_status"] == "CLOUD_VERIFIED"
    assert row["md5"] == hashlib.md5(b"photo one").hexdigest()


def test_an_s3_copy_that_does_not_match_is_not_verified(env) -> None:  # noqa: F811
    config, db = env
    add_archived(config, db, "a", "2019/11/IMG_1.JPG", b"photo one")
    result = cloud_engine.run(
        config, Selector(), provider=FakeS3(corrupt={"2019/11/IMG_1.JPG"}), db=db
    )
    assert (result.verified, result.failed) == (0, 1)
    assert db.conn.execute("SELECT cloud_status FROM assets").fetchone()[0] != "CLOUD_VERIFIED"


def test_no_md5_never_matches(env) -> None:  # noqa: F811
    assert cloud_engine.recorded_digest({"sha256": "x", "md5": None}, "md5") is None
    assert cloud_engine.recorded_digest({"sha256": "x"}, "sha256") == "x"
