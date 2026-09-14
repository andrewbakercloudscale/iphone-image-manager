"""Rendering for the CLI.

Every command produces a plain dict and a way to render it. `--json` prints the
dict, so the tool is scriptable and testable without parsing tables, and the
human view can change without breaking anyone's script.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Iterable, Sequence
from typing import Any, Literal

from rich.console import Console
from rich.table import Table
from rich.theme import Theme

THEME = Theme(
    {
        "ok": "green",
        "warn": "yellow",
        "bad": "bold red",
        "muted": "dim",
        "head": "bold",
    }
)


class Output:
    def __init__(self, *, as_json: bool = False, quiet: bool = False) -> None:
        self.as_json = as_json
        self.quiet = quiet
        # soft_wrap keeps long filesystem paths on one line so they stay
        # selectable and copy-pasteable instead of being broken mid-path.
        #
        # markup=False because everything we print may contain user data. A file
        # called "IMG[1].jpg" or a marker like "[ok]" would otherwise be eaten as
        # a style tag, silently dropping text.
        self.console = Console(
            theme=THEME, stderr=False, highlight=False, soft_wrap=True, markup=False
        )
        self.err = Console(theme=THEME, stderr=True, highlight=False, soft_wrap=True, markup=False)

    # -- structured --------------------------------------------------------

    def result(self, data: dict[str, Any], render: Callable[[], None]) -> None:
        """Emit a command result, as JSON or through the given renderer."""
        if self.as_json:
            json.dump(data, sys.stdout, indent=2, default=str)
            sys.stdout.write("\n")
        else:
            render()

    # -- human -------------------------------------------------------------

    def title(self, text: str) -> None:
        if self.as_json or self.quiet:
            return
        self.console.print()
        self.console.print(text, style="head")
        self.console.print("─" * len(text), style="muted")

    def line(self, text: str = "", style: str | None = None) -> None:
        if self.as_json or self.quiet:
            return
        self.console.print(text, style=style)

    def pairs(self, items: Iterable[tuple[str, Any]], *, width: int = 28) -> None:
        if self.as_json or self.quiet:
            return
        for key, value in items:
            self.console.print(f"  {key:<{width}}{value}")

    def table(
        self, columns: Sequence[str], rows: Iterable[Sequence[Any]], *, title: str | None = None
    ) -> None:
        if self.as_json or self.quiet:
            return
        table = Table(title=title, box=None, pad_edge=False, header_style="head")
        for column in columns:
            justify: Literal["left", "right"] = (
                "right" if column in ("COUNT", "BYTES", "SIZE") else "left"
            )
            table.add_column(column, justify=justify)
        empty = True
        for row in rows:
            table.add_row(*[str(c) for c in row])
            empty = False
        if empty:
            self.console.print("  (nothing to show)", style="muted")
            return
        self.console.print(table)

    # -- messages ----------------------------------------------------------

    def ok(self, text: str) -> None:
        if not self.as_json and not self.quiet:
            self.console.print(f"  {text}", style="ok")

    def warn(self, text: str) -> None:
        if not self.as_json:
            self.err.print(f"  warning: {text}", style="warn")

    def error(self, text: str) -> None:
        if self.as_json:
            json.dump({"error": text}, sys.stdout, indent=2)
            sys.stdout.write("\n")
        else:
            self.err.print(f"error: {text}", style="bad")


def human_bytes(n: float | None) -> str:
    if n is None:
        return "-"
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024.0:
            return f"{value:,.1f} {unit}"
        value /= 1024.0
    return f"{value:,.1f} PB"
