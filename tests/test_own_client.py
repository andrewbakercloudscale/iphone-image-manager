"""Signing Google Drive in with the owner's own client: the account changes only
when the new sign-in reaches the same Google Drive, and is put back otherwise."""

from __future__ import annotations

import json
import subprocess

import pytest

from iphone_image import storage
from iphone_image.storage import OwnClientError, own_client, parse_authorize

CLIENT = "123-abc.apps.googleusercontent.com"
TOKEN = '{"access_token":"new","token_type":"Bearer","refresh_token":"r","expiry":"x"}'
FOLDERS = [{"ID": "f1", "Name": "iPhone Archive", "IsDir": True}, {"ID": "f2", "Name": "Docs"}]


class FakeRclone:
    """rclone as far as own_client sees it: a config file, a Drive, a browser sign-in."""

    def __init__(
        self,
        conf,
        *,
        running=False,
        new_drive=FOLDERS,
        old_drive=FOLDERS,
        signs_in=True,
        update_works=True,
    ):
        self.conf = conf
        self.running = running
        self.new_drive = new_drive
        self.old_drive = old_drive
        self.signs_in = signs_in
        self.update_works = update_works
        self.calls: list[list[str]] = []

    def __call__(self, args, capture_output=True, text=True, timeout=None, env=None):
        self.calls.append(args)
        if args[0] == "pgrep":
            return self._done(0 if self.running else 1)
        cmd = args[1]
        if cmd == "config" and args[2] == "show":
            return self._done(0, self.conf.read_text().split("\n", 1)[1])
        if cmd == "config" and args[2] == "file":
            return self._done(0, f"Configuration file is stored at:\n{self.conf}\n")
        if cmd == "config" and args[2] == "update":
            if self.update_works:
                self.conf.write_text(self.conf.read_text() + f"client_id = {CLIENT}\n")
            else:
                self.conf.write_text("[gdrive]\ntype = drive\nbroken = yes\n")
            return self._done(0)
        if cmd == "authorize":
            if not self.signs_in:
                return self._done(1, "", "Failed to get token: invalid_client")
            return self._done(
                0, f"Paste the following into your remote machine --->\n{TOKEN}\n<---End paste\n"
            )
        if cmd == "lsjson":
            trial = env is not None
            if trial:
                assert env["RCLONE_CONFIG_IIMOWNCLIENT_TOKEN"] == TOKEN
            drive = (
                self.new_drive
                if trial
                else (self.new_drive if "client_id" in self.conf.read_text() else self.old_drive)
            )
            return self._done(0 if drive is not None else 1, json.dumps(drive or []))
        raise AssertionError(args)

    @staticmethod
    def _done(code, out="", err=""):
        return subprocess.CompletedProcess([], code, out, err)

    def updated(self) -> bool:
        return any(a[1:3] == ["config", "update"] for a in self.calls if len(a) > 2)


@pytest.fixture
def conf(tmp_path):
    p = tmp_path / "rclone.conf"
    p.write_text("[gdrive]\ntype = drive\nscope = drive\ntoken = {old}\n")
    return p


def test_signs_in_and_changes_the_account(conf):
    fake = FakeRclone(conf)
    done = own_client("gdrive", CLIENT, "secret", "iPhone Archive", run=fake)
    assert done["remote"] == "gdrive" and done["folders"] == 2
    assert fake.updated()
    assert "client_id" in conf.read_text()
    kept = conf.with_name("rclone.conf.before-own-client")
    assert kept.read_text() == "[gdrive]\ntype = drive\nscope = drive\ntoken = {old}\n"
    update = next(a for a in fake.calls if a[1:3] == ["config", "update"])
    assert update[update.index("token") + 1] == TOKEN


def test_a_different_google_account_changes_nothing(conf):
    other = [{"ID": "zz", "Name": "iPhone Archive", "IsDir": True}]
    fake = FakeRclone(conf, new_drive=other)
    with pytest.raises(OwnClientError, match="different Google Drive"):
        own_client("gdrive", CLIENT, "secret", "iPhone Archive", run=fake)
    assert not fake.updated()


def test_without_the_old_listing_the_backup_folder_must_be_there(conf):
    fake = FakeRclone(conf, old_drive=None, new_drive=[{"ID": "a", "Name": "Docs"}])
    with pytest.raises(OwnClientError, match="no iPhone Archive folder"):
        own_client("gdrive", CLIENT, "secret", "iPhone Archive/Phone", run=fake)
    assert not fake.updated()
    assert own_client(
        "gdrive", CLIENT, "secret", "iPhone Archive", run=FakeRclone(conf, old_drive=None)
    )


def test_refused_while_rclone_runs(conf):
    fake = FakeRclone(conf, running=True)
    with pytest.raises(OwnClientError, match="upload is running"):
        own_client("gdrive", CLIENT, "secret", "iPhone Archive", run=fake)
    assert not any(a[1] == "authorize" for a in fake.calls if a[0] != "pgrep")


def test_a_failed_sign_in_changes_nothing(conf):
    fake = FakeRclone(conf, signs_in=False)
    with pytest.raises(OwnClientError, match="invalid_client"):
        own_client("gdrive", CLIENT, "secret", "iPhone Archive", run=fake)
    assert not fake.updated()


def test_a_change_that_does_not_work_is_put_back(conf):
    before = conf.read_text()
    fake = FakeRclone(conf, update_works=False)
    with pytest.raises(OwnClientError, match="put back"):
        own_client("gdrive", CLIENT, "secret", "iPhone Archive", run=fake)
    assert conf.read_text() == before


@pytest.mark.parametrize(
    "cid,secret,says",
    [
        ("not-a-client", "s", "not a client ID"),
        (CLIENT, "  ", "secret is empty"),
    ],
)
def test_bad_input_is_refused_before_anything_runs(conf, cid, secret, says):
    fake = FakeRclone(conf)
    with pytest.raises(OwnClientError, match=says):
        own_client("gdrive", cid, secret, "iPhone Archive", run=fake)
    assert fake.calls == []


def test_only_google_drive(tmp_path):
    p = tmp_path / "rclone.conf"
    p.write_text("[s3]\ntype = s3\n")
    with pytest.raises(OwnClientError, match="not a Google Drive"):
        own_client("s3", CLIENT, "secret", "x", run=FakeRclone(p))


def test_parse_authorize():
    assert parse_authorize(f"noise\n--->\n{TOKEN}\n<---End paste\n") == TOKEN
    assert parse_authorize("nothing here\n{not json\n") is None
    assert parse_authorize('{"error":"x"}\n') is None
    assert storage.AUTHORIZE_SECONDS >= 300
