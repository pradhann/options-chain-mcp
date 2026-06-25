"""Skew metrics: 25Δ Risk Reversal + 25Δ Butterfly + ATM IV.

Definitions (all in vol points):

    RR(25Δ) = IV(25Δ call) − IV(25Δ put)            # negative on indexes
    BF(25Δ) = ½·[IV(25Δ call) + IV(25Δ put)] − IV(ATM)

A persistently negative RR on equity indexes is "evacuation insurance":
everyone owns stocks, everyone wants the put, only crashes matter.
Single names with binary catalysts show a smile (BF up, RR near zero).

Percentile history: snapshots accumulate in `.optionslab/history/`
per (ticker, days) — yfinance doesn't give historical IVs, so the
two-year percentile builds from your own daily runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from ...data.chain import load_chain
from ...errors import OptionsLabError
from .atm import atm_iv, iv_at_delta
from .percentile import (
    PercentileResult,
    append_snapshot,
    load_history,
    percentile_from_series,
)


@dataclass(frozen=True, slots=True)
class SkewResult:
    """Today's 25Δ RR + 25Δ BF + ATM IV with optional percentiles."""

    asof: str
    symbol: str
    expiration: str
    spot: float
    atm_iv_pct: Optional[float]
    iv_25d_call_pct: Optional[float]
    iv_25d_put_pct: Optional[float]
    risk_reversal_pct: Optional[float]
    butterfly_pct: Optional[float]
    rr_percentile: Optional[PercentileResult]
    bf_percentile: Optional[PercentileResult]

    def to_dict(self) -> dict:
        return {
            "asof": self.asof,
            "symbol": self.symbol,
            "expiration": self.expiration,
            "spot": round(self.spot, 4),
            "atm_iv_pct": (round(self.atm_iv_pct, 3)
                           if self.atm_iv_pct is not None else None),
            "iv_25d_call_pct": (round(self.iv_25d_call_pct, 3)
                                if self.iv_25d_call_pct is not None else None),
            "iv_25d_put_pct": (round(self.iv_25d_put_pct, 3)
                               if self.iv_25d_put_pct is not None else None),
            "risk_reversal_pct": (round(self.risk_reversal_pct, 3)
                                  if self.risk_reversal_pct is not None else None),
            "butterfly_pct": (round(self.butterfly_pct, 3)
                              if self.butterfly_pct is not None else None),
            "rr_percentile": (self.rr_percentile.to_dict()
                              if self.rr_percentile else None),
            "bf_percentile": (self.bf_percentile.to_dict()
                              if self.bf_percentile else None),
        }


def skew_metrics(
    symbol: str,
    *,
    expiration: Optional[str] = None,
    exp_index: Optional[int] = None,
    save_history: bool = False,
) -> SkewResult:
    """Compute 25Δ RR + 25Δ BF + ATM IV for one expiration.

    `save_history=True` appends today's RR and BF to per-key CSVs in
    `.optionslab/history/`, building the two-year percentile organically
    over time.
    """
    ch = load_chain(symbol, expiration=expiration, exp_index=exp_index)

    iv_atm_call = atm_iv(ch.calls, ch.spot)
    iv_atm_put = atm_iv(ch.puts, ch.spot)
    atm = None
    if iv_atm_call is not None and iv_atm_put is not None:
        atm = 0.5 * (iv_atm_call + iv_atm_put)
    elif iv_atm_call is not None:
        atm = iv_atm_call
    elif iv_atm_put is not None:
        atm = iv_atm_put

    iv_call = iv_at_delta(ch.calls, 0.25, "call")
    iv_put = iv_at_delta(ch.puts, -0.25, "put")

    rr = (iv_call - iv_put) if (iv_call is not None and iv_put is not None) else None
    bf = (0.5 * (iv_call + iv_put) - atm
          if (iv_call is not None and iv_put is not None and atm is not None)
          else None)

    # Persist and read percentiles from local history.
    key_base = f"skew_{symbol.upper()}_{ch.expiration}"
    rr_pct: Optional[PercentileResult] = None
    bf_pct: Optional[PercentileResult] = None
    if rr is not None:
        if save_history:
            append_snapshot(f"{key_base}_RR", rr)
        hist = load_history(f"{key_base}_RR")
        if not hist.empty:
            rr_pct = percentile_from_series(rr, hist)
    if bf is not None:
        if save_history:
            append_snapshot(f"{key_base}_BF", bf)
        hist = load_history(f"{key_base}_BF")
        if not hist.empty:
            bf_pct = percentile_from_series(bf, hist)

    return SkewResult(
        asof=pd.Timestamp.now().strftime("%Y-%m-%d"),
        symbol=ch.symbol, expiration=ch.expiration, spot=ch.spot,
        atm_iv_pct=atm,
        iv_25d_call_pct=iv_call, iv_25d_put_pct=iv_put,
        risk_reversal_pct=rr, butterfly_pct=bf,
        rr_percentile=rr_pct, bf_percentile=bf_pct,
    )
