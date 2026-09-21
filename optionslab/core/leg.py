"""A single option leg — the atomic building block of a Position.

A Leg is a stateless contract description: side, type, strike, premium,
quantity, optional expiration. All validation happens at construction;
once built a Leg is immutable and trustable downstream.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

_SIDES = ("long", "short")
_TYPES = ("call", "put")


@dataclass(frozen=True, slots=True)
class Leg:
    """One option leg.

    Fields
    ------
    side : 'long' | 'short'
        Direction. Long pays the premium and owns the option; short
        receives the premium and is short the option.
    option_type : 'call' | 'put'
    strike : float
        Positive.
    premium : float
        Per-share entry price (>= 0). For paper / scenario legs that
        haven't been priced yet, pass 0.0 — payoff math still works,
        only P&L vs entry needs it.
    qty : int
        Number of contracts (one contract = 100 shares). Positive.
        Direction is encoded by `side`, never by sign on qty.
    expiration : Optional[str]
        'YYYY-MM-DD' if known. Required for mid-flight valuation,
        optional for expiration-only payoff math.
    """

    side: str
    option_type: str
    strike: float
    premium: float = 0.0
    qty: int = 1
    expiration: str | None = None

    def __post_init__(self) -> None:
        if self.side not in _SIDES:
            raise ValueError(f"side must be one of {_SIDES}, got {self.side!r}")
        if self.option_type not in _TYPES:
            raise ValueError(
                f"option_type must be one of {_TYPES}, got {self.option_type!r}"
            )
        if self.strike <= 0:
            raise ValueError(f"strike must be positive, got {self.strike}")
        if self.premium < 0:
            raise ValueError(f"premium must be >= 0, got {self.premium}")
        if self.qty <= 0:
            raise ValueError(f"qty must be a positive int, got {self.qty}")

    # ---- convenience ----

    @property
    def sign(self) -> int:
        """+1 if long, -1 if short. Used to weight payoffs and Greeks."""
        return 1 if self.side == "long" else -1

    @property
    def is_call(self) -> bool:
        return self.option_type == "call"

    # ---- (de)serialization ----

    def to_dict(self) -> dict:
        return {
            "side": self.side,
            "option_type": self.option_type,
            "strike": self.strike,
            "premium": self.premium,
            "qty": self.qty,
            "expiration": self.expiration,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Leg:
        """Build a Leg from a plain dict (MCP/JSON/file entry point).

        Unknown keys are ignored — the dict may carry extra annotation
        fields without breaking construction.
        """
        try:
            return cls(
                side=d["side"],
                option_type=d["option_type"],
                strike=float(d["strike"]),
                premium=float(d.get("premium", 0.0)),
                qty=int(d.get("qty", 1)),
                expiration=d.get("expiration"),
            )
        except KeyError as exc:
            raise ValueError(f"leg missing required field {exc}") from None

    def __str__(self) -> str:  # pragma: no cover — display only
        exp = f" {self.expiration}" if self.expiration else ""
        return (f"{self.side} {self.qty}x {self.option_type} "
                f"K=${self.strike:g} @${self.premium:g}{exp}")


def parse_legs(raw: Iterable[dict]) -> list[Leg]:
    """Build a validated list of Legs from a list of dicts."""
    legs = [Leg.from_dict(d) for d in raw]
    if not legs:
        raise ValueError("legs must be a non-empty list")
    return legs
