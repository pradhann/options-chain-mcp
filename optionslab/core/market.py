"""MarketContext — the "world state" every analysis is evaluated against.

Holds spot, risk-free rate, dividend yield, and the as-of timestamp.
One object carries everything an analysis needs to know about "now."
Build with `MarketContext.explicit(...)`; adapters fill `r` from the live
13-week T-bill (`data.quotes.get_risk_free_rate`) when the caller omits it.

Design note: this exists so we never thread `r`, `q`, `asof` through
every signature. Functions take `(position, market)` — that's the shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime


@dataclass(frozen=True, slots=True)
class MarketContext:
    """A snapshot of "the world" for pricing/valuation/scenarios.

    Fields
    ------
    spot : Optional[float]
        Current underlying price. Required for valuation/Greeks; may be
        None for expiration-only payoff math.
    r : float
        Continuous risk-free rate, decimal (0.045 = 4.5%).
    q : float
        Continuous dividend yield, decimal (0.018 = 1.8%).
    asof : datetime
        Time the snapshot represents. Used to compute T from each leg's
        expiration. Defaults to datetime.now() at construction.
    symbol : Optional[str]
        The ticker this snapshot came from, if any.
    """

    spot: float | None
    r: float
    q: float
    asof: datetime = field(default_factory=datetime.now)
    symbol: str | None = None

    # ---- factories ----

    @classmethod
    def explicit(cls, *, spot: float | None = None,
                 r: float, q: float = 0.0,
                 asof: datetime | None = None,
                 symbol: str | None = None) -> MarketContext:
        """Build a context from explicit values (deterministic tests, scenarios)."""
        return cls(spot=spot, r=r, q=q,
                   asof=asof or datetime.now(), symbol=symbol)

    # ---- scenario overrides ----

    def at_spot(self, spot: float) -> MarketContext:
        """A copy with `spot` replaced — the most common scenario op."""
        return replace(self, spot=spot)

    def forward(self, days: float) -> MarketContext:
        """A copy with `asof` advanced by `days` calendar days."""
        from datetime import timedelta
        return replace(self, asof=self.asof + timedelta(days=days))

    # ---- (de)serialization ----

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "spot": self.spot,
            "r": self.r,
            "q": self.q,
            "asof": self.asof.isoformat(timespec="seconds"),
        }
