"""Mirroring to the cloud, and refusing to believe it happened."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from iphone_image import cloud as cloud_engine
from iphone_image.config import Config
from iphone_image.db.database import Database, utcnow
from iphone_image.selector import Selector

MB = 1024 * 1024


class FakeCloud:
    """A remote that can be told to lose or corrupt what it was given."""

    name = "fake"

    def __init__(self, *, lose: set[str] | None = None, corrupt: set[str] | None = None) -> None:
        self.stored: dict[str, str] = {}
        self.lose = lose or set()
        self.corrupt = corrupt or set()
        self.checked = False
        self.calls: list[tuple[Path, int, str]] = []

    def check(self) -> None:
        self.checked = True

    def upload(self, root: Path, relatives: list[str], destination: str) -> None:
        self.calls.append((root, len(relatives), destination))
        for relative in relatives:
            if relative in self.lose:
                continue
            data = (root / relative).read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if relative in self.corrupt:
                digest = "0" * 64
            self.stored[relative] = digest

    def hashes(self, destination: str) -> dict[str, str]:
        return dict(self.stored)


@pytest.fixture
def env(tmp_path: Path):
    config = Config(
        archive={"local_path": str(tmp_path / "archive")},
        database={"path": str(tmp_path / "db.sqlite")},
        logging={"path": str(tmp_path / "logs")},
        photos={"library_path": str(tmp_path / "lib.photoslibrary")},
        cloud={
            "enabled": True,
            "provider": "google_drive",
            "remote": "gdrive",
            "destination": "Family Photos/Andrew iPhone Archive",
        },
    )
    db = Database(config.database.path).connect()
    db.migrate()
    db.conn.execute(
        "INSERT INTO devices (id, udid, name, first_seen_at, last_seen_at, created_at, updated_at) "
        "VALUES (1, 'lib', 'lib', ?, ?, ?, ?)",
        (utcnow(), utcnow(), utcnow(), utcnow()),
    )
    return config, db


def add_archived(config: Config, db: Database, key: str, relative: str, body: bytes = b"photo"):
    path = config.archive.local_path / "camera" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()
    db.conn.execute(
        "INSERT INTO assets (device_id, identity_key, filename, media_type, created_at_device, "
        "size_bytes, source_bundle_id, present_on_phone, subtypes, local_path, local_status, "
        "sha256, first_seen_at, last_seen_at, created_at, updated_at) "
        "VALUES (1, ?, ?, 'PHOTO', '2019-11-04T10:00:00+00:00', ?, 'com.apple.camera', 1, '[]', "
        "?, 'LOCAL_VERIFIED', ?, ?, ?, ?, ?)",
        (
            key,
            Path(relative).name,
            len(body),
            str(path),
            digest,
            utcnow(),
            utcnow(),
            utcnow(),
            utcnow(),
        ),
    )
    return digest


def test_the_remote_layout_drops_the_channel_level(env) -> None:
    """Locally camera/2019/..., in Drive 2019/... under the destination."""
    config, db = env
    add_archived(config, db, "a", "2019/11-12 Cape Town/IMG_1.JPG")
    uploads = cloud_engine.plan(config, Selector(), db)
    assert [u.relative for u in uploads] == ["2019/11-12 Cape Town/IMG_1.JPG"]


def test_a_verified_upload_is_recorded_with_where_it_went(env) -> None:
    config, db = env
    add_archived(config, db, "a", "2019/11 Cape Town/IMG_1.JPG")
    fake = FakeCloud()

    result = cloud_engine.run(config, Selector(), provider=fake, db=db)

    assert fake.checked, "a provider must be checked before it is trusted"
    assert (result.uploaded, result.verified, result.failed) == (1, 1, 0)
    row = db.conn.execute("SELECT cloud_status, cloud_path FROM assets").fetchone()
    assert row["cloud_status"] == "CLOUD_VERIFIED"
    assert row["cloud_path"] == "Family Photos/Andrew iPhone Archive/2019/11 Cape Town/IMG_1.JPG"


def test_a_file_the_remote_does_not_have_is_never_marked_verified(env) -> None:
    """The failure that would matter: the local copy is released on this record."""
    config, db = env
    add_archived(config, db, "a", "2019/11 Cape Town/IMG_1.JPG")
    fake = FakeCloud(lose={"2019/11 Cape Town/IMG_1.JPG"})

    result = cloud_engine.run(config, Selector(), provider=fake, db=db)

    assert result.verified == 0 and result.failed == 1
    assert "not found" in result.failures[0]["error"]
    # The schema defaults this to 'NONE', so the meaningful assertion is that it
    # is not verified -- 'is None' would have passed for the wrong reason.
    status = db.conn.execute("SELECT cloud_status FROM assets").fetchone()["cloud_status"]
    assert status != "CLOUD_VERIFIED"


def test_a_hash_that_does_not_match_is_a_failure_not_a_pass(env) -> None:
    config, db = env
    add_archived(config, db, "a", "2019/11 Cape Town/IMG_1.JPG")
    fake = FakeCloud(corrupt={"2019/11 Cape Token/IMG_1.JPG"} | {"2019/11 Cape Town/IMG_1.JPG"})

    result = cloud_engine.run(config, Selector(), provider=fake, db=db)

    assert result.verified == 0 and result.failed == 1
    assert "hash mismatch" in result.failures[0]["error"]


def test_an_upload_that_exits_zero_is_not_evidence(env) -> None:
    """rclone returning 0 proves it ran, not that the bytes arrived."""
    config, db = env
    for i in range(3):
        add_archived(config, db, f"a{i}", f"2019/11 Cape Town/IMG_{i}.JPG", body=f"x{i}".encode())
    fake = FakeCloud(lose={"2019/11 Cape Town/IMG_1.JPG"})

    result = cloud_engine.run(config, Selector(), provider=fake, db=db)

    assert result.uploaded == 3, "the transfer reported three"
    assert result.verified == 2, "but only two are actually there"
    assert result.failed == 1


def test_already_verified_assets_are_not_uploaded_again(env) -> None:
    config, db = env
    add_archived(config, db, "a", "2019/11 Cape Town/IMG_1.JPG")
    cloud_engine.run(config, Selector(), provider=FakeCloud(), db=db)

    second = FakeCloud()
    result = cloud_engine.run(config, Selector(), provider=second, db=db)

    assert result.planned == 0
    assert second.calls == [], "a resumed run must not re-send what is verified"


def test_cloud_disabled_refuses_rather_than_doing_nothing_quietly(env) -> None:
    config, db = env
    config = config.model_copy(update={"cloud": config.cloud.model_copy(update={"enabled": False})})
    with pytest.raises(cloud_engine.CloudError, match="enabled"):
        cloud_engine.run(config, Selector(), provider=FakeCloud(), db=db)


def test_an_asset_with_no_hash_is_skipped_rather_than_uploaded_unverifiable(env) -> None:
    config, db = env
    add_archived(config, db, "a", "2019/11 Cape Town/IMG_1.JPG")
    db.conn.execute("UPDATE assets SET sha256 = NULL")
    assert cloud_engine.plan(config, Selector(), db) == []


def test_limit_is_honoured(env) -> None:
    """The CLI offers --limit for every verb; one that ignored it silently
    would be worse than one that did not offer it."""
    config, db = env
    for i in range(5):
        add_archived(config, db, f"a{i}", f"2019/11/IMG_{i}.JPG", body=f"x{i}".encode())
    assert len(cloud_engine.plan(config, Selector(limit=2), db)) == 2
    assert len(cloud_engine.plan(config, Selector(), db)) == 5


# ---------------------------------------------------------------------------
# What rclone tells us while it is working
#
# The first real upload wrote 228 bytes to its logfile and then nothing for
# hours: rclone prints no progress unless asked, and stderr was captured rather
# than streamed, so nothing could have appeared before the run ended anyway. A
# log that looks identical whether a transfer is moving or dead is the failure
# this project keeps meeting, so these tests hold both halves of the fix.
# ---------------------------------------------------------------------------


def _fake_rclone(path: Path, body: str) -> str:
    """A stand-in for the binary. Tests drive its output, not a real remote."""
    script = path / "rclone"
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(0o755)
    return str(script)


def test_upload_asks_for_the_stats_rclone_will_not_print_otherwise(tmp_path: Path) -> None:
    """All three flags, and --stats-log-level is the one easily left out.

    Measured against rclone 1.74.1: --stats with --stats-one-line prints
    nothing at all on its own, because stats are logged at INFO while the
    default log level is NOTICE. Dropping it would restore the silent log
    while looking like progress had been asked for.
    """
    argv = tmp_path / "argv.txt"
    binary = _fake_rclone(tmp_path, f'printf "%s\\n" "$@" > {argv}\nexit 0\n')
    (tmp_path / "src").mkdir()

    provider = cloud_engine.RcloneProvider("gdrive", binary=binary)
    provider.upload(tmp_path / "src", ["a/IMG_1.JPG"], "Dest")

    args = argv.read_text().split("\n")
    assert "--stats" in args
    assert "--stats-one-line" in args
    assert "--stats-log-level" in args
    assert args[args.index("--stats-log-level") + 1] == "NOTICE"
    assert "--progress" not in args, "redraws with control codes; unreadable in a logfile"


def test_progress_arrives_while_rclone_is_still_running(tmp_path: Path) -> None:
    """The test cannot pass if stderr is only handed over at exit.

    The fake prints one line and then waits for the callback to have seen it,
    failing if it never does. Under `capture_output` the callback cannot run
    until the process has exited, so the wait times out and the run fails.
    """
    seen: list[str] = []
    sentinel = tmp_path / "seen.flag"
    binary = _fake_rclone(
        tmp_path,
        'echo "NOTICE: 1 MiB / 10 MiB, 10%" >&2\n'
        "i=0\n"
        f'while [ ! -f "{sentinel}" ]; do\n'
        "  i=$((i+1))\n"
        '  [ "$i" -gt 100 ] && exit 9\n'
        "  sleep 0.05\n"
        "done\n"
        "exit 0\n",
    )
    (tmp_path / "src").mkdir()

    def note(line: str) -> None:
        seen.append(line)
        sentinel.write_text("")

    provider = cloud_engine.RcloneProvider("gdrive", binary=binary, on_progress=note)
    provider.upload(tmp_path / "src", ["a/IMG_1.JPG"], "Dest")

    assert seen == ["NOTICE: 1 MiB / 10 MiB, 10%"]


def test_a_failing_rclone_still_reports_what_it_said(tmp_path: Path) -> None:
    """Streaming stderr must not cost us the diagnostic on a non-zero exit."""
    binary = _fake_rclone(
        tmp_path,
        'echo "ERROR: IMG_1.JPG: quota exceeded" >&2\nexit 3\n',
    )
    (tmp_path / "src").mkdir()

    provider = cloud_engine.RcloneProvider("gdrive", binary=binary)
    with pytest.raises(cloud_engine.CloudError, match="quota exceeded") as caught:
        provider.upload(tmp_path / "src", ["a/IMG_1.JPG"], "Dest")
    assert "exited 3" in str(caught.value)


def test_hours_of_stats_do_not_crowd_out_the_error(tmp_path: Path) -> None:
    """Only the tail is kept, and the last thing said is what went wrong."""
    binary = _fake_rclone(
        tmp_path,
        'i=0\nwhile [ $i -lt 400 ]; do echo "NOTICE: stats $i" >&2; i=$((i+1)); done\n'
        'echo "ERROR: giving up" >&2\nexit 1\n',
    )
    (tmp_path / "src").mkdir()

    provider = cloud_engine.RcloneProvider("gdrive", binary=binary)
    with pytest.raises(cloud_engine.CloudError, match="giving up"):
        provider.upload(tmp_path / "src", ["a/IMG_1.JPG"], "Dest")


def test_a_large_listing_does_not_deadlock(tmp_path: Path) -> None:
    """20,000 remote files is megabytes of JSON on stdout.

    A pipe nobody drains fills at 64 KB and blocks the child forever, which is
    why stdout goes to a file. This is the size at which that stops being
    theoretical.
    """
    rows = [
        {"Path": f"2019/11/IMG_{i}.JPG", "Hashes": {"sha256": f"{i:064x}"}} for i in range(20_000)
    ]
    listing = tmp_path / "listing.json"
    listing.write_text(json.dumps(rows))
    assert listing.stat().st_size > 1_000_000, "not big enough to prove anything"

    binary = _fake_rclone(tmp_path, f"cat {listing}\nexit 0\n")
    provider = cloud_engine.RcloneProvider("gdrive", binary=binary)

    found = provider.hashes("Dest")
    assert len(found) == 20_000
    assert found["2019/11/IMG_7.JPG"] == f"{7:064x}"


def test_a_hung_rclone_is_killed_and_says_so(tmp_path: Path) -> None:
    """A transfer that stops responding must not wait forever in silence."""
    binary = _fake_rclone(tmp_path, 'echo "NOTICE: 0 B / 10 GiB, 0%" >&2\nsleep 30\n')
    provider = cloud_engine.RcloneProvider("gdrive", binary=binary)

    with pytest.raises(cloud_engine.CloudError, match="did not finish") as caught:
        provider._run(["lsjson", "gdrive:Dest"], timeout=1)
    assert "0 B / 10 GiB" in str(caught.value), "say what it was last doing"
