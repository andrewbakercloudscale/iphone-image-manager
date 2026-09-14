"""Shared selector options.

One decorator, so `list`, `sync` and `remove` cannot drift apart. What you
previewed with `list` is literally what the other two act on.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import click

from ..selector import CHANNELS, MEDIA_TYPES, ORDERS, Selector

F = Callable[..., Any]


def selector_options(func: F) -> F:
    """Attach the selector flags to a command."""
    options = [
        click.option(
            "--source", help=f"Channel: {', '.join(sorted(CHANNELS))}, or a raw bundle identifier."
        ),
        click.option("--type", "media_type", help=f"One of: {', '.join(sorted(MEDIA_TYPES))}."),
        click.option("--older-than", help="Age, e.g. 1y, 90d, 12w."),
        click.option("--newer-than", help="Age, e.g. 30d."),
        click.option("--year", type=int, help="Capture year, e.g. 2019."),
        click.option("--min-size", help="Smallest to include, e.g. 10MB."),
        click.option("--max-size", help="Largest to include, e.g. 500KB."),
        click.option(
            "--order",
            default="oldest",
            show_default=True,
            type=click.Choice(sorted(ORDERS)),
            help="Which end to work from.",
        ),
        click.option(
            "--no-favourites",
            is_flag=True,
            help="Exclude favourites. They are included by default so "
            "that listing shows everything; removal protects them.",
        ),
        click.option(
            "--include-proxy-suspects",
            is_flag=True,
            help="Include assets that look like iCloud proxies. They are "
            "excluded by default and can never be removed.",
        ),
        click.option("--limit", type=int, help="Stop after this many assets."),
    ]
    for option in reversed(options):
        func = option(func)
    return func


def build_selector(**kwargs: Any) -> Selector:
    """Turn the click keyword arguments into a Selector."""
    return Selector(
        source=kwargs.get("source"),
        media_type=kwargs.get("media_type"),
        older_than=kwargs.get("older_than"),
        newer_than=kwargs.get("newer_than"),
        year=kwargs.get("year"),
        min_size=kwargs.get("min_size"),
        max_size=kwargs.get("max_size"),
        order=kwargs.get("order") or "oldest",
        include_favourites=not kwargs.get("no_favourites"),
        include_proxy_suspects=bool(kwargs.get("include_proxy_suspects")),
        limit=kwargs.get("limit"),
    )
