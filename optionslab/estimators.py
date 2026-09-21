"""Realized-volatility estimators: pure functions of a daily OHLC frame.

The five estimators all target quadratic variation; they disagree at the
edges because each ignores different information about the intraday path.

  close_to_close   Close prices only. Robust to intraday noise; blind to the
                   path between closes.
  parkinson        High/low range. Captures intraday range; blind to
                   direction and overnight gaps.
  garman_klass     OHLC. Range and direction; assumes no overnight gap.
  rogers_satchell  OHLC, drift-robust; assumes no overnight gap.
  yang_zhang       Overnight + open-to-close + Rogers-Satchell. The most
                   general; preferred when overnight gaps matter.

A large close_to_close minus parkinson gap means moves happen between
sessions (gaps), not inside them.

Input frames have columns Open, High, Low, Close (yfinance naming).
Every function returns annualized volatility as a DECIMAL series
(0.32 = 32%), NaN until the window fills.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


ESTIMATORS = (
    "close_to_close",
    "parkinson",
    "garman_klass",
    "rogers_satchell",
    "yang_zhang",
)


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


ESTIMATOR_FUNCTIONS = {
    "close_to_close": _rv_close_to_close,
    "parkinson":      _rv_parkinson,
    "garman_klass":   _rv_garman_klass,
    "rogers_satchell": _rv_rogers_satchell,
    "yang_zhang":     _rv_yang_zhang,
}


def rv_series(ohlc: pd.DataFrame, estimator: str, window: int) -> pd.Series:
    """Rolling annualized RV (decimal) for one estimator and window."""
    if estimator not in ESTIMATOR_FUNCTIONS:
        raise ValueError(f"estimator must be one of {ESTIMATORS}, got {estimator!r}")
    if window < 2:
        raise ValueError("window must be >= 2")
    return ESTIMATOR_FUNCTIONS[estimator](ohlc, window)
