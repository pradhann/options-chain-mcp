"""The Vol Dashboard — Day 7 of Week 3, the daily ritual.

Five computed fields plus two human-written fields, in one structured
result that can be printed, JSON-serialized, charted, or emailed.

  1. 30-day ATM IV (SPX) + 2y percentile
  2. VRP (VIX − trailing-21d-RV) + 2y percentile
  3. VIX3M/VIX ratio + regime label + 2y percentile
  4. 25Δ Risk Reversal (SPY default) + 2y percentile (history grows over time)
  5. 25Δ Butterfly (SPY default) + 2y percentile (history grows over time)
  6. Yesterday's call — human passthrough
  7. Written vol view — human passthrough

The default symbol for the skew block is `SPY` since SPX delta data via
yfinance can be patchy; override with `skew_symbol=`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from ...data.vol import iv_term_structure, realized_vol_series, vix_curve
from .atm import interpolate_term_iv
from .percentile import (
    PercentileResult,
    append_snapshot,
    load_history,
    percentile_from_series,
)
from .skew import SkewResult, skew_metrics
from .term import TermStructureResult, term_structure
from .vrp import VrpTodayResult, vrp_today


@dataclass(frozen=True, slots=True)
class DashboardResult:
    """The structured Vol Dashboard read."""

    asof: str
    atm_iv_pct: Optional[float]
    atm_iv_percentile_2y: Optional[PercentileResult]
    vrp: Optional[VrpTodayResult]
    term: Optional[TermStructureResult]
    skew: Optional[SkewResult]
    yesterdays_call: Optional[str]
    vol_view: Optional[str]

    def to_dict(self) -> dict:
        return {
            "asof": self.asof,
            "fields": {
                "atm_iv_30d": {
                    "value": (round(self.atm_iv_pct, 2)
                              if self.atm_iv_pct is not None else None),
                    "percentile_2y": (self.atm_iv_percentile_2y.to_dict()
                                       if self.atm_iv_percentile_2y else None),
                },
                "vrp": self.vrp.to_dict() if self.vrp else None,
                "term_structure": self.term.to_dict() if self.term else None,
                "skew": self.skew.to_dict() if self.skew else None,
            },
            "human": {
                "yesterdays_call": self.yesterdays_call,
                "vol_view": self.vol_view,
            },
        }


def _atm_iv_30d_with_percentile(symbol: str = "^VIX") -> tuple[Optional[float],
                                                                Optional[PercentileResult]]:
    """For SPX/SPY we use VIX as 30-day ATM IV; for others, interpolate term.

    The 2y percentile comes from VIX history (yfinance has it) when we're
    using VIX as the proxy; otherwise from local snapshot history.
    """
    if symbol.upper() in ("^VIX", "SPX", "SPY", "^GSPC"):
        # VIX is the 30-day SPX ATM IV proxy. Pull history and percentile.
        import yfinance as yf
        try:
            t = yf.Ticker("^VIX")
            hist = t.history(period=f"{int(2.5 * 365)}d", auto_adjust=False)
            if hist.empty:
                return None, None
            series = hist["Close"].dropna()
            today = float(series.iloc[-1])
            pct = percentile_from_series(today, series.iloc[:-1])
            return today, pct
        except Exception:
            return None, None
    # Fallback: interpolate the live IV term structure for the symbol.
    try:
        term = iv_term_structure(symbol, max_expirations=12)
        iv30 = interpolate_term_iv(term, 30)
        if iv30 is None:
            return None, None
        # Local history file
        key = f"atm_iv_30d_{symbol.upper()}"
        append_snapshot(key, iv30)
        hist = load_history(key)
        pct = percentile_from_series(iv30, hist.iloc[:-1] if len(hist) > 1 else hist)
        return iv30, pct
    except Exception:
        return None, None


def vol_dashboard(
    *,
    skew_symbol: str = "SPY",
    skew_exp_index: int = 4,
    save_history: bool = True,
    yesterdays_call: Optional[str] = None,
    vol_view: Optional[str] = None,
) -> DashboardResult:
    """Assemble the full Vol Dashboard read.

    `save_history=True` appends today's RR/BF to the project-local
    percentile history files (the only way to build those percentiles
    over time, since yfinance doesn't surface historical chains).
    """
    asof = pd.Timestamp.now().strftime("%Y-%m-%d")

    atm, atm_pct = _atm_iv_30d_with_percentile("^VIX")

    try:
        v = vrp_today()
    except Exception:
        v = None

    try:
        ts = term_structure()
    except Exception:
        ts = None

    try:
        sk = skew_metrics(skew_symbol, exp_index=skew_exp_index,
                          save_history=save_history)
    except Exception:
        sk = None

    return DashboardResult(
        asof=asof,
        atm_iv_pct=atm,
        atm_iv_percentile_2y=atm_pct,
        vrp=v,
        term=ts,
        skew=sk,
        yesterdays_call=yesterdays_call,
        vol_view=vol_view,
    )


# Pretty-print for the CLI.
def format_dashboard(result: DashboardResult) -> str:
    """A human-readable text rendering of the dashboard."""
    lines: list[str] = []
    lines.append(f"=== Vol Dashboard  ·  {result.asof} ===\n")

    iv = result.atm_iv_pct
    iv_pct = result.atm_iv_percentile_2y
    lines.append(f"  30d ATM IV (VIX)     : "
                 f"{iv:5.2f}" if iv is not None else "  30d ATM IV (VIX)     :   n/a")
    if iv_pct and iv_pct.percentile is not None:
        lines[-1] += f"   ({iv_pct.percentile:5.1f}p, n={iv_pct.samples})"
    lines.append("")

    v = result.vrp
    if v:
        lines.append(f"  VRP (VIX − 21d RV)   : "
                     f"{v.vrp:+5.2f} vol pts")
        if v.percentile_2y.percentile is not None:
            lines[-1] += f"  ({v.percentile_2y.percentile:5.1f}p)"
        lines.append(f"     → {v.to_dict()['interpretation']}")
    else:
        lines.append("  VRP                  :   n/a")
    lines.append("")

    ts = result.term
    if ts and ts.ratio_vix3m_over_vix is not None:
        lines.append(f"  VIX3M / VIX          : {ts.ratio_vix3m_over_vix:.3f}"
                     f"   regime: {ts.regime}")
        if ts.percentile_2y and ts.percentile_2y.percentile is not None:
            lines[-1] += f"  ({ts.percentile_2y.percentile:5.1f}p)"
        lines.append(f"     → {ts.action}")
    else:
        lines.append("  VIX3M / VIX          :   n/a")
    lines.append("")

    sk = result.skew
    if sk and sk.risk_reversal_pct is not None:
        rr = sk.risk_reversal_pct
        bf = sk.butterfly_pct
        lines.append(f"  25Δ RR ({sk.symbol})        : "
                     f"{rr:+5.2f} vol pts")
        if sk.rr_percentile and sk.rr_percentile.percentile is not None:
            lines[-1] += f"  ({sk.rr_percentile.percentile:5.1f}p, n={sk.rr_percentile.samples})"
        if bf is not None:
            lines.append(f"  25Δ BF ({sk.symbol})        : "
                         f"{bf:+5.2f} vol pts")
            if sk.bf_percentile and sk.bf_percentile.percentile is not None:
                lines[-1] += f"  ({sk.bf_percentile.percentile:5.1f}p, n={sk.bf_percentile.samples})"
    else:
        lines.append("  25Δ skew             :   n/a")

    lines.append("")
    lines.append(f"  Yesterday's call     : {result.yesterdays_call or '(none)'}")
    lines.append(f"  Vol view             : {result.vol_view or '(none)'}")
    return "\n".join(lines)
