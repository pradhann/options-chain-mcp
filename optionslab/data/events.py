"""Recent headlines from yfinance (display only; never a feed for sizing).

Earnings dates live in `feeds.calendar` (two sources, verified flag) and
analyst targets in `feeds.fundamentals` (staleness only).
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import yfinance as yf


def _to_iso(value: Any) -> str | None:
    """Best-effort date -> 'YYYY-MM-DD' string."""
    try:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return pd.to_datetime(value, unit="s").strftime("%Y-%m-%d")
        return pd.to_datetime(value).strftime("%Y-%m-%d")
    except Exception:
        return str(value)


def recent_news(ticker: yf.Ticker, n: int = 10) -> list[dict]:
    """Recent headlines: list of {date, headline, url, source}."""
    try:
        items = ticker.news or []
    except Exception:
        return []
    out: list[dict] = []
    for it in items[:n]:
        c = it.get("content", it)  # newer yfinance nests under 'content'
        url = ""
        if isinstance(c.get("clickThroughUrl"), dict):
            url = c["clickThroughUrl"].get("url", "")
        out.append({
            "date": _to_iso(c.get("pubDate") or it.get("providerPublishTime")),
            "headline": c.get("title") or it.get("title", ""),
            "url": url or it.get("link", ""),
            "source": (c.get("provider") or {}).get("displayName")
            if isinstance(c.get("provider"), dict)
            else it.get("publisher", ""),
        })
    return out
