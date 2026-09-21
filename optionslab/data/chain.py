"""Option chain fetch + decomposition + per-strike Greeks.

`load_chain(symbol, ...)` returns a `ChainSnapshot` containing the spot,
expiration, full list of expirations, decomposed calls/puts DataFrames,
and the rate/yield used for Greeks. This is the high-level data entry
point everything else builds on.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..errors import OptionsLabError
from ..pricing import bs_greeks, year_fraction
from .quotes import (
    get_dividend_yield,
    get_risk_free_rate,
    get_spot,
    list_expirations,
    make_ticker,
    resolve_expiration,
)

_RAW_COLUMNS = [
    "strike", "bid", "ask", "lastPrice",
    "volume", "openInterest", "impliedVolatility",
]
_DISPLAY = {
    "strike": "Strike", "bid": "Bid", "ask": "Ask", "lastPrice": "Last",
    "volume": "Vol", "openInterest": "OI", "impliedVolatility": "IV %",
}

PRINT_COLUMNS = [
    "Strike", "Bid", "Ask", "Last", "Mid",
    "Intrinsic", "Extrinsic", "Vol", "OI", "IV %",
]
GREEK_COLUMNS = ["Delta", "Gamma", "Theta", "Vega", "Rho"]


# ---------- pure transforms ----------

def format_raw(df: pd.DataFrame) -> pd.DataFrame:
    """Select known columns, fill NaNs, IV to percent, rename for display."""
    missing = [c for c in _RAW_COLUMNS if c not in df.columns]
    if missing:
        raise OptionsLabError(f"Chain is missing expected columns: {missing}")

    out = df[_RAW_COLUMNS].copy()
    out["volume"] = out["volume"].fillna(0).astype(int)
    out["openInterest"] = out["openInterest"].fillna(0).astype(int)
    out["impliedVolatility"] = (out["impliedVolatility"] * 100).round(2)
    out["bid"] = out["bid"].round(2)
    out["ask"] = out["ask"].round(2)
    out["lastPrice"] = out["lastPrice"].round(2)
    return out.rename(columns=_DISPLAY)


def add_value_decomposition(
    df: pd.DataFrame, spot: float, option_type: str
) -> pd.DataFrame:
    """Add Mid, Intrinsic, Extrinsic columns.

    Mid comes from a two-sided quote only: a zero or missing Bid or Ask
    (after-hours, dead strikes) leaves Mid and Extrinsic as NaN, never 0.
    """
    if option_type not in ("call", "put"):
        raise ValueError(f"option_type must be 'call'|'put', got {option_type!r}")
    out = df.copy()
    two_sided = (out["Bid"] > 0) & (out["Ask"] > 0)
    out["Mid"] = ((out["Bid"] + out["Ask"]) / 2).where(two_sided).round(3)
    if option_type == "call":
        out["Intrinsic"] = (spot - out["Strike"]).clip(lower=0).round(2)
    else:
        out["Intrinsic"] = (out["Strike"] - spot).clip(lower=0).round(2)
    out["Extrinsic"] = (out["Mid"] - out["Intrinsic"]).round(3)
    return out


def add_greeks(
    df: pd.DataFrame,
    spot: float,
    expiration: str,
    option_type: str,
    r: float,
    q: float,
) -> pd.DataFrame:
    """Add Delta/Gamma/Theta/Vega/Rho using each strike's own IV.

    Strikes with no IV (dead quotes) get NaN.
    """
    out = df.copy()
    T = year_fraction(expiration)
    sigma = out["IV %"].to_numpy(dtype=float) / 100.0
    strikes = out["Strike"].to_numpy(dtype=float)

    g = bs_greeks(np.full_like(strikes, spot), strikes, T, r, sigma,
                  option_type, q)
    valid = sigma > 0
    out["Delta"] = np.where(valid, np.round(g["delta"], 4), np.nan)
    out["Gamma"] = np.where(valid, np.round(g["gamma"], 5), np.nan)
    out["Theta"] = np.where(valid, np.round(g["theta"], 4), np.nan)
    out["Vega"] = np.where(valid, np.round(g["vega"], 4), np.nan)
    out["Rho"] = np.where(valid, np.round(g["rho"], 4), np.nan)
    return out


def filter_near_money(
    df: pd.DataFrame, spot: float | None, n: int
) -> pd.DataFrame:
    """Keep the n strikes nearest spot. No-op if spot unknown or n <= 0."""
    if spot is None or n <= 0:
        return df
    idx = (df["Strike"] - spot).abs().sort_values().index[:n]
    return df.loc[sorted(idx)].reset_index(drop=True)


def filter_liquid(df: pd.DataFrame, min_oi: int = 1) -> pd.DataFrame:
    """Drop strikes with Bid 0 or OI below min — they're stale."""
    return df[(df["Bid"] > 0) & (df["OI"] >= min_oi)].reset_index(drop=True)


# ---------- snapshot ----------

@dataclass(frozen=True, slots=True)
class ChainSnapshot:
    """Decomposed chain for one symbol/expiration, with optional Greeks.

    `calls` and `puts` are pandas DataFrames with the columns in
    `PRINT_COLUMNS` (plus `GREEK_COLUMNS` when `r`/`q` are set).
    """

    symbol: str
    spot: float
    expiration: str
    expirations: list[str]
    calls: pd.DataFrame
    puts: pd.DataFrame
    r: float           # rate used for Greeks (0 if Greeks skipped)
    q: float

    def to_records(self) -> dict:
        """JSON-safe representation: spot/expiration + records lists."""
        import json
        return {
            "symbol": self.symbol,
            "spot": round(self.spot, 4),
            "expiration": self.expiration,
            "expirations": list(self.expirations),
            "r": round(self.r, 4),
            "q": round(self.q, 4),
            "calls": json.loads(self.calls.to_json(orient="records")),
            "puts": json.loads(self.puts.to_json(orient="records")),
        }


def load_chain(
    symbol: str,
    *,
    expiration: str | None = None,
    exp_index: int | None = None,
    r: float | None = None,
    q: float | None = None,
    greeks: bool = True,
) -> ChainSnapshot:
    """Fetch + decompose a chain, optionally with per-strike Greeks.

    `r` and `q` default to live values (13-week T-bill, trailing
    dividend yield) when None; pass explicit decimals to pin a scenario.
    `greeks=False` skips the Greek columns and any rate fetch.
    """
    ticker = make_ticker(symbol)
    expirations = list_expirations(ticker)
    chosen = resolve_expiration(
        expirations, requested=expiration, index=exp_index
    )
    spot = get_spot(ticker)

    raw = ticker.option_chain(chosen)
    calls = add_value_decomposition(format_raw(raw.calls), spot, "call")
    puts = add_value_decomposition(format_raw(raw.puts), spot, "put")

    if greeks:
        r_used = get_risk_free_rate() if r is None else r
        q_used = get_dividend_yield(ticker) if q is None else q
        calls = add_greeks(calls, spot, chosen, "call", r_used, q_used)
        puts = add_greeks(puts, spot, chosen, "put", r_used, q_used)
    else:
        r_used = r or 0.0
        q_used = q or 0.0

    return ChainSnapshot(
        symbol=ticker.ticker, spot=spot, expiration=chosen,
        expirations=list(expirations),
        calls=calls, puts=puts, r=r_used, q=q_used,
    )


def iv_at_strike(df: pd.DataFrame, strike: float) -> float | None:
    """IV (decimal) of the listed strike nearest `strike`, or None."""
    if df.empty:
        return None
    idx = (df["Strike"] - strike).abs().idxmin()
    iv_pct = df.loc[idx, "IV %"]
    return float(iv_pct) / 100.0 if iv_pct and iv_pct > 0 else None
