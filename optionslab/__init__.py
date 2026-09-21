"""optionslab — options analytics toolkit.

A small, layered public surface. The contract is the same whether you
use the CLI, MCP, or the Python API:

  1. Build a `Position` (and, for valuation, a `MarketContext`).
  2. Hand it to one of the analysis functions.
  3. Get back a typed result with `.to_dict()` for JSON.

Quick start
-----------

::

    from optionslab import Position, MarketContext
    from optionslab.analysis import expiration_payoff, position_metrics, greeks

    pos = Position.from_dicts([
        {"side": "long",  "option_type": "call", "strike": 100, "premium": 6.0},
        {"side": "short", "option_type": "call", "strike": 110, "premium": 2.5},
    ], name="bull-call-100-110")

    expiration_payoff(pos, s_t=115).total_dollars   # 650.0
    position_metrics(pos).max_profit                 # 650.0
    greeks(pos, MarketContext.explicit(spot=105, r=0.045), ivs=0.30).delta

CLI
---

    python -m optionslab --help

MCP server
----------

    python -m optionslab.mcp_server
"""

from __future__ import annotations

# Core types — the central language.
from .core import (
    GreeksResult,
    Leg,
    MarketContext,
    MetricsResult,
    PayoffResult,
    Position,
    ScenarioResult,
    ValueResult,
    parse_legs,
)

# Errors users may want to catch.
from .errors import (
    ExpirationNotFoundError,
    NoOptionsDataError,
    OptionsLabError,
    SpotUnavailableError,
)

# Pricing engine — the low-level math, sometimes useful directly.
from .pricing import bs_greeks, bs_price, implied_vol, year_fraction

__all__ = [
    # core
    "Leg", "Position", "MarketContext",
    "PayoffResult", "ValueResult", "GreeksResult",
    "MetricsResult", "ScenarioResult",
    "parse_legs",
    # pricing
    "bs_price", "bs_greeks", "implied_vol", "year_fraction",
    # errors
    "OptionsLabError", "NoOptionsDataError",
    "SpotUnavailableError", "ExpirationNotFoundError",
]
