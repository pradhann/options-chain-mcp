"""Variance Risk Premium — IV² minus expected RV², on SPX-style indexes.

In vol points (not variance points) for practitioner readability:

    VRP_t = VIX_t  −  forward_RV_t

where forward_RV_t is the *trailing* 21-day realized vol of SPX
*starting at t* (i.e., the realization of the variance VIX_t was
pricing). The casino analogy: VRP is the house edge on insurance —
positive on average, fat-left-tailed, the disasters fund the discount.

Functions:
  vrp_history(years=15)  — full time series + summary stats
  vrp_today()            — today's VIX − trailing-21d-RV with 2y percentile
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import yfinance as yf

from ...errors import OptionsLabError
from .percentile import PercentileResult, percentile_from_series


@dataclass(frozen=True, slots=True)
class VrpHistoryResult:
    """A full VRP time series + summary statistics."""

    start: str
    end: str
    series: list[dict]              # [{date, VIX, RV21, VRP}]
    mean_vrp: float
    median_vrp: float
    pct_positive: float             # 0..100
    worst_day_vrp: float
    worst_day_date: str

    def to_dict(self) -> dict:
        return {
            "start": self.start, "end": self.end,
            "n": len(self.series),
            "mean_vrp": round(self.mean_vrp, 3),
            "median_vrp": round(self.median_vrp, 3),
            "pct_days_positive": round(self.pct_positive, 1),
            "worst_day_vrp": round(self.worst_day_vrp, 3),
            "worst_day_date": self.worst_day_date,
            # Drop the dense daily series from the default dict to keep
            # MCP / CLI responses readable; callers wanting it grab it from
            # the dataclass directly.
        }


@dataclass(frozen=True, slots=True)
class VrpTodayResult:
    """Today's VRP snapshot with rolling-window percentile context."""

    asof: str
    vix: float
    realized_vol_21d: float
    vrp: float
    percentile_2y: PercentileResult

    def to_dict(self) -> dict:
        return {
            "asof": self.asof,
            "vix": round(self.vix, 2),
            "realized_vol_21d_pct": round(self.realized_vol_21d, 2),
            "vrp_vol_points": round(self.vrp, 2),
            "percentile_2y": self.percentile_2y.to_dict(),
            "interpretation": _interpret_vrp(self.vrp,
                                             self.percentile_2y.percentile),
        }


def _interpret_vrp(vrp: float, pct: float | None) -> str:
    if vrp < 0:
        return "negative VRP — RV exceeded IV; vol sellers were paid less than realized"
    if pct is None:
        return "positive VRP — vol sellers were paid more than realized"
    if pct < 25:
        return "positive but historically low; thin premium for short-vol risk"
    if pct > 75:
        return "positive and historically rich; structurally good for vol sellers"
    return "positive, around the long-run median"


# ---------- the data pipeline ----------

def _spx_history(years: int) -> pd.Series:
    """Daily closes of SPX (^GSPC); used to compute RV."""
    days = int(years * 365.25)
    h = yf.Ticker("^GSPC").history(period=f"{days}d", auto_adjust=False)
    if h.empty:
        raise OptionsLabError("Could not pull SPX history")
    return h["Close"].rename("SPX").tz_localize(None)


def _vix_history(years: int) -> pd.Series:
    days = int(years * 365.25)
    h = yf.Ticker("^VIX").history(period=f"{days}d", auto_adjust=False)
    if h.empty:
        raise OptionsLabError("Could not pull VIX history")
    return h["Close"].rename("VIX").tz_localize(None)


def _rolling_rv_pct(closes: pd.Series, window: int = 21) -> pd.Series:
    """Annualized close-to-close RV (%) on the same calendar as `closes`."""
    r = np.log(closes / closes.shift(1))
    return r.rolling(window).std(ddof=1) * np.sqrt(252) * 100


def vrp_history(years: int = 15) -> VrpHistoryResult:
    """Full VIX − trailing-21d-RV time series for the requested window."""
    spx = _spx_history(years)
    vix = _vix_history(years)
    rv = _rolling_rv_pct(spx, 21)
    df = pd.concat([vix.rename("VIX"), rv.rename("RV21")], axis=1).dropna()
    df["VRP"] = df["VIX"] - df["RV21"]

    series = [
        {"date": idx.strftime("%Y-%m-%d"),
         "VIX": round(float(row.VIX), 3),
         "RV21": round(float(row.RV21), 3),
         "VRP": round(float(row.VRP), 3)}
        for idx, row in df.iterrows()
    ]
    worst_idx = df["VRP"].idxmin()
    return VrpHistoryResult(
        start=df.index.min().strftime("%Y-%m-%d"),
        end=df.index.max().strftime("%Y-%m-%d"),
        series=series,
        mean_vrp=float(df["VRP"].mean()),
        median_vrp=float(df["VRP"].median()),
        pct_positive=float((df["VRP"] > 0).mean() * 100),
        worst_day_vrp=float(df["VRP"].min()),
        worst_day_date=worst_idx.strftime("%Y-%m-%d"),
    )


def vrp_today() -> VrpTodayResult:
    """Today's VRP + 2-year percentile context."""
    # Pull 3 years to be safe for a 2-year rolling window.
    spx = _spx_history(years=3)
    vix = _vix_history(years=3)
    rv = _rolling_rv_pct(spx, 21)
    df = pd.concat([vix.rename("VIX"), rv.rename("RV21")], axis=1).dropna()
    df["VRP"] = df["VIX"] - df["RV21"]

    last = df.iloc[-1]
    pct = percentile_from_series(float(last["VRP"]),
                                 df["VRP"].iloc[:-1],     # exclude today itself
                                 window_days=504)
    return VrpTodayResult(
        asof=df.index[-1].strftime("%Y-%m-%d"),
        vix=float(last["VIX"]),
        realized_vol_21d=float(last["RV21"]),
        vrp=float(last["VRP"]),
        percentile_2y=pct,
    )
