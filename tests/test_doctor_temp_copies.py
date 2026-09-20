"""The Photos database copy made by `doctor` must not outlive its use.

It once did, on every call: 1,748 copies and 23 GB by 2026-09-20, enough to
starve the video job of the disk it needed.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from iphone_image import doctor


def _library(tmp_path: Path) -> Path:
    lib = tmp_path / "Photos Library.photoslibrary"
    (lib / "database").mkdir(parents=True)
    (lib / "database" / "Photos.sqlite").write_bytes(b"x" * 1024)
    (lib / "database" / "Photos.sqlite-wal").write_bytes(b"w" * 1024)
    return lib


def test_copy_registers_its_own_removal(tmp_path, monkeypatch):
    registered = []
    def record(fn, *a, **k):
        registered.append((fn, a, k))

    monkeypatch.setattr(doctor.atexit, "register", record)
    monkeypatch.setattr(doctor.tempfile, "tempdir", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()

    copy = doctor._copy_photos_db(_library(tmp_path))

    assert copy is not None and copy.is_file()
    assert (copy.parent / "Photos.sqlite-wal").is_file()
    assert len(registered) == 1
    fn, args, kwargs = registered[0]
    fn(*args, **kwargs)
    assert not copy.parent.exists()


def test_a_failed_copy_leaves_nothing_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor.tempfile, "tempdir", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()

    def boom(*_a, **_k):
        raise OSError("No space left on device")

    monkeypatch.setattr(doctor.shutil, "copy2", boom)

    assert doctor._copy_photos_db(_library(tmp_path)) is None
    assert list((tmp_path / "tmp").glob("iim-doctor-*")) == []


def test_sweep_removes_old_copies_and_keeps_young_ones(tmp_path):
    old = tmp_path / "iim-doctor-old"
    young = tmp_path / "iim-doctor-young"
    other = tmp_path / "somebody-elses-dir"
    for d in (old, young, other):
        d.mkdir()
        (d / "f").write_bytes(b"x")
    two_hours_ago = time.time() - 7200
    os.utime(old, (two_hours_ago, two_hours_ago))
    os.utime(other, (two_hours_ago, two_hours_ago))

    assert doctor._sweep_stale_copies(root=tmp_path) == 1
    assert not old.exists()
    assert young.exists()
    assert other.exists()  # only our own prefix is ever touched
