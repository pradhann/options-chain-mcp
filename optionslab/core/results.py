"""Typed result objects for every analysis.

Every public function returns a dataclass with a `.to_dict()` for
JSON serialization at the MCP / CLI edge. Library callers (notebooks,
tests) get attribute access; adapters get dict access. One conversion
boundary, never sprinkled.

Conventions for Greeks (returned in every GreeksResult so units are
never ambiguous):
  delta  — per +$1 spot
  gamma  — per +$1 spot (the derivative of delta)
  theta  — per +1 CALENDAR day
  vega   — per +1 IV POINT (0.01 sigma)
  rho    — per +1 PERCENTAGE POINT in r (0.01)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


_GREEK_CONVENTIONS = {
    "delta": "per +$1 spot",
    "gamma": "per +$1 spot",
    "theta": "per +1 calendar day",
    "vega":  "per +1 IV point (0.01 sigma)",
    "rho":   "per +1 percentage point in r (0.01)",
    "*_dollars": "the corresponding raw value × 100 (one contract = 100 shares)",
}


def _round_floats(d: dict, places: int = 6) -> dict:
    """Round nested floats for clean JSON output."""
    out = {}
    for k, v in d.items():
        if isinstance(v, float):
            out[k] = round(v, places)
        elif isinstance(v, list):
            out[k] = [
                _round_floats(x, places) if isinstance(x, dict)
                else (round(x, places) if isinstance(x, float) else x)
                for x in v
            ]
        elif isinstance(v, dict):
            out[k] = _round_floats(v, places)
        else:
            out[k] = v
    return out


@dataclass(frozen=True, slots=True)
class PayoffResult:
    """Expiration P&L for a position evaluated at one or more S_T."""

    s_t: float | list[float]
    per_leg_per_share: list                  # one entry per leg (scalar or list)
    total_per_share: float | list[float]     # qty-weighted sum
    total_dollars: float | list[float]       # × 100 contract multiplier

    def to_dict(self) -> dict:
        return _round_floats(asdict(self), places=4)


@dataclass(frozen=True, slots=True)
class ValueResult:
    """Mid-flight mark-to-model value (dollars) of a position."""

    spot: float
    asof: str
    leg_values_dollars: list[float]          # signed, qty- and 100x-folded
    total_value_dollars: float
    leg_pnls_vs_entry_dollars: list[float]   # leg value − sign·premium·qty·100
    total_pnl_vs_entry_dollars: float

    def to_dict(self) -> dict:
        return _round_floats(asdict(self), places=4)


@dataclass(frozen=True, slots=True)
class GreeksResult:
    """Qty-weighted, side-signed portfolio Greeks (plus dollar versions)."""

    spot: float
    asof: str
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float
    delta_dollars: float
    gamma_dollars: float
    theta_dollars: float
    vega_dollars: float
    rho_dollars: float
    per_leg: list[dict] = field(default_factory=list)
    conventions: dict = field(default_factory=lambda: dict(_GREEK_CONVENTIONS))

    def to_dict(self) -> dict:
        return _round_floats(asdict(self), places=6)


@dataclass(frozen=True, slots=True)
class MetricsResult:
    """Closed-form structural metrics for an expiration-only payoff."""

    max_profit: float | str    # float dollars; 'Unlimited' string when unbounded
    max_loss: float | str
    breakevens: list[float]
    net_entry_cost_dollars: float   # +ve = debit paid, -ve = credit received

    def to_dict(self) -> dict:
        return _round_floats(asdict(self), places=4)


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    """Grid of dollar P&L vs spot moves × days forward."""

    spot: float
    spot_pcts: list[float]
    spot_prices: list[float]
    days_forward: list[float]
    pnl_dollars: list[list[float]]      # [pct_idx][day_idx]
    r: float
    q: float
    ivs_used: list[float]

    def to_dict(self) -> dict:
        return _round_floats(asdict(self), places=4)
