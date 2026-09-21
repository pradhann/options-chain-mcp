"""Record-and-replay for Python-API sources (yfinance), mirroring http.py.

`http.get` snapshots raw HTTP bodies. Sources reached through a client
library instead (yfinance) go through `recorded()`: the JSON-able result
of the call is written to the snapshot store, and offline mode replays
the latest snapshot on or before OPTIONSLAB_ASOF.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from ..storage import snapshots as snap
from .envelope import clean, iso, now_utc


@dataclass(frozen=True)
class Recorded:
    value: Any
    fetched_at: str
    snapshot_date: date
    from_snapshot: bool
    note: str | None = None


def recorded(kind: str, key: str, name: str, fetch: Callable[[], Any]) -> Recorded:
    """Call `fetch` (online) and snapshot its result, or replay (offline).

    Raises snapshots.OfflineMiss offline with no snapshot. Online, if
    `fetch` raises, the latest snapshot stands in with a `note`; with no
    snapshot the original exception propagates.
    """
    if snap.is_offline():
        return _replay(kind, key, name, note=None)
    try:
        value = clean(fetch())
    except Exception as e:  # client libraries raise anything; fall back honestly
        try:
            return _replay(kind, key, name, note=f"live read failed ({e})")
        except snap.OfflineMiss:
            raise e from None
    fetched_at = iso(now_utc())
    snap.write_json(kind, key, name, {"fetched_at": fetched_at, "value": value})
    return Recorded(value, fetched_at, snap.today_et(), from_snapshot=False)


def _replay(kind: str, key: str, name: str, *, note: str | None) -> Recorded:
    hit = snap.read_json(kind, key, name)
    if hit is None:
        raise snap.OfflineMiss(f"no snapshot for {kind}/{key}/{name}")
    doc, day = hit
    return Recorded(doc["value"], doc["fetched_at"], day, from_snapshot=True, note=note)
