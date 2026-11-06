"""Event-layer fetchers: earnings, news, analyst targets.

Thin yfinance wrappers. Never raise — return {} / [] when data is missing
or yfinance is uncooperative. The caller decides whether absence is fatal.
"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd
import yfinance as yf


def _to_iso(value: Any) -> Optional[str]:
    """Best-effort date -> 'YYYY-MM-DD' string."""
    try:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return pd.to_datetime(value, unit="s").strftime("%Y-%m-%d")
        return pd.to_datetime(value).strftime("%Y-%m-%d")
    except Exception:
        return str(value)


def next_earnings(ticker: yf.Ticker) -> dict:
    """Next earnings date + EPS/revenue estimates.

    Keys with None when yfinance has nothing. Never raises.
    """
    out: dict[str, Any] = {
        "date": None, "eps_estimate": None, "revenue_estimate": None,
    }
    try:
        cal = ticker.calendar
    except Exception:
        cal = None
    if isinstance(cal, dict):
        dates = cal.get("Earnings Date")
        if isinstance(dates, (list, tuple)) and dates:
            out["date"] = _to_iso(dates[0])
        elif dates is not None:
            out["date"] = _to_iso(dates)
        out["eps_estimate"] = cal.get("Earnings Average")
        out["revenue_estimate"] = cal.get("Revenue Average")
    return out


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


def analyst_targets(ticker: yf.Ticker) -> dict:
    """Analyst price targets + consensus rating."""
    out: dict[str, Any] = {
        "mean": None, "high": None, "low": None,
        "num_analysts": None, "rating": None,
    }
    try:
        info = ticker.info
    except Exception:
        return out
    out["mean"] = info.get("targetMeanPrice")
    out["high"] = info.get("targetHighPrice")
    out["low"] = info.get("targetLowPrice")
    out["num_analysts"] = info.get("numberOfAnalystOpinions")
    out["rating"] = info.get("recommendationKey")
    return out
