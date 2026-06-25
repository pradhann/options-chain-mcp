"""Week 3 volatility analytics.

  percentile  — rolling percentile + local snapshot CSV helpers
  atm         — ATM/Δ/term IV interpolation
  vrp         — variance risk premium (Day 3)
  term        — VIX term structure + regime (Day 4)
  skew        — 25Δ Risk Reversal / 25Δ Butterfly (Day 5)
  strip       — model-free VIX replication (Day 6)
  event       — forward-variance event-implied move (Day 7)
  dashboard   — the daily Vol Dashboard (Day 7)
"""

from .atm import atm_iv, interpolate_term_iv, iv_at_delta
from .dashboard import DashboardResult, format_dashboard, vol_dashboard
from .event import EventVolResult, event_implied_move
from .percentile import (
    PercentileResult,
    append_snapshot,
    history_dir,
    load_history,
    percentile_from_series,
)
from .skew import SkewResult, skew_metrics
from .strip import VixStripResult, vix_strip
from .term import (
    TermStructureResult,
    regime_action,
    regime_label,
    term_structure,
    term_structure_history,
)
from .vrp import VrpHistoryResult, VrpTodayResult, vrp_history, vrp_today

__all__ = [
    # results
    "DashboardResult", "EventVolResult", "PercentileResult", "SkewResult",
    "TermStructureResult", "VixStripResult",
    "VrpHistoryResult", "VrpTodayResult",
    # functions
    "vol_dashboard", "format_dashboard",
    "event_implied_move",
    "skew_metrics",
    "vix_strip",
    "term_structure", "term_structure_history", "regime_label", "regime_action",
    "vrp_history", "vrp_today",
    # helpers
    "atm_iv", "iv_at_delta", "interpolate_term_iv",
    "history_dir", "load_history", "append_snapshot", "percentile_from_series",
]
