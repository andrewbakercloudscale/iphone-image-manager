"""Group assets into event folders: "08-09 Pringle Bay".

The convention comes from the user's existing photo archive, where a year holds
named spans rather than bare months: "02 Anniversary and Misty Cliffs",
"05 and 06 Lockdown Covid". This reproduces that shape from what Photos already
knows, so an archive built by the tool sits beside one built by hand.

Three rules, each of which a naive version got wrong against real data:

- **A cluster never crosses a year.** Photos taken at home have no gap longer
  than a few days, so an unbounded run merged five years into one folder called
  "09-06 Home". The folder lives under a year, so the cluster must too.
- **A return visit is a separate folder.** Cape Town in March and again in
  November is two trips, not one eight-month span, so a gap splits a run.
- **A folder has to earn its name.** One photo taken in passing is not a trip.
  Below the threshold the asset falls back to its month, which sorts next to
  the named folders rather than into a junk drawer: `2019/11` sits beside
  `2019/11-12 Cape Town`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

#: A gap longer than this starts a new visit rather than extending one.
DEFAULT_GAP = timedelta(days=45)

#: Below this many photos a place does not get its own folder.
DEFAULT_MIN_PHOTOS = 10


@dataclass(frozen=True)
class Event:
    """One folder: a place, and the span of one visit to it."""

    year: int
    place: str
    start: datetime
    end: datetime
    count: int

    @property
    def folder(self) -> str:
        span = (
            f"{self.start:%m}"
            if self.start.month == self.end.month
            else f"{self.start:%m}-{self.end:%m}"
        )
        return f"{span} {self.place}"


def month_folder(when: datetime) -> str:
    """Where an asset goes when no event claims it. Sorts with the events."""
    return f"{when:%m}"


def assign(
    assets: list[dict[str, Any]],
    places: Mapping[str, str | None],
    *,
    gap: timedelta = DEFAULT_GAP,
    min_photos: int = DEFAULT_MIN_PHOTOS,
) -> tuple[dict[str, str], list[Event]]:
    """Decide every asset's folder within its year.

    Returns the folder per identity key, and the events that earned a name.
    Assets with no place, or in a cluster too small to name, get their month.
    """
    dated: list[tuple[str, datetime, str | None]] = []
    for asset in assets:
        raw = asset.get("created_at_device")
        if not raw:
            continue
        try:
            when = datetime.fromisoformat(raw)
        except ValueError:
            continue
        key = str(asset["identity_key"])
        dated.append((key, when, places.get(key.split("/", 1)[0])))

    # Cluster within (place, year). Both halves matter: without the place a trip
    # is not a trip, and without the year "Home" swallows the whole library.
    runs: dict[tuple[str, int], list[tuple[str, datetime]]] = defaultdict(list)
    folders: dict[str, str] = {}
    for key, when, place in dated:
        if place:
            runs[(place, when.year)].append((key, when))
        else:
            folders[key] = month_folder(when)

    events: list[Event] = []
    for (place, year), members in runs.items():
        members.sort(key=lambda m: m[1])
        run: list[tuple[str, datetime]] = [members[0]]
        batches = []
        for item in members[1:]:
            if item[1] - run[-1][1] > gap:
                batches.append(run)
                run = [item]
            else:
                run.append(item)
        batches.append(run)

        for batch in batches:
            if len(batch) < min_photos:
                # Too small to name. Its month is a better answer than a folder
                # nobody would look in.
                for key, when in batch:
                    folders[key] = month_folder(when)
                continue
            event = Event(
                year=year,
                place=place,
                start=batch[0][1],
                end=batch[-1][1],
                count=len(batch),
            )
            events.append(event)
            for key, _ in batch:
                folders[key] = event.folder

    events.sort(key=lambda e: (e.year, e.folder))
    return folders, events
