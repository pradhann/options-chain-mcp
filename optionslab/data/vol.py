"""Volatility analytics: realized vol estimator zoo + VIX curve + IV term.

The five RV estimators are different surveillance cameras watching the
same drunk man stagger home. They all chase quadratic variation; they
disagree at the edges because each ignores some information about the
intraday path.

  close_to_close   Only close prices. Robust to intraday noise; ignores
                   the entire path between closes.
  parkinson        Uses high/low. Captures intraday range; ignores
                   direction and overnight gaps.
  garman_klass     Uses OHLC. Captures direction and range together;
                   assumes no overnight gap.
  rogers_satchell  Uses OHLC, drift-robust. Works when there's a non-zero
                   mean return; assumes no overnight gap.
  yang_zhang       Combines overnight, opening drift, and Rogers-Satchell.
                   The most general; preferred when overnight gaps matter.

When two estimators disagree materially on the same window, the gap is
itself a signal: large parkinson−c2c means intraday whipsaw; large
yang_zhang−c2c means overnight gap risk.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

from ..errors import OptionsLabError
from .chain import load_chain
from .quotes import make_ticker


TRADING_DAYS = 252


ESTIMATORS = (
    "close_to_close",
    "parkinson",
    "garman_klass",
    "rogers_satchell",
    "yang_zhang",
)


# ---------- per-estimator vectorized formulas (rolling-window safe) ----------

def _log_returns(closes: pd.Series) -> pd.Series:
    return np.log(closes / closes.shift(1))


def _rv_close_to_close(ohlc: pd.DataFrame, window: int) -> pd.Series:
    """σ_annual = std(log returns) · √252."""
    r = _log_returns(ohlc["Close"])
    return r.rolling(window).std(ddof=1) * np.sqrt(TRADING_DAYS)


def _rv_parkinson(ohlc: pd.DataFrame, window: int) -> pd.Series:
    """Parkinson 1980: 1/(4 ln 2) · mean(ln(H/L)²)."""
    hl = np.log(ohlc["High"] / ohlc["Low"]) ** 2
    var = hl.rolling(window).mean() / (4 * np.log(2))
    return np.sqrt(var * TRADING_DAYS)


def _rv_garman_klass(ohlc: pd.DataFrame, window: int) -> pd.Series:
    """Garman-Klass 1980 — uses OHLC, no gap assumption."""
    hl = np.log(ohlc["High"] / ohlc["Low"]) ** 2
    co = np.log(ohlc["Close"] / ohlc["Open"]) ** 2
    daily = 0.5 * hl - (2 * np.log(2) - 1) * co
    var = daily.rolling(window).mean()
    return np.sqrt(var * TRADING_DAYS)


def _rv_rogers_satchell(ohlc: pd.DataFrame, window: int) -> pd.Series:
    """Rogers-Satchell 1991 — drift-robust OHLC estimator."""
    ho = np.log(ohlc["High"] / ohlc["Open"])
    hc = np.log(ohlc["High"] / ohlc["Close"])
    lo = np.log(ohlc["Low"] / ohlc["Open"])
    lc = np.log(ohlc["Low"] / ohlc["Close"])
    daily = ho * hc + lo * lc
    var = daily.rolling(window).mean()
    return np.sqrt(var * TRADING_DAYS)


def _rv_yang_zhang(ohlc: pd.DataFrame, window: int) -> pd.Series:
    """Yang-Zhang 2000 — handles overnight gaps + opening drift + RS.

    Variance = σ²_overnight + k·σ²_open_to_close + (1-k)·σ²_rogers_satchell
    with k chosen to minimize variance of the estimator (Yang-Zhang's formula
    using N=window).
    """
    o = ohlc["Open"]
    c = ohlc["Close"]
    prev_c = c.shift(1)
    overnight = np.log(o / prev_c) ** 2
    open_to_close = np.log(c / o) ** 2

    # k: tuning constant from the paper.
    k = 0.34 / (1.34 + (window + 1) / (window - 1)) if window > 1 else 0.34

    sigma_overnight = overnight.rolling(window).mean()
    sigma_otc = open_to_close.rolling(window).mean()
    # RS component (already daily-variance terms, no sqrt yet)
    ho = np.log(ohlc["High"] / ohlc["Open"])
    hc = np.log(ohlc["High"] / ohlc["Close"])
    lo = np.log(ohlc["Low"] / ohlc["Open"])
    lc = np.log(ohlc["Low"] / ohlc["Close"])
    rs_daily = ho * hc + lo * lc
    sigma_rs = rs_daily.rolling(window).mean()

    var = sigma_overnight + k * sigma_otc + (1 - k) * sigma_rs
    return np.sqrt(var * TRADING_DAYS)


_ESTIMATOR_FN = {
    "close_to_close": _rv_close_to_close,
    "parkinson":      _rv_parkinson,
    "garman_klass":   _rv_garman_klass,
    "rogers_satchell": _rv_rogers_satchell,
    "yang_zhang":     _rv_yang_zhang,
}


# ---------- public API ----------

def _fetch_history(symbol: str, lookback_days: int) -> pd.DataFrame:
    """Pull enough OHLC to compute a rolling RV over the requested window.

    Multiplies the calendar lookback by ~1.5x to net the trading days.
    """
    ticker = make_ticker(symbol)
    days_to_pull = int(lookback_days * 1.5 + 30)
    hist = ticker.history(period=f"{days_to_pull}d", auto_adjust=False)
    if hist.empty:
        raise OptionsLabError(f"No price history for {symbol}")
    return hist


def realized_vol(
    symbol: str,
    window_days: int = 21,
    estimator: str = "close_to_close",
) -> float:
    """Annualized realized vol (%) for the most recent `window_days`.

    `estimator` ∈ ESTIMATORS. Default close_to_close matches the legacy
    behavior and is the safest default for general use.
    """
    if estimator not in _ESTIMATOR_FN:
        raise ValueError(f"estimator must be one of {ESTIMATORS}, got {estimator!r}")
    if window_days < 2:
        raise ValueError("window_days must be >= 2")
    hist = _fetch_history(symbol, window_days)
    series = _ESTIMATOR_FN[estimator](hist, window_days)
    last = series.dropna().iloc[-1] if not series.dropna().empty else None
    if last is None or not np.isfinite(last):
        raise OptionsLabError(f"Could not compute {estimator} RV for {symbol}")
    return round(float(last * 100), 2)


def realized_vol_series(
    symbol: str,
    window_days: int = 21,
    estimator: str = "close_to_close",
    lookback_days: int = 1260,    # ~5 years of trading days
) -> pd.Series:
    """Time series of annualized RV (%) for plotting and percentiles."""
    if estimator not in _ESTIMATOR_FN:
        raise ValueError(f"estimator must be one of {ESTIMATORS}, got {estimator!r}")
    hist = _fetch_history(symbol, lookback_days)
    series = _ESTIMATOR_FN[estimator](hist, window_days) * 100
    return series.dropna().rename(f"RV_{estimator}_{window_days}d")


def realized_vol_all_estimators(
    symbol: str,
    window_days: int = 21,
    lookback_days: int = 1260,
) -> pd.DataFrame:
    """All five estimators side-by-side. Columns = estimator names (annual %)."""
    hist = _fetch_history(symbol, lookback_days)
    out = pd.DataFrame({
        est: _ESTIMATOR_FN[est](hist, window_days) * 100
        for est in ESTIMATORS
    })
    return out.dropna(how="all")


# ---------- VIX family (the term-structure inputs) ----------

VIX_TICKERS = {
    "VIX9D":  "^VIX9D",
    "VIX":    "^VIX",
    "VIX3M":  "^VIX3M",
    "VIX6M":  "^VIX6M",
}


def vix_curve(date: Optional[str] = None) -> dict[str, float]:
    """Today's VIX-family snapshot: {VIX9D, VIX, VIX3M, VIX6M}.

    Missing tickers (data feed gaps) come back as None rather than
    raising — the dashboard uses what's present.
    """
    out: dict[str, float] = {}
    for name, sym in VIX_TICKERS.items():
        try:
            t = yf.Ticker(sym)
            if date:
                h = t.history(start=date, end=pd.Timestamp(date) + pd.Timedelta(days=2))
                v = float(h["Close"].iloc[0]) if not h.empty else None
            else:
                v = t.fast_info.get("last_price")
                if not v:
                    h = t.history(period="5d")
                    v = float(h["Close"].iloc[-1]) if not h.empty else None
            out[name] = float(v) if v else None
        except Exception:
            out[name] = None
    return out


def vix_history(
    members: tuple[str, ...] = ("VIX9D", "VIX", "VIX3M", "VIX6M"),
    lookback_days: int = 1260,
) -> pd.DataFrame:
    """Historical closes for the requested VIX family members."""
    frames: dict[str, pd.Series] = {}
    for name in members:
        sym = VIX_TICKERS.get(name)
        if sym is None:
            continue
        try:
            h = yf.Ticker(sym).history(period=f"{int(lookback_days * 1.5)}d")
            if not h.empty:
                frames[name] = h["Close"].rename(name)
        except Exception:
            continue
    if not frames:
        raise OptionsLabError("Could not pull any VIX-family history")
    return pd.concat(frames.values(), axis=1).dropna(how="all")


# ---------- IV term structure (ATM IV by expiration) ----------

def iv_term_structure(symbol: str, max_expirations: int = 12) -> dict[str, float]:
    """ATM implied vol (%) by expiration. One chain fetch per expiration."""
    ticker = make_ticker(symbol)
    expirations = list(ticker.options or ())
    if not expirations:
        raise OptionsLabError(f"No options listed for {symbol}")

    out: dict[str, float] = {}
    for exp in expirations[:max_expirations]:
        try:
            ch = load_chain(symbol, expiration=exp, greeks=False)
        except OptionsLabError:
            continue
        ivs: list[float] = []
        for df in (ch.calls, ch.puts):
            if df.empty:
                continue
            idx = (df["Strike"] - ch.spot).abs().idxmin()
            iv = df.loc[idx, "IV %"]
            if iv and iv > 0:
                ivs.append(float(iv))
        if ivs:
            out[exp] = round(sum(ivs) / len(ivs), 2)
    return out
