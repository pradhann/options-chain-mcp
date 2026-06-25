"""MarketContext — the "world state" every analysis is evaluated against.

Holds spot, risk-free rate, dividend yield, and the as-of timestamp.
One object carries everything an analysis needs to know about "now."
Construct with `MarketContext.live(ticker)` to pull live values, or
build explicitly for scenarios / deterministic tests.

Design note: this exists so we never thread `r`, `q`, `asof` through
every signature. Functions take `(position, market)` — that's the shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import ClassVar, Optional


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

    spot: Optional[float]
    r: float
    q: float
    asof: datetime = field(default_factory=datetime.now)
    symbol: Optional[str] = None

    DEFAULT_RISK_FREE: ClassVar[float] = 0.045  # fallback when ^IRX unreachable

    # ---- factories ----

    @classmethod
    def explicit(cls, *, spot: Optional[float] = None,
                 r: float = 0.045, q: float = 0.0,
                 asof: Optional[datetime] = None,
                 symbol: Optional[str] = None) -> "MarketContext":
        """Build a context from explicit values (deterministic tests, scenarios)."""
        return cls(spot=spot, r=r, q=q,
                   asof=asof or datetime.now(), symbol=symbol)

    @classmethod
    def live(cls, symbol: str, *,
             r: Optional[float] = None,
             q: Optional[float] = None,
             asof: Optional[datetime] = None) -> "MarketContext":
        """Pull spot/r/q live from the data layer; explicit overrides win.

        Lazy import of data fetchers to keep core/ free of I/O cycles.
        """
        from ..data.quotes import get_dividend_yield, get_risk_free_rate, get_spot, make_ticker

        ticker = make_ticker(symbol)
        spot = get_spot(ticker)
        r_used = r if r is not None else get_risk_free_rate()
        q_used = q if q is not None else get_dividend_yield(ticker)
        return cls(spot=spot, r=r_used, q=q_used,
                   asof=asof or datetime.now(),
                   symbol=ticker.ticker)

    # ---- scenario overrides ----

    def at_spot(self, spot: float) -> "MarketContext":
        """A copy with `spot` replaced — the most common scenario op."""
        return replace(self, spot=spot)

    def forward(self, days: float) -> "MarketContext":
        """A copy with `asof` advanced by `days` calendar days."""
        from datetime import timedelta
        return replace(self, asof=self.asof + timedelta(days=days))

    def with_overrides(self, **changes) -> "MarketContext":
        """A copy with any fields replaced. The general-purpose escape hatch."""
        return replace(self, **changes)

    # ---- (de)serialization ----

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "spot": self.spot,
            "r": self.r,
            "q": self.q,
            "asof": self.asof.isoformat(timespec="seconds"),
        }
