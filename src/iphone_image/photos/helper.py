"""Subprocess wrapper for the Swift PhotoKit helper.

The helper speaks JSON Lines on stdout, one object per line, and exits non-zero
on every failure path. This turns that into an iterator, and turns a non-zero
exit into an exception rather than a short result that looks like success.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..logs import get_logger

log = get_logger("photos.helper")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_HELPER = REPO_ROOT / "spikes" / "iimphotos" / ".build" / "release" / "iimphotos"


class HelperError(Exception):
    """The helper could not run, or failed part way through."""


class Helper:
    def __init__(self, binary: Path | None = None) -> None:
        self.binary = Path(binary) if binary else DEFAULT_HELPER

    def check(self) -> None:
        if not self.binary.exists():
            raise HelperError(
                f"the PhotoKit helper is not built at {self.binary}. "
                f"Build it with: cd spikes/iimphotos && swift build -c release"
            )

    def _stream(
        self, args: list[str], *, stdin: str | None = None, timeout: float | None = None
    ) -> Iterator[dict[str, Any]]:
        self.check()
        command = [str(self.binary), *args]
        log.debug("running %s", " ".join(command))
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE if stdin is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        if stdin is not None and process.stdin:
            process.stdin.write(stdin)
            process.stdin.close()

        assert process.stdout is not None
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                log.warning("unparseable helper output: %s", line[:200])

        process.wait(timeout=timeout)
        stderr = process.stderr.read() if process.stderr else ""
        if process.returncode != 0:
            # A short result that looks like success is the failure this project
            # keeps warning about. Exit code decides, never the output.
            raise HelperError(
                f"helper exited {process.returncode}: {stderr.strip() or 'no diagnostic on stderr'}"
            )

    def scan(self, *, limit: int = 0) -> Iterator[dict[str, Any]]:
        args = ["scan"]
        if limit:
            args += ["--limit", str(limit)]
        yield from self._stream(args)

    def export(
        self, work: list[tuple[str, Path]], *, timeout: float = 1800, allow_network: bool = True
    ) -> Iterator[dict[str, Any]]:
        """Export originals. `work` is (local identifier, destination path)."""
        if not work:
            return
        args = ["export", "--timeout", str(int(timeout))]
        if not allow_network:
            args.append("--no-network")
        payload = "".join(f"{identifier}\t{path}\n" for identifier, path in work)
        # Overall timeout allows for every asset taking its own full timeout.
        yield from self._stream(args, stdin=payload, timeout=timeout * len(work) + 60)

    def delete(self, identifiers: list[str], *, timeout: float = 3600) -> Iterator[dict[str, Any]]:
        """Delete assets from the library, by local identifier.

        Yields the helper's events. A non-zero exit raises `HelperError` after
        the events have been yielded, which is the point: it means at least one
        asset survived, and the caller must record only the ones it actually
        saw confirmed rather than assuming the batch went.
        """
        if not identifiers:
            return
        payload = "".join(f"{identifier}\n" for identifier in identifiers)
        yield from self._stream(["delete"], stdin=payload, timeout=timeout)

    def authorized(self) -> bool:
        self.check()
        result = subprocess.run(
            [str(self.binary), "auth"], capture_output=True, text=True, timeout=120
        )
        return result.returncode == 0
