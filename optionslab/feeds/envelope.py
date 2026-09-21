"""Provenance envelope shared by every feed.

Rules this module encodes (from the data-layer spec):

  * Every result carries `asof`, `source`, `source_url`, `session`, `quality`.
  * Missing is `None`, never zero.
  * A value the tool cannot source is not returned; a `not_verified` line
    with a reason and a URL the user can follow is returned instead.

Two shapes are used everywhere:

    envelope(...)  -> {"status", "provenance", "data", "warnings", "not_verified"}
    unverified(item, reason, url) -> {"item", "status": "not_verified", "reason", "url"}
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

OK = "ok"
PARTIAL = "partial"
NOT_VERIFIED = "not_verified"

QUALITIES = ("live", "delayed", "stale", "last_only", "snapshot", "unavailable")


def now_utc() -> datetime:
    return datetime.now(UTC)


def iso(ts: datetime | None = None) -> str:
    """ISO-8601 with timezone; naive datetimes are taken as UTC."""
    ts = ts or now_utc()
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.isoformat(timespec="seconds")


def provenance(
    *,
    source: str,
    source_url: str | None = None,
    asof: str | datetime | None = None,
    session: str | None = None,
    quality: str = "live",
    **extra: Any,
) -> dict:
    """Build the provenance block. `quality` must be one of QUALITIES."""
    if quality not in QUALITIES:
        raise ValueError(f"quality must be one of {QUALITIES}, got {quality!r}")
    out = {
        "asof": asof if isinstance(asof, str) else iso(asof),
        "source": source,
        "source_url": source_url,
        "session": session,
        "quality": quality,
    }
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


def unverified(item: str, reason: str, url: str | None = None, **extra: Any) -> dict:
    """A `not_verified` line. The tool returns this instead of a number."""
    out = {"item": item, "status": NOT_VERIFIED, "reason": reason, "url": url}
    out.update(extra)
    return out


def envelope(
    data: Any,
    *,
    prov: dict,
    warnings: list[str] | None = None,
    not_verified: list[dict] | None = None,
    status: str | None = None,
) -> dict:
    """Wrap a feed result. Status defaults from the not_verified list."""
    unverified_lines = list(not_verified or [])
    if status is None:
        if data is None:
            status = NOT_VERIFIED
        else:
            status = PARTIAL if unverified_lines else OK
    return {
        "status": status,
        "provenance": prov,
        "data": clean(data),
        "warnings": list(warnings or []),
        "not_verified": unverified_lines,
    }


def unsourced(item: str, reason: str, url: str | None = None, *, source: str) -> dict:
    """Envelope for a feed that produced nothing usable."""
    return envelope(
        None,
        prov=provenance(source=source, source_url=url, quality="unavailable"),
        not_verified=[unverified(item, reason, url)],
        status=NOT_VERIFIED,
    )


def clean(obj: Any) -> Any:
    """Recursively make JSON-safe: NaN/inf -> None, numpy -> python."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if hasattr(obj, "item") and not isinstance(obj, (str, bytes)):
        try:
            obj = obj.item()
        except (ValueError, AttributeError):
            pass
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, datetime):
        return iso(obj)
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


def num(v: Any) -> float | None:
    """Coerce to float; None for missing/NaN/non-numeric. Never zero-fills."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else f
