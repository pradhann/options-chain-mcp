"""Dated snapshot store under `.optionslab/snapshots/`.

Free providers keep no history of option chains, curves, or positioning,
so every fetch writes a dated copy here and history accrues locally.

Layout (one directory per ET calendar date; a second fetch the same day
overwrites the first, so each date holds that day's latest read):

    snapshots/{kind}/{key}/{YYYY-MM-DD}/{name}.csv         tables
    snapshots/{kind}/{key}/{YYYY-MM-DD}/{name}.json        documents
    snapshots/{kind}/{key}/{YYYY-MM-DD}/{name}.meta.json   provenance for a table

Kinds in use: chains, ohlc, dividends, futures, raw (HTTP bodies),
plus whatever a feed registers. Retention: everything is kept; a chain
snapshot for one liquid ticker is ~0.5-2 MB/day across all expiries.

Environment:
    OPTIONSLAB_HOME     use this directory as the `.optionslab` root
    OPTIONSLAB_OFFLINE  "1" -> feeds read snapshots only, never the network
    OPTIONSLAB_ASOF     YYYY-MM-DD -> offline reads use the latest snapshot
                        on or before this date (default: today, ET)
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .positions import PROJECT_DIR, _find_project_root

ET = ZoneInfo("America/New_York")
SNAPSHOT_DIR = "snapshots"
_SAFE = re.compile(r"[^A-Za-z0-9._=^-]+")


class OfflineMiss(LookupError):
    """Offline mode and no snapshot exists for the request."""


def home(start: Path | None = None) -> Path:
    """The `.optionslab` directory (created on demand)."""
    env = os.environ.get("OPTIONSLAB_HOME")
    if env:
        p = Path(env)
    else:
        root = _find_project_root(start) or (start or Path.cwd())
        p = Path(root) / PROJECT_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def is_offline() -> bool:
    return os.environ.get("OPTIONSLAB_OFFLINE", "").strip() in ("1", "true", "yes")


def today_et() -> date:
    return datetime.now(ET).date()


def asof_date() -> date:
    """The date offline reads are pinned to."""
    env = os.environ.get("OPTIONSLAB_ASOF")
    return date.fromisoformat(env) if env else today_et()


def safe(part: str) -> str:
    return _SAFE.sub("_", str(part)).strip("_") or "_"


def _dir(kind: str, key: str, day: date | str) -> Path:
    d = day if isinstance(day, str) else day.isoformat()
    return home() / SNAPSHOT_DIR / safe(kind) / safe(key) / d


def snapshot_dates(kind: str, key: str) -> list[date]:
    """Dates with a snapshot for (kind, key), oldest first."""
    base = home() / SNAPSHOT_DIR / safe(kind) / safe(key)
    if not base.is_dir():
        return []
    out = []
    for p in base.iterdir():
        try:
            out.append(date.fromisoformat(p.name))
        except ValueError:
            continue
    return sorted(out)


def _pick_date(kind: str, key: str, name: str, ext: str,
               on_or_before: date | None) -> date | None:
    limit = on_or_before or asof_date()
    for d in reversed(snapshot_dates(kind, key)):
        if d <= limit and (_dir(kind, key, d) / f"{safe(name)}{ext}").exists():
            return d
    return None


# ---------- tables ----------

def write_table(kind: str, key: str, name: str, df: pd.DataFrame, *,
                meta: dict | None = None, day: date | None = None) -> Path:
    d = _dir(kind, key, day or today_et())
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{safe(name)}.csv"
    df.to_csv(path, index=False)
    if meta is not None:
        (d / f"{safe(name)}.meta.json").write_text(json.dumps(meta, indent=1, default=str))
    return path


def read_table(kind: str, key: str, name: str, *,
               on_or_before: date | None = None,
               day: date | None = None) -> tuple[pd.DataFrame, dict, date] | None:
    """(frame, meta, snapshot_date) for the latest snapshot, or None."""
    d = day or _pick_date(kind, key, name, ".csv", on_or_before)
    if d is None:
        return None
    path = _dir(kind, key, d) / f"{safe(name)}.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path)
    mpath = path.with_name(f"{safe(name)}.meta.json")
    meta = json.loads(mpath.read_text()) if mpath.exists() else {}
    return df, meta, d


# ---------- documents ----------

def write_json(kind: str, key: str, name: str, obj, *, day: date | None = None) -> Path:
    d = _dir(kind, key, day or today_et())
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{safe(name)}.json"
    path.write_text(json.dumps(obj, indent=1, default=str))
    return path


def read_json(kind: str, key: str, name: str, *,
              on_or_before: date | None = None,
              day: date | None = None) -> tuple[object, date] | None:
    d = day or _pick_date(kind, key, name, ".json", on_or_before)
    if d is None:
        return None
    path = _dir(kind, key, d) / f"{safe(name)}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text()), d


def history(kind: str, key: str, name: str, *,
            since: date | None = None,
            until: date | None = None) -> list[tuple[date, pd.DataFrame, dict]]:
    """Every table snapshot for (kind, key, name) in [since, until].

    `until` defaults to asof_date(), so offline replays never see the future.
    """
    until = until or asof_date()
    out = []
    for d in snapshot_dates(kind, key):
        if since and d < since:
            continue
        if d > until:
            continue
        r = read_table(kind, key, name, day=d)
        if r is not None:
            out.append((d, r[0], r[1]))
    return out
