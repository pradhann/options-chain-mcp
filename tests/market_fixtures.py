"""Seed an offline `.optionslab` with synthetic snapshots whose answers are known.

The chain is priced by Black-Scholes at a known spot, rate, and vol, so
parity must recover the spot, IV-from-mid must recover the vol, and the
walls are wherever the OI is placed. Nothing here is market data.
"""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd

from optionslab.feeds.chains import CBOE_URL, LISTING, RAW_COLUMNS
from optionslab.pricing import bs_price, year_fraction
from optionslab.storage import snapshots as snap

DAY = date(2026, 9, 18)
FETCHED_AT = "2026-09-18T19:45:00+00:00"          # 15:45 ET
SPOT, R, VOL, Q = 100.0, 0.04, 0.30, 0.0
EXPIRY = "2027-03-19"
STRIKES = np.arange(70.0, 135.0, 5.0)


def quote_envelope(spot: float = SPOT, session: str = "regular") -> dict:
    return {"status": "ok", "data": {"spot": spot, "spot_time": FETCHED_AT},
            "provenance": {"asof": FETCHED_AT, "source": "yfinance Ticker.info",
                           "session": session, "quality": "delayed"},
            "warnings": [], "not_verified": []}


def rate_envelope(r: float | None = R) -> dict:
    return {"status": "ok" if r is not None else "not_verified",
            "data": {"r": r} if r is not None else None,
            "provenance": {"asof": FETCHED_AT, "source": "yfinance ^IRX",
                           "source_url": "https://finance.yahoo.com/quote/%5EIRX"},
            "warnings": [], "not_verified": []}


def chain_frame(spot: float = SPOT, *, oi: dict | None = None,
                zero_quotes: bool = False, half_spread: float = 0.05) -> pd.DataFrame:
    """Calls and puts priced at VOL; bid/ask = price -/+ half_spread."""
    now = datetime.fromisoformat(FETCHED_AT).astimezone(snap.ET).replace(tzinfo=None)
    T = year_fraction(EXPIRY, now=now)
    rows = []
    for side in ("call", "put"):
        for k in STRIKES:
            px = float(bs_price(spot, k, T, R, VOL, side, Q))
            bid, ask = (0.0, 0.0) if zero_quotes else (max(px - half_spread, 0.01), px + half_spread)
            rows.append({"type": side, "contract": f"X{side[0]}{k:g}", "strike": k,
                         "bid": bid, "ask": ask, "last": round(px, 2),
                         "volume": 10, "oi": (oi or {}).get((side, k), 100),
                         "provider_iv": VOL, "last_trade": FETCHED_AT})
    return pd.DataFrame(rows)[RAW_COLUMNS]


def seed_chain(symbol: str = "TEST", *, day: date = DAY, feed_spot: float = SPOT,
               true_spot: float = SPOT, cboe_spot: float | None = SPOT,
               r: float | None = R, **frame_kwargs) -> None:
    """A chain priced at `true_spot` while the feed reports `feed_spot`."""
    context = {"fetched_at": FETCHED_AT, "quote": quote_envelope(feed_spot),
               "rate": rate_envelope(r), "trailing_dividend_yield": Q,
               "cboe": ({"spot": cboe_spot, "url": CBOE_URL.format(symbol=symbol)}
                        if cboe_spot is not None else
                        {"item": "CBOE cross-check spot", "status": "not_verified",
                         "reason": "fixture", "url": None})}
    snap.write_json("chains", symbol, LISTING, [EXPIRY], day=day)
    snap.write_table("chains", symbol, EXPIRY, chain_frame(true_spot, **frame_kwargs),
                     meta=context, day=day)


def seed_info(symbol: str = "TEST", *, day: date = DAY, **fields) -> None:
    value = {"marketState": "REGULAR", "regularMarketPrice": SPOT,
             "regularMarketTime": 1790020800, "trailingAnnualDividendYield": Q,
             "quoteType": "EQUITY", **fields}
    snap.write_json("info", symbol, "info", {"fetched_at": FETCHED_AT, "value": value}, day=day)


def seed_ohlc(symbol: str = "TEST", *, day: date = DAY, sessions: int = 200,
              gap: float = 0.0, seed: int = 7) -> None:
    """Intraday random walk with small overnight moves; `gap` adds overnight jumps."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=pd.Timestamp(day), periods=sessions)
    overnight = rng.normal(0, 0.002, sessions) + gap * rng.choice([-1, 1], sessions)
    intraday = rng.normal(0, 0.015, sessions)
    open_, close = np.empty(sessions), np.empty(sessions)
    prev = 100.0
    for i in range(sessions):
        open_[i] = prev * np.exp(overnight[i])
        close[i] = open_[i] * np.exp(intraday[i])
        prev = close[i]
    high = np.maximum(open_, close) * np.exp(np.abs(rng.normal(0, 0.005, sessions)))
    low = np.minimum(open_, close) * np.exp(-np.abs(rng.normal(0, 0.005, sessions)))
    divs = np.zeros(sessions)
    divs[[-190, -127, -64, -1]] = [0.50, 0.50, 0.60, 0.75]
    rows = pd.DataFrame({"Date": dates.strftime("%Y-%m-%d"), "Open": open_, "High": high,
                         "Low": low, "Close": close, "Adj Close": close, "Volume": 1e6,
                         "Dividends": divs, "Stock Splits": 0.0})
    snap.write_json("ohlc", symbol, "daily",
                    {"fetched_at": FETCHED_AT, "value": rows.to_dict("records")}, day=day)
