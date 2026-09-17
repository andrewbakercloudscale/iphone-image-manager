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
    last_transfer_bytes: int | None = None

    def __init__(self, *, lose: set[str] | None = None, corrupt: set[str] | None = None) -> None:
        self.stored: dict[str, str] = {}
        self.lose = lose or set()
        self.corrupt = corrupt or set()
        self.checked = False
        self.calls: list[tuple[Path, int, str]] = []
        self.listed: list[str | None] = []

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

    def hashes(self, destination: str, subdir: str | None = None) -> dict[str, str]:
        """Scoped like the real thing, or the key space goes untested.

        rclone returns paths relative to whatever it was asked to list, so a
        fake that ignored `subdir` and handed back everything would hide a
        batch looking up `IMG_1.JPG` in a dict keyed `2019/11/IMG_1.JPG`.
        """
        self.listed.append(subdir)
        if subdir is None:
            return dict(self.stored)
        return {k: v for k, v in self.stored.items() if k.startswith(f"{subdir}/")}


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


def add_archived(
    config: Config,
    db: Database,
    key: str,
    relative: str,
    body: bytes = b"photo",
    media_type: str = "PHOTO",
):
    path = config.archive.local_path / "camera" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()
    db.conn.execute(
        "INSERT INTO assets (device_id, identity_key, filename, media_type, created_at_device, "
        "size_bytes, source_bundle_id, present_on_phone, subtypes, local_path, local_status, "
        "sha256, first_seen_at, last_seen_at, created_at, updated_at) "
        "VALUES (1, ?, ?, ?, '2019-11-04T10:00:00+00:00', ?, 'com.apple.camera', 1, '[]', "
        "?, 'LOCAL_VERIFIED', ?, ?, ?, ?, ?)",
        (
            key,
            Path(relative).name,
            media_type,
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


# ---------------------------------------------------------------------------
# Banking progress as it is earned
#
# The first real upload ran six hours, put 11,748 files on Drive, was killed by
# a wall-clock timeout before it reached verification, and recorded nothing at
# all. Six hours of genuine work read as zero because verification was one step
# at the very end. These tests are about what survives an interruption.
# ---------------------------------------------------------------------------


class FlakyCloud(FakeCloud):
    """A remote that stops accepting uploads partway through the run."""

    def __init__(self, *, fail_from: int, **kwargs) -> None:
        super().__init__(**kwargs)
        self.fail_from = fail_from

    def upload(self, root: Path, relatives: list[str], destination: str) -> None:
        if len(self.calls) >= self.fail_from:
            self.calls.append((root, len(relatives), destination))
            raise cloud_engine.CloudError("rclone copy said nothing for 900s and was killed")
        super().upload(root, relatives, destination)


def test_one_batch_per_folder(env) -> None:
    config, db = env
    add_archived(config, db, "a", "2019/11 Cape Town/IMG_1.JPG", body=b"1")
    add_archived(config, db, "b", "2019/11 Cape Town/IMG_2.JPG", body=b"2")
    add_archived(config, db, "c", "2019/12 Mossel Bay/IMG_3.JPG", body=b"3")

    fake = FakeCloud()
    result = cloud_engine.run(config, Selector(), provider=fake, db=db)

    assert result.batches == 2, "two folders, two batches"
    assert result.verified == 3
    assert fake.listed == ["2019/11 Cape Town", "2019/12 Mossel Bay"], (
        "each batch lists its own folder, never the whole archive"
    )


def test_an_interrupted_run_keeps_what_it_already_proved(env) -> None:
    """The failure that cost six hours, as a test.

    The remote takes the first folder and then refuses. What was verified
    before the refusal must still be in the ledger afterwards.
    """
    config, db = env
    add_archived(config, db, "a", "2019/11 Cape Town/IMG_1.JPG", body=b"1")
    add_archived(config, db, "b", "2019/12 Mossel Bay/IMG_2.JPG", body=b"2")
    add_archived(config, db, "c", "2020/01 Plett/IMG_3.JPG", body=b"3")

    result = cloud_engine.run(config, Selector(), provider=FlakyCloud(fail_from=1), db=db)

    assert result.verified == 1, "the first folder got through"
    assert result.batches_done == 1
    recorded = db.conn.execute(
        "SELECT COUNT(*) FROM assets WHERE cloud_status = 'CLOUD_VERIFIED'"
    ).fetchone()[0]
    assert recorded == 1, "and it is durable, not merely counted in memory"


def test_a_resumed_run_replans_and_skips_what_was_banked(env) -> None:
    """Which is what makes the interruption cheap rather than merely survivable."""
    config, db = env
    for i, folder in enumerate(["2019/11 Cape Town", "2019/12 Mossel Bay", "2020/01 Plett"]):
        add_archived(config, db, f"a{i}", f"{folder}/IMG_{i}.JPG", body=f"{i}".encode())

    first = cloud_engine.run(config, Selector(), provider=FlakyCloud(fail_from=1), db=db)
    assert first.verified == 1

    second = cloud_engine.run(config, Selector(), provider=FakeCloud(), db=db)
    assert second.planned == 2, "the banked folder is not replanned"
    assert second.verified == 2
    assert (
        db.conn.execute(
            "SELECT COUNT(*) FROM assets WHERE cloud_status = 'CLOUD_VERIFIED'"
        ).fetchone()[0]
        == 3
    )


def test_an_outage_stops_the_run_rather_than_failing_every_folder(env) -> None:
    """Counted, not diagnosed -- the rule `sync` already uses."""
    config, db = env
    for i in range(10):
        add_archived(config, db, f"a{i}", f"2019/{i:02d} Trip/IMG_{i}.JPG", body=f"{i}".encode())

    result = cloud_engine.run(config, Selector(), provider=FlakyCloud(fail_from=0), db=db)

    assert result.stopped_early, "an unavailable remote is an outage, not 10 bad folders"
    assert result.batches_done == 0
    assert "resumes where this one stopped" in result.stopped_early


def test_a_folder_missing_from_the_remote_is_not_an_error(tmp_path: Path) -> None:
    """The first upload creates it; a listing that blew up would fail its own batch."""
    binary = _fake_rclone(
        tmp_path,
        'echo "ERROR: directory not found" >&2\nexit 3\n',
    )
    provider = cloud_engine.RcloneProvider("gdrive", binary=binary)
    assert provider.hashes("Dest", "2019/11 Cape Town") == {}


def test_a_listing_asks_only_for_the_folder_it_is_checking(tmp_path: Path) -> None:
    """Re-listing 20,000 files per batch is what made this too dear to do often."""
    argv = tmp_path / "argv.txt"
    binary = _fake_rclone(tmp_path, f'printf "%s\\n" "$@" > {argv}\nprintf "[]"\nexit 0\n')
    provider = cloud_engine.RcloneProvider("gdrive", binary=binary)

    provider.hashes("Dest", "2019/11 Cape Town")
    assert "gdrive:Dest/2019/11 Cape Town" in argv.read_text().split("\n")


def test_a_wedged_transfer_is_killed_on_silence_not_on_the_clock(tmp_path: Path) -> None:
    """The distinction that cost the first upload.

    A wall clock cannot tell a slow transfer from a dead one, so a throttled
    but working upload was killed at 62%. Silence can: rclone is asked for
    stats every 30s and prints them even at 0 B/s.
    """
    binary = _fake_rclone(tmp_path, 'echo "NOTICE: 0 B / 10 GiB, 0%" >&2\nsleep 30\n')
    provider = cloud_engine.RcloneProvider("gdrive", binary=binary)

    with pytest.raises(cloud_engine.CloudError, match="said nothing for") as caught:
        provider._run(["copy", "x"], timeout=3600, stall_timeout=1)
    assert "0 B / 10 GiB" in str(caught.value)


def test_a_slow_transfer_that_keeps_talking_is_left_alone(tmp_path: Path) -> None:
    """The other half: being throttled is not being broken."""
    binary = _fake_rclone(
        tmp_path,
        "i=0\nwhile [ $i -lt 6 ]; do\n"
        '  echo "NOTICE: $i MiB / 10 GiB, 0%, 0.1 MiB/s" >&2\n'
        "  sleep 0.2\n  i=$((i+1))\ndone\nexit 0\n",
    )
    seen: list[str] = []
    provider = cloud_engine.RcloneProvider("gdrive", binary=binary, on_progress=seen.append)

    provider._run(["copy", "x"], timeout=3600, stall_timeout=1)
    assert len(seen) == 6, "it talked the whole way through and was not killed"


# ---------------------------------------------------------------------------
# Rates that measure what they claim to
#
# The first resumed run reported `banked 189 verified at 76.99 MB/s` for a
# folder where rclone sent nothing at all: the files were already on the remote
# and the figure was planned-size over elapsed-time. Entry 4 in the handover,
# for the third time.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("2026/09/17 09:12:46 NOTICE:  6.027 MiB / 60 MiB, 10%, 3.027 MiB/s, ETA 17s", 6319767),
        ("2026/09/17 09:12:46 NOTICE:         0 B / 0 B, -, 0 B/s, ETA -", 0),
        ("2026/09/17 09:12:46 NOTICE:  1.5 GiB / 60 GiB, 2%, 3 MiB/s, ETA 1h", 1610612736),
        ("ERROR: something went wrong", None),
        ("2026/09/17 NOTICE: 6.027 QiB / 60 QiB, 10%", None),
    ],
)
def test_transferred_bytes_reads_what_rclone_reports(line: str, expected: int | None) -> None:
    assert cloud_engine._transferred_bytes(line) == expected


def test_an_unreadable_stats_line_is_unknown_and_never_zero() -> None:
    """A format we cannot parse must say so.

    Calling it zero would report a confident 0 MB/s for a transfer that was
    working perfectly well -- a wrong number where no number was available.
    """
    assert cloud_engine._transferred_bytes("NOTICE: transferred everything") is None


def test_a_skipped_folder_does_not_invent_a_transfer_rate(env) -> None:
    """The 76.99 MB/s bug: bytes that never crossed the network."""
    config, db = env
    add_archived(config, db, "a", "2019/11 Cape Town/IMG_1.JPG", body=b"x" * 4096)

    fake = FakeCloud()
    fake.last_transfer_bytes = 0  # rclone skipped it: already on the remote
    result = cloud_engine.run(config, Selector(), provider=fake, db=db)

    assert result.verified == 1
    assert result.bytes_transferred == 0
    assert result.rate_mb_s == 0.0, "no bytes were sent, so there is no rate to report"


def test_the_rate_counts_only_batches_that_sent_something(env) -> None:
    """Otherwise a resumed run's many instant skips drag the figure to nothing."""
    config, db = env
    add_archived(config, db, "a", "2019/11 Cape Town/IMG_1.JPG", body=b"x" * 4096)

    fake = FakeCloud()
    fake.last_transfer_bytes = 10 * 1024 * 1024
    result = cloud_engine.run(config, Selector(), provider=fake, db=db)

    assert result.bytes_transferred == 10 * 1024 * 1024
    assert result.seconds_transferring > 0
    assert result.rate_mb_s > 0


# ---------------------------------------------------------------------------
# Two archives, and every verb agreeing about which is which
#
# The user's Drive keeps `Family Photos` and `Family Videos` as separate
# top-level archives. `cloud` writes to one, `release` and `remove-from-iphone`
# delete on the strength of finding the file there. If those can disagree, a
# video uploaded to one folder is looked for in the other and reported missing
# -- and in `remove-from-iphone` "missing" is what decides whether the phone
# keeps the only copy.
# ---------------------------------------------------------------------------

VIDEO_DEST = "Diskstation2/Family Videos"


def _two_archive_config(config: Config) -> Config:
    return config.model_copy(
        update={"cloud": config.cloud.model_copy(update={"video_destination": VIDEO_DEST})}
    )


def test_videos_go_to_the_video_archive_and_photos_do_not(env) -> None:
    config, db = env
    config = _two_archive_config(config)
    add_archived(config, db, "p", "2024/07 Plett/IMG_1.JPG", body=b"photo")
    add_archived(config, db, "v", "2024/07 Plett/IMG_2.MOV", body=b"video", media_type="VIDEO")

    uploads = {u.relative: u.destination for u in cloud_engine.plan(config, Selector(), db)}
    assert uploads["2024/07 Plett/IMG_1.JPG"] == config.cloud.destination
    assert uploads["2024/07 Plett/IMG_2.MOV"] == VIDEO_DEST


def test_each_archive_is_uploaded_and_verified_against_itself(env) -> None:
    """The batch key includes the destination, so the two never mix."""
    config, db = env
    config = _two_archive_config(config)
    add_archived(config, db, "p", "2024/07 Plett/IMG_1.JPG", body=b"photo")
    add_archived(config, db, "v", "2024/07 Plett/IMG_2.MOV", body=b"video", media_type="VIDEO")

    fake = FakeCloud()
    result = cloud_engine.run(config, Selector(), provider=fake, db=db)

    assert result.verified == 2
    assert result.batches == 2, "same folder, different archives, so two batches"
    paths = dict(db.conn.execute("SELECT filename, cloud_path FROM assets").fetchall())
    assert paths["IMG_1.JPG"].startswith(config.cloud.destination + "/")
    assert paths["IMG_2.MOV"].startswith(VIDEO_DEST + "/")


def test_a_recorded_path_resolves_to_the_archive_it_was_written_to(env) -> None:
    """What `release` and `remove` rely on, and the reason it reads the path
    rather than the config: config can be edited after the upload."""
    config, _db = env
    config = _two_archive_config(config)

    dest, rel = cloud_engine.split_cloud_path(config, f"{VIDEO_DEST}/2024/07 Plett/IMG_2.MOV")
    assert (dest, rel) == (VIDEO_DEST, "2024/07 Plett/IMG_2.MOV")

    dest, rel = cloud_engine.split_cloud_path(
        config, f"{config.cloud.destination}/2024/07 Plett/IMG_1.JPG"
    )
    assert (dest, rel) == (config.cloud.destination, "2024/07 Plett/IMG_1.JPG")


def test_one_destination_being_a_prefix_of_another_resolves_the_long_one(env) -> None:
    """ "Family Photos" and "Family Photos/Andrew iPhone Archive" both match by
    prefix; the shorter would hand back a relative path with a folder still
    glued to the front, and every hash lookup would miss."""
    config, _db = env
    config = config.model_copy(
        update={
            "cloud": config.cloud.model_copy(
                update={"destination": "Family Photos", "video_destination": "Family Photos/Videos"}
            )
        }
    )
    dest, rel = cloud_engine.split_cloud_path(config, "Family Photos/Videos/2024/IMG_2.MOV")
    assert dest == "Family Photos/Videos"
    assert rel == "2024/IMG_2.MOV"


def test_a_path_matching_no_configured_archive_is_not_silently_accepted(env) -> None:
    """It must fail the later hash lookup rather than resolve to something
    plausible: blocking is recoverable, deleting on a wrong match is not."""
    config, _db = env
    config = _two_archive_config(config)
    dest, rel = cloud_engine.split_cloud_path(config, "Somewhere Else/2024/IMG_9.JPG")
    assert rel == "Somewhere Else/2024/IMG_9.JPG", "kept whole, so no lookup can match it"
    assert dest == config.cloud.destination
