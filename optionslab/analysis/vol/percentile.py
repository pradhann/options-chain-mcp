"""Percentile + lightweight CSV history helpers.

A few of the dashboard fields (VIX, VIX3M/VIX, VRP) can compute real
2-year percentiles from yfinance history. Others (25Δ RR / 25Δ BF on
arbitrary tickers, single-name IV percentiles) cannot — yfinance only
gives the live chain, not historical option surfaces.

For those, we store one row per snapshot in `.optionslab/history/<key>.csv`
and compute the percentile against whatever's in the file. Run the
dashboard daily; the percentile gets more meaningful over time. The
dashboard makes it explicit when history is too thin to trust.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from ...storage.positions import PROJECT_DIR, _find_project_root


HISTORY_SUBDIR = "history"
MIN_PCTILE_SAMPLES = 30  # below this, percentile is reported but flagged thin


def history_dir(start: Optional[Path] = None) -> Path:
    """`.optionslab/history/` under the project root (created on demand)."""
    root = _find_project_root(start) or (start or Path.cwd())
    out = Path(root) / PROJECT_DIR / HISTORY_SUBDIR
    out.mkdir(parents=True, exist_ok=True)
    return out


def append_snapshot(key: str, value: float, *,
                    start: Optional[Path] = None,
                    when: Optional[datetime] = None) -> Path:
    """Append a single (timestamp, value) row to history/<key>.csv."""
    when = when or datetime.now()
    path = history_dir(start) / f"{key}.csv"
    new_row = pd.DataFrame({"timestamp": [when.isoformat(timespec="seconds")],
                            "value": [float(value)]})
    if path.exists():
        new_row.to_csv(path, mode="a", header=False, index=False)
    else:
        new_row.to_csv(path, index=False)
    return path


def load_history(key: str, *, start: Optional[Path] = None) -> pd.Series:
    """Load history/<key>.csv as a time-indexed Series, or empty Series."""
    path = history_dir(start) / f"{key}.csv"
    if not path.exists():
        return pd.Series(dtype=float, name=key)
    df = pd.read_csv(path)
    s = pd.Series(df["value"].values,
                  index=pd.to_datetime(df["timestamp"]), name=key)
    return s.sort_index()


@dataclass(frozen=True, slots=True)
class PercentileResult:
    """A value with its empirical percentile context."""

    value: float
    percentile: Optional[float]      # 0..100, None if not enough data
    window_days: int                 # window the percentile was over
    samples: int                     # how many observations contributed
    thin: bool                       # True when samples < MIN_PCTILE_SAMPLES

    def to_dict(self) -> dict:
        d = {
            "value": round(self.value, 4) if self.value is not None else None,
            "percentile": (round(self.percentile, 1)
                           if self.percentile is not None else None),
            "window_days": self.window_days,
            "samples": self.samples,
        }
        if self.thin:
            d["note"] = (f"thin history ({self.samples} samples); "
                         f"percentile is approximate")
        return d


def percentile_from_series(
    today_value: float,
    series: pd.Series,
    *,
    window_days: int = 504,    # ~2 trading years
) -> PercentileResult:
    """Where does `today_value` sit in the trailing `window_days` of `series`?

    Series is sliced to its last `window_days` observations. The percentile
    is the fraction of those observations that fell at or below `today_value`,
    expressed in 0–100.
    """
    s = series.dropna()
    if not s.empty:
        s = s.tail(window_days)
    n = len(s)
    if n == 0:
        return PercentileResult(value=today_value, percentile=None,
                                window_days=window_days, samples=0, thin=True)
    pct = float((s <= today_value).mean() * 100.0)
    return PercentileResult(
        value=float(today_value), percentile=pct,
        window_days=window_days, samples=n,
        thin=n < MIN_PCTILE_SAMPLES,
    )
