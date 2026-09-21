"""Analysis layer: pure functions over (Position, MarketContext, …).

  payoff      — expiration P&L
  metrics     — closed-form max P / max L / breakevens
  valuation   — mid-flight value + portfolio Greeks
  scenario    — theoretical option price + P&L grid
  vol/        — Week-3 volatility analytics (VRP, term structure,
                skew, VIX strip, event vol, dashboard)

Every public function returns a typed result with `.to_dict()`.
"""

from . import vol
from .metrics import position_metrics
from .parity import ParityResult, parity_check
from .payoff import expiration_payoff
from .scenario import scenario_grid, theoretical_price
from .synthetics import (
    SyntheticVerifyResult,
    long_stock_payoff,
    short_stock_payoff,
    synthetic_long_call,
    synthetic_long_put,
    synthetic_long_stock,
    synthetic_short_call,
    synthetic_short_put,
    synthetic_short_stock,
    verify_synthetic,
)
from .valuation import greeks, value

__all__ = [
    # canonical
    "expiration_payoff", "position_metrics", "value", "greeks",
    "theoretical_price", "scenario_grid",
    # W1.D5 + W1.D6
    "parity_check", "ParityResult",
    "verify_synthetic", "SyntheticVerifyResult",
    "synthetic_long_stock", "synthetic_short_stock",
    "synthetic_long_call", "synthetic_short_call",
    "synthetic_long_put", "synthetic_short_put",
    "long_stock_payoff", "short_stock_payoff",
    # Week 3
    "vol",
]
