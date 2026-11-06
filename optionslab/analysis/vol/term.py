"""VIX term structure + regime classification.

The vol-yield-curve. In contango (VIX3M > VIX) the market is sleepy and
vol-selling is on; in backwardation (VIX3M < VIX) stress is here and the
trade is to stand down or buy convexity. The ratio is the regime knob.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from ...errors import OptionsLabError
from ...data.vol import vix_curve, vix_history
from .percentile import PercentileResult, percentile_from_series


CONTANGO_THRESHOLD = 1.0       # VIX3M/VIX > 1 → contango
STRONG_CONTANGO = 1.05
DEEP_BACKWARDATION = 0.95


def regime_label(ratio: float) -> str:
    """Strong contango / contango / flat / backwardation / deep backwardation."""
    if ratio is None:
        return "unknown"
    if ratio >= STRONG_CONTANGO:
        return "strong contango"
    if ratio >= CONTANGO_THRESHOLD:
        return "contango"
    if ratio > DEEP_BACKWARDATION:
        return "flat / mild backwardation"
    return "deep backwardation"


def regime_action(ratio: float) -> str:
    """The trader-level read of the regime."""
    if ratio is None:
        return "no signal"
    if ratio >= STRONG_CONTANGO:
        return "vol-selling regime on"
    if ratio >= CONTANGO_THRESHOLD:
        return "mild contango — selective vol-selling"
    if ratio > DEEP_BACKWARDATION:
        return "stand down; structures need wide wings"
    return "stress regime — stand down or buy convexity"


@dataclass(frozen=True, slots=True)
class TermStructureResult:
    """Today's VIX-family snapshot + regime + 2-year ratio percentile."""

    asof: str
    curve: dict[str, float]                 # {VIX9D, VIX, VIX3M, VIX6M}
    ratio_vix3m_over_vix: Optional[float]
    regime: str
    action: str
    percentile_2y: Optional[PercentileResult]

    def to_dict(self) -> dict:
        return {
            "asof": self.asof,
            "curve": {k: (round(v, 2) if v is not None else None)
                      for k, v in self.curve.items()},
            "ratio_vix3m_over_vix": (round(self.ratio_vix3m_over_vix, 4)
                                     if self.ratio_vix3m_over_vix else None),
            "regime": self.regime,
            "action": self.action,
            "percentile_2y": (self.percentile_2y.to_dict()
                              if self.percentile_2y else None),
        }


def term_structure() -> TermStructureResult:
    """Today's term structure with ratio percentile (2y)."""
    curve = vix_curve()
    vix = curve.get("VIX")
    vix3m = curve.get("VIX3M")
    ratio = (vix3m / vix) if (vix and vix3m) else None

    pct: Optional[PercentileResult] = None
    if ratio is not None:
        try:
            hist = vix_history(("VIX", "VIX3M"), lookback_days=750)
            ratio_series = (hist["VIX3M"] / hist["VIX"]).dropna()
            pct = percentile_from_series(ratio, ratio_series.iloc[:-1])
        except Exception:
            pct = None

    return TermStructureResult(
        asof=pd.Timestamp.now().strftime("%Y-%m-%d"),
        curve=curve,
        ratio_vix3m_over_vix=ratio,
        regime=regime_label(ratio),
        action=regime_action(ratio),
        percentile_2y=pct,
    )


def term_structure_history(lookback_days: int = 1260) -> pd.DataFrame:
    """Historical {VIX9D, VIX, VIX3M, VIX6M} + the VIX3M/VIX ratio."""
    df = vix_history(("VIX9D", "VIX", "VIX3M", "VIX6M"),
                     lookback_days=lookback_days)
    if "VIX" in df.columns and "VIX3M" in df.columns:
        df["VIX3M/VIX"] = df["VIX3M"] / df["VIX"]
    return df
