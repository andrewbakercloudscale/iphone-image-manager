"""Destination listings run side by side, and fail exactly as they did in a row."""

from __future__ import annotations

import threading
import time

import pytest

from iphone_image.cloud import CloudError, list_hashes


class Slow:
    def __init__(self, fail: str | None = None) -> None:
        self.running = 0
        self.most = 0
        self.lock = threading.Lock()
        self.fail = fail

    def hashes(self, destination: str, subdir: str | None = None) -> dict[str, str]:
        with self.lock:
            self.running += 1
            self.most = max(self.most, self.running)
        time.sleep(0.2)
        with self.lock:
            self.running -= 1
        if destination == self.fail:
            raise CloudError("listing failed")
        return {f"{destination}.jpg": destination}


def test_every_destination_is_listed_at_once() -> None:
    p = Slow()
    found = list_hashes(p, ["Photos", "Videos", "Photos/screenshots", "Videos"])
    assert found == {d: {f"{d}.jpg": d} for d in ["Photos", "Photos/screenshots", "Videos"]}
    assert p.most == 3


def test_one_failed_listing_fails_the_call() -> None:
    with pytest.raises(CloudError):
        list_hashes(Slow(fail="Videos"), ["Photos", "Videos"])


def test_nothing_wanted_lists_nothing() -> None:
    assert list_hashes(Slow(), []) == {}
