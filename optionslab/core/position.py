"""Position — a named bundle of Legs with analysis methods.

The central type. Every analysis function takes a Position; every
adapter returns a Position. Methods on Position are thin delegates to
the `analysis.*` modules — they exist purely for ergonomic dotted
access (`pos.greeks(market, ivs=...)`).

Why methods AND free functions? The methods are sugar; the free
functions are the testable contract. Adapters call the free
functions for clarity; notebooks/REPL use the methods.

Module layering: core MUST NOT import analysis at top level (analysis
imports core for typed inputs). The methods lazy-import inside their
bodies.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable, Optional, Sequence

from .leg import Leg, parse_legs


@dataclass(frozen=True, slots=True)
class Position:
    """A multi-leg option position.

    Fields
    ------
    legs : tuple[Leg, ...]
        Immutable tuple of validated legs. At least one.
    name : Optional[str]
        Display name (also the key in positions storage when saved).
    notes : Optional[str]
        Free-form notes — thesis, risk plan, anything human.
    symbol : Optional[str]
        Underlying ticker, if known. Helpful when fetching live IVs.

    Methods are thin sugar over `analysis.*` — they all accept the same
    options (`ivs`, `market`) so a notebook user can swap calls with
    minimal friction.
    """

    legs: tuple[Leg, ...]
    name: Optional[str] = None
    notes: Optional[str] = None
    symbol: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.legs, tuple):
            object.__setattr__(self, "legs", tuple(self.legs))
        if not self.legs:
            raise ValueError("Position must have at least one leg")
        for i, lg in enumerate(self.legs):
            if not isinstance(lg, Leg):
                raise TypeError(f"legs[{i}] is not a Leg, got {type(lg).__name__}")

    # ---- factories ----

    @classmethod
    def from_legs(cls, legs: Iterable[Leg], **meta) -> "Position":
        return cls(legs=tuple(legs), **meta)

    @classmethod
    def from_dicts(cls, legs: Sequence[dict], **meta) -> "Position":
        """Build a Position from a list of plain dicts (the JSON entry point)."""
        return cls(legs=tuple(parse_legs(legs)), **meta)

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        """Build a Position from a top-level dict (the storage entry point).

        Expected shape: {name?, notes?, symbol?, legs: [...]}.
        """
        if "legs" not in d:
            raise ValueError("position dict missing required 'legs'")
        return cls.from_dicts(
            d["legs"],
            name=d.get("name"),
            notes=d.get("notes"),
            symbol=d.get("symbol"),
        )

    # ---- introspection ----

    def __len__(self) -> int:
        return len(self.legs)

    def __iter__(self):
        return iter(self.legs)

    @property
    def strikes(self) -> list[float]:
        return [lg.strike for lg in self.legs]

    @property
    def expirations(self) -> list[Optional[str]]:
        return [lg.expiration for lg in self.legs]

    @property
    def is_single_leg(self) -> bool:
        return len(self.legs) == 1

    # ---- copy with changes ----

    def with_(self, **changes) -> "Position":
        """Return a copy with metadata fields replaced (legs untouched here)."""
        return replace(self, **changes)

    # ---- (de)serialization ----

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "symbol": self.symbol,
            "notes": self.notes,
            "legs": [lg.to_dict() for lg in self.legs],
        }

    # ---- analysis methods (lazy imports inside body) ----

    def payoff(self, s_t):
        """Expiration P&L. See analysis.payoff.expiration_payoff."""
        from ..analysis.payoff import expiration_payoff
        return expiration_payoff(self, s_t)

    def metrics(self):
        """Closed-form max P / max L / breakevens. See analysis.metrics."""
        from ..analysis.metrics import position_metrics
        return position_metrics(self)

    def value(self, market, *, ivs):
        """Mark-to-model value (dollars). See analysis.valuation.value."""
        from ..analysis.valuation import value as _value
        return _value(self, market, ivs=ivs)

    def greeks(self, market, *, ivs):
        """Portfolio Greeks. See analysis.valuation.greeks."""
        from ..analysis.valuation import greeks as _greeks
        return _greeks(self, market, ivs=ivs)

    def scenario(self, market, *, spot_pcts=None, days_forward=None, ivs=None):
        """P&L grid over spot × time. See analysis.scenario.scenario_grid."""
        from ..analysis.scenario import scenario_grid
        return scenario_grid(self, market,
                             spot_pcts=spot_pcts,
                             days_forward=days_forward, ivs=ivs)

    def __str__(self) -> str:  # pragma: no cover — display only
        head = self.name or f"{len(self.legs)}-leg position"
        return head + "\n  " + "\n  ".join(str(lg) for lg in self.legs)
