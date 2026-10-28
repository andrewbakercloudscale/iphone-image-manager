"""Google Photos: an extra copy, sent only by request, never trusted.

The rules worth a test are the ones that keep it an *extra* copy: only what
Google Drive has verified is sent, Google Drive is only ever read, a file is
recorded as sent only when rclone said so, and nothing that releases or removes
anything ever looks at what this records.
"""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from iphone_image import google_photos as gp
from iphone_image.cli.main import cli
from iphone_image.config import Config, ConfigError, load_config
from iphone_image.db.database import Database, utcnow
from iphone_image.selector import Selector
from iphone_image.storage import HASH_TYPES, Account, is_google_photos, parse_remotes

DEST = "Family Photos/Andrew iPhone Archive"
SHOTS = f"{DEST}/screenshots"


class FakeSender:
    """Google Photos, faked at the rclone boundary. `calls` is the point of it."""

    def __init__(self, *, fail: set[str] | None = None, quota: str | None = None) -> None:
        self.calls: list[tuple[str, list[str], str]] = []
        self.fail = fail or set()
        self.quota = quota
        self.checked = False

    def check(self) -> None:
        self.checked = True

    def send(self, source: str, names: list[str], album: str, on_copied=None) -> gp.Sent:
        self.calls.append((source, list(names), album))
        if self.quota:
            return gp.Sent(error="429 Too Many Requests", quota=self.quota)
        done = {n for n in names if n not in self.fail}
        for n in sorted(done):
            if on_copied:
                on_copied(n)
        return gp.Sent(names=done, error="boom" if self.fail & set(names) else None)


def make_config(tmp_path: Path, **google_photos: Any) -> Config:
    return Config(
        archive={"local_path": str(tmp_path / "archive")},
        database={"path": str(tmp_path / "db.sqlite")},
        logging={"path": str(tmp_path / "logs")},
        photos={"library_path": str(tmp_path / "lib.photoslibrary")},
        cloud={
            "enabled": True,
            "provider": "google_drive",
            "remote": "gdrive",
            "destination": DEST,
            "screenshot_destination": SHOTS,
        },
        google_photos={"remote": "gphotos", **google_photos},
    )


@pytest.fixture
def env(tmp_path: Path):
    config = make_config(tmp_path)
    db = Database(config.database.path).connect()
    db.migrate()
    db.conn.execute(
        "INSERT INTO devices (id, udid, name, first_seen_at, last_seen_at, created_at, updated_at) "
        "VALUES (1, 'lib', 'lib', ?, ?, ?, ?)",
        (utcnow(), utcnow(), utcnow(), utcnow()),
    )
    yield config, db
    db.close()


def backed_up(
    config: Config,
    db: Database,
    key: str,
    relative: str,
    *,
    body: bytes = b"photo",
    cloud: bool = True,
    on_mac: bool = True,
    on_phone: bool = True,
    bundle: str = "com.apple.camera",
    dest: str = DEST,
    gphotos: str | None = None,
    channel: str = "camera",
) -> int:
    path = config.archive.local_path / channel / relative
    if on_mac:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    cursor = db.conn.execute(
        "INSERT INTO assets (device_id, identity_key, filename, media_type, created_at_device, "
        "size_bytes, source_bundle_id, present_on_phone, subtypes, local_path, local_status, "
        "sha256, cloud_status, cloud_path, gphotos_status, "
        "first_seen_at, last_seen_at, created_at, updated_at) "
        "VALUES (1, ?, ?, 'PHOTO', '2019-11-04T10:00:00+00:00', ?, ?, ?, '[]', ?, ?, ?, ?, ?, ?, "
        "?, ?, ?, ?)",
        (
            key,
            Path(relative).name,
            len(body),
            bundle,
            1 if on_phone else 0,
            str(path) if on_mac else None,
            "LOCAL_VERIFIED" if on_mac else "RELEASED",
            hashlib.sha256(body).hexdigest(),
            "CLOUD_VERIFIED" if cloud else "NONE",
            f"{dest}/{relative}" if cloud else None,
            gphotos,
            utcnow(),
            utcnow(),
            utcnow(),
            utcnow(),
        ),
    )
    return int(cursor.lastrowid or 0)


def status_of(db: Database, asset_id: int) -> tuple[str | None, str | None]:
    row = db.conn.execute(
        "SELECT gphotos_status, gphotos_album FROM assets WHERE id = ?", (asset_id,)
    ).fetchone()
    return row[0], row[1]


# -- configuration and ledger -----------------------------------------------


def test_defaults_send_nothing() -> None:
    c = Config().google_photos
    assert (c.remote, c.sources, c.album) == ("", [], "Photo Sentinel")


@pytest.mark.parametrize(
    "body",
    [
        "google_photos: {sources: [nonsense]}\n",
        'google_photos: {album: ""}\n',
        "google_photos: {albums: x}\n",
        'cloud: {remote: "g"}\ngoogle_photos: {remote: "g"}\n',
    ],
)
def test_bad_google_photos_settings_are_refused(tmp_path: Path, body: str) -> None:
    path = tmp_path / "c.yaml"
    path.write_text("version: 1\n" + body)
    with pytest.raises(ConfigError):
        load_config(path)


def test_the_migration_adds_the_columns(db: Database) -> None:
    columns = {r["name"] for r in db.conn.execute("PRAGMA table_info(assets)")}
    assert {"gphotos_status", "gphotos_album", "gphotos_at"} <= columns
    indexes = {r["name"] for r in db.conn.execute("PRAGMA index_list(assets)")}
    assert "idx_assets_gphotos_status" in indexes


def test_google_photos_can_never_be_the_backup_account() -> None:
    accounts = parse_remotes("gdrive: drive\ngphotos: google photos\n")
    photos = next(a for a in accounts if a.name == "gphotos")
    assert is_google_photos(photos.kind)
    assert photos.title == "Google Photos"
    assert not photos.can_verify
    assert not any(is_google_photos(k) for k in HASH_TYPES)
    assert is_google_photos("googlephotos") and is_google_photos("gphotos")
    assert Account("gdrive", "drive").title != "Google Photos"


def test_albums_are_named_per_source_and_per_folder() -> None:
    assert gp.album_for("Photo Sentinel", source="camera") == "Photo Sentinel \u2013 Camera"
    assert gp.album_for("Photo Sentinel", folder=f"{DEST}/2019/") == (
        "Photo Sentinel \u2013 Andrew iPhone Archive \u2013 2019"
    )
    assert "/" not in gp.album_for("a/b", folder="x/y/z")


# -- what a send selects ------------------------------------------------------


def test_only_what_google_drive_verified_and_not_yet_sent(env) -> None:
    config, db = env
    verified = backed_up(config, db, "a", "2019/IMG_1.HEIC")
    backed_up(config, db, "b", "2019/IMG_2.HEIC", cloud=False)
    backed_up(config, db, "c", "2019/IMG_3.HEIC", gphotos=gp.SENT)
    items = gp.plan(config, db, Selector())
    assert [i.asset_id for i in items] == [verified]


def test_being_off_the_phone_does_not_matter(env) -> None:
    config, db = env
    gone = backed_up(config, db, "a", "2019/IMG_1.HEIC", on_phone=False, on_mac=False)
    assert [i.asset_id for i in gp.plan(config, db, Selector())] == [gone]


def test_a_folder_is_a_path_prefix(env) -> None:
    config, db = env
    inside = backed_up(config, db, "a", "2019/03/IMG_1.HEIC")
    deeper = backed_up(config, db, "b", "2019/11/IMG_2.HEIC")
    backed_up(config, db, "c", "20190/IMG_3.HEIC")
    backed_up(config, db, "d", "2020/IMG_4.HEIC")
    items = gp.plan(config, db, Selector(), folder=f"{DEST}/2019/")
    assert sorted(i.asset_id for i in items) == [inside, deeper]
    assert {i.album for i in items} == {"Photo Sentinel \u2013 Andrew iPhone Archive \u2013 2019"}


def test_following_keeps_only_the_chosen_sources(tmp_path: Path, env) -> None:
    _, db = env
    config = make_config(tmp_path, sources=["screenshot"])
    backed_up(config, db, "a", "2019/IMG_1.HEIC")
    shot = backed_up(
        config, db, "b", "2019/s.PNG", bundle="com.apple.springboard", dest=SHOTS,
        channel="screenshot",
    )
    items = gp.plan(config, db, Selector(), following=True)
    assert [i.asset_id for i in items] == [shot]
    assert items[0].album == "Photo Sentinel \u2013 Screenshots"
    assert len(gp.plan(config, db, Selector())) == 2


def test_only_on_mac_leaves_released_items_for_a_click(env) -> None:
    config, db = env
    here = backed_up(config, db, "a", "2019/IMG_1.HEIC")
    backed_up(config, db, "b", "2019/IMG_2.HEIC", on_mac=False)
    assert [i.asset_id for i in gp.plan(config, db, Selector(), only_on_mac=True)] == [here]


# -- sending ------------------------------------------------------------------


def test_sent_is_recorded_only_for_what_rclone_reported(env) -> None:
    config, db = env
    ok = backed_up(config, db, "a", "2019/IMG_1.HEIC")
    bad = backed_up(config, db, "b", "2019/IMG_2_fail.HEIC", body=b"other")
    sender = FakeSender(fail={"IMG_2_fail.HEIC"})
    result = gp.send(config, db, gp.plan(config, db, Selector()), sender=sender)
    assert sender.checked
    assert (result.sent, result.failed) == (1, 1)
    assert status_of(db, ok) == (gp.SENT, "Photo Sentinel \u2013 Camera")
    assert status_of(db, bad) == (None, None)
    # The failure is not sent again as a success; the next plan offers it again.
    assert [i.asset_id for i in gp.plan(config, db, Selector())] == [bad]


def test_the_mac_copy_is_used_only_while_it_matches_its_hash(env) -> None:
    config, db = env
    backed_up(config, db, "a", "2019/IMG_1.HEIC")
    changed = backed_up(config, db, "b", "2020/IMG_2.HEIC")
    released = backed_up(config, db, "c", "2021/IMG_3.HEIC", on_mac=False)
    (config.archive.local_path / "camera/2020/IMG_2.HEIC").write_bytes(b"edited on disk")
    sender = FakeSender()
    result = gp.send(config, db, gp.plan(config, db, Selector()), sender=sender)
    sources = {s for s, _names, _album in sender.calls}
    assert str(config.archive.local_path / "camera/2019") in sources
    assert f"gdrive:{DEST}/2020" in sources
    assert f"gdrive:{DEST}/2021" in sources
    assert (result.from_mac, result.from_drive) == (1, 2)
    assert status_of(db, changed)[0] == status_of(db, released)[0] == gp.SENT


def test_a_quota_refusal_stops_the_run_and_records_nothing(env) -> None:
    config, db = env
    a = backed_up(config, db, "a", "2019/IMG_1.HEIC")
    backed_up(config, db, "b", "2020/IMG_2.HEIC")
    sender = FakeSender(quota="Google Photos asked to slow down.")
    result = gp.send(config, db, gp.plan(config, db, Selector()), sender=sender)
    assert len(sender.calls) == 1
    assert result.stopped == "Google Photos asked to slow down."
    assert status_of(db, a) == (None, None)


def test_progress_counts_files_as_rclone_copies_them(env) -> None:
    config, db = env
    backed_up(config, db, "a", "2019/IMG_1.HEIC")
    backed_up(config, db, "b", "2019/IMG_2.HEIC", body=b"two")
    heard: list[dict[str, Any]] = []
    items = gp.plan(config, db, Selector())
    gp.send(config, db, items, sender=FakeSender(), on_progress=heard.append)
    assert heard[0] == {**heard[0], "phase": "send", "done": 0, "total": 2}
    assert [h.get("inFlight") for h in heard if "inFlight" in h] == [1, 2]
    assert heard[-1]["done"] == 2


def test_rclone_log_lines_decide_what_was_sent() -> None:
    lines = [
        json.dumps({"level": "info", "msg": "Copied (new)", "object": "a.jpg"}),
        json.dumps({"level": "info", "msg": "Copied (new)", "object": "b.jpg"}),
        json.dumps({"level": "error", "msg": "Failed to copy: x", "object": "b.jpg"}),
        json.dumps({"level": "notice", "msg": "1 MiB / 2 MiB, 50%"}),
        "not json",
    ]
    sent = gp.parse_log(lines)
    assert sent.names == {"a.jpg"}
    assert sent.error == "Failed to copy: x"
    assert sent.quota is None
    quota = gp.parse_log(
        [json.dumps({"level": "error", "msg": "Quota exceeded for quota metric 'Write requests'"})]
    )
    assert quota.quota and "slow down" in quota.quota
    full = gp.parse_log([], "rclone copy exited 1: storage quota has been exceeded")
    assert full.quota and "storage is full" in full.quota


# -- the real rclone command, against a fake binary ---------------------------

FAKE_RCLONE = """#!/bin/bash
printf '%s\\n' "$@" >> "{log}"; echo --- >> "{log}"
if [ "$1" = listremotes ]; then printf 'gdrive: drive\\ngphotos: google photos\\n'; exit 0; fi
if [ "$1" = copy ]; then
  while [ $# -gt 0 ]; do [ "$1" = --files-from-raw ] && list=$2; shift; done
  while IFS= read -r n || [ -n "$n" ]; do
    case "$n" in
      *fail*) printf '{{"level":"error","msg":"Failed to copy: boom","object":"%s"}}\\n' "$n" >&2 ;;
      *) printf '{{"level":"info","msg":"Copied (new)","object":"%s"}}\\n' "$n" >&2 ;;
    esac
  done < "$list"
  exit 0
fi
exit 1
"""


@pytest.fixture
def fake_rclone(tmp_path: Path) -> tuple[Path, Path]:
    log = tmp_path / "rclone-calls.txt"
    binary = tmp_path / "rclone"
    binary.write_text(FAKE_RCLONE.format(log=log))
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    return binary, log


def calls(log: Path) -> list[list[str]]:
    return [c.strip("\n").split("\n") for c in log.read_text().split("---\n") if c.strip()]


def test_google_drive_is_only_ever_read(env, fake_rclone) -> None:
    config, db = env
    binary, log = fake_rclone
    backed_up(config, db, "a", "2019/IMG_1.HEIC", on_mac=False)
    backed_up(config, db, "b", "2019/IMG_2_fail.HEIC", on_mac=False, body=b"x")
    backed_up(config, db, "c", "2020/IMG_3.HEIC")
    sender = gp.RcloneGooglePhotos("gphotos", binary=str(binary))
    result = gp.send(config, db, gp.plan(config, db, Selector()), sender=sender)
    assert (result.sent, result.failed) == (2, 1)

    made = calls(log)
    assert made[0][0] == "listremotes"
    copies = [c for c in made if c[0] != "listremotes"]
    assert copies and all(c[0] == "copy" for c in copies)
    forbidden = {"move", "moveto", "sync", "delete", "deletefile", "purge", "rmdir", "rmdirs"}
    for c in copies:
        assert not forbidden & set(c)
        # Google Drive appears only as the source; the destination is always Google Photos.
        assert c[2].startswith("gphotos:album/Photo Sentinel")
        assert not any(arg.startswith("gdrive:") for arg in c[2:])
        assert "--no-check-dest" in c
        assert "--use-json-log" in c
    assert {c[1] for c in copies} == {
        f"gdrive:{DEST}/2019",
        str(config.archive.local_path / "camera/2020"),
    }


def test_a_remote_that_is_not_google_photos_is_refused(fake_rclone) -> None:
    binary, _ = fake_rclone
    with pytest.raises(gp.GooglePhotosError):
        gp.RcloneGooglePhotos("gdrive", binary=str(binary)).check()
    with pytest.raises(gp.GooglePhotosError):
        gp.RcloneGooglePhotos("nope", binary=str(binary)).check()
    gp.RcloneGooglePhotos("gphotos", binary=str(binary)).check()


# -- what the app reads ---------------------------------------------------------


def test_per_source_counts(tmp_path: Path, env) -> None:
    _, db = env
    config = make_config(tmp_path, sources=["camera"])
    backed_up(config, db, "a", "2019/IMG_1.HEIC", gphotos=gp.SENT)
    backed_up(config, db, "b", "2019/IMG_2.HEIC", body=b"longer")
    backed_up(config, db, "c", "2019/IMG_3.HEIC", cloud=False)
    camera = next(s for s in gp.source_counts(config, db) if s["source"] == "camera")
    assert camera == {
        "source": "camera",
        "title": "Camera",
        "following": True,
        "sent": 1,
        "sentBytes": 5,
        "notSent": 1,
        "notSentBytes": 6,
    }


def test_folders_count_what_send_folder_would_select(env) -> None:
    config, db = env
    backed_up(config, db, "a", "2019/03/IMG_1.HEIC", gphotos=gp.SENT)
    backed_up(config, db, "b", "2019/11/IMG_2.HEIC")
    backed_up(
        config, db, "c", "2019/s.PNG", bundle="com.apple.springboard", dest=SHOTS,
        channel="screenshot",
    )
    found = {f["path"]: f for f in gp.folders(config, db)}
    assert found[f"{DEST}/2019"]["count"] == 2
    assert found[f"{DEST}/2019/03"]["sent"] == 1
    # The photo root holds the nested screenshot folder, as `--folder` would select it.
    assert found[DEST]["count"] == 3
    assert found[DEST]["notSent"] == len(gp.plan(config, db, Selector(), folder=DEST))
    assert found[SHOTS]["relative"] == "" and found[f"{SHOTS}/2019"]["depth"] == 1


# -- the CLI ------------------------------------------------------------------


@pytest.fixture
def run(tmp_path: Path, env, monkeypatch):
    config, _ = env
    path = tmp_path / "config.yaml"
    path.write_text(
        "version: 1\n"
        f'archive: {{local_path: "{config.archive.local_path}"}}\n'
        f'database: {{path: "{config.database.path}"}}\n'
        f'logging: {{path: "{tmp_path}/logs"}}\n'
        f'cloud: {{enabled: true, provider: google_drive, remote: gdrive, destination: "{DEST}"}}\n'
        "\n"
        "google_photos:\n"
        '  remote: "gphotos"   # the owner\'s note\n'
    )
    sender = FakeSender()
    monkeypatch.setattr(gp, "RcloneGooglePhotos", lambda *a, **k: sender)
    runner = CliRunner()

    def invoke(*args: str):
        return runner.invoke(cli, ["--config", str(path), *args], catch_exceptions=False)

    invoke.sender = sender  # type: ignore[attr-defined]
    invoke.path = path  # type: ignore[attr-defined]
    return invoke


def test_without_apply_nothing_is_sent(run, env) -> None:
    config, db = env
    a = backed_up(config, db, "a", "2019/IMG_1.HEIC")
    out = run("--json", "google-photos", "send", "--source", "camera")
    assert out.exit_code == 0
    data = json.loads(out.output)
    assert (data["applied"], data["planned"], data["plannedBytes"]) == (False, 1, 5)
    assert data["albums"] == [{"album": "Photo Sentinel \u2013 Camera", "count": 1, "bytes": 5}]
    assert run.sender.calls == []
    assert status_of(db, a) == (None, None)


def test_apply_sends_and_records(run, env) -> None:
    config, db = env
    a = backed_up(config, db, "a", "2019/IMG_1.HEIC")
    out = run("--json", "google-photos", "send", "--folder", f"{DEST}/2019", "--apply")
    assert out.exit_code == 0, out.output
    assert json.loads(out.output)["sent"] == 1
    assert status_of(db, a)[0] == gp.SENT


def test_following_never_fails_the_backup(run, env) -> None:
    config, db = env
    backed_up(config, db, "a", "2019/IMG_fail.HEIC")
    run("config", "google-photos", "--follow", "camera")
    run.sender.fail = {"IMG_fail.HEIC"}
    out = run("--json", "google-photos", "send", "--following", "--apply")
    assert out.exit_code == 0
    assert json.loads(out.output)["failed"] == 1
    assert run("--json", "google-photos", "send", "--apply").exit_code == 1


def test_following_sends_nothing_when_not_set_up(tmp_path: Path, config_file: Path) -> None:
    out = CliRunner().invoke(
        cli, ["--config", str(config_file), "--json", "google-photos", "send", "--following"]
    )
    assert out.exit_code == 0
    assert json.loads(out.output)["planned"] == 0


def test_following_a_source_is_saved_with_comments_kept(run) -> None:
    out = run("--json", "config", "google-photos", "--follow", "camera", "--follow", "whatsapp")
    assert json.loads(out.output) == {
        "remote": "gphotos",
        "sources": ["camera", "whatsapp"],
        "album": "Photo Sentinel",
        "applied": True,
    }
    run("config", "google-photos", "--unfollow", "camera")
    text = run.path.read_text()
    assert "the owner's note" in text
    assert load_config(run.path).google_photos.sources == ["whatsapp"]
    assert run("config", "google-photos", "--follow", "nonsense").exit_code == 1


def test_status_reports_per_source(run, env, monkeypatch) -> None:
    from iphone_image import storage

    config, db = env
    backed_up(config, db, "a", "2019/IMG_1.HEIC", gphotos=gp.SENT)
    monkeypatch.setattr(
        storage, "accounts", lambda: parse_remotes("gdrive: drive\ngphotos: google photos\n")
    )
    data = json.loads(run("--json", "google-photos", "status").output)
    assert data["configured"] is True
    assert data["account"]["title"] == "Google Photos"
    assert data["sent"] == 1
    assert [s["source"] for s in data["sources"]][:2] == ["camera", "whatsapp"]


def test_following_survives_an_unexpected_error(run, env) -> None:
    config, db = env
    backed_up(config, db, "a", "2019/IMG_1.HEIC")
    run("config", "google-photos", "--follow", "camera")

    def boom(*_a: Any, **_k: Any) -> gp.Sent:
        raise RuntimeError("rclone vanished")

    run.sender.send = boom
    out = run("--json", "google-photos", "send", "--following", "--apply")
    assert out.exit_code == 0
    assert "rclone vanished" in json.loads(out.output)["skipped"]
    with pytest.raises(RuntimeError):
        run("--json", "google-photos", "send", "--apply")
