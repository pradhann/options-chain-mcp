"""Position — a named bundle of Legs.

The central type: every analysis function in `optionslab.analysis` takes
a Position, and every adapter builds one. Core never imports analysis.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

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
    name: str | None = None
    notes: str | None = None
    symbol: str | None = None

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
    def from_legs(cls, legs: Iterable[Leg], **meta) -> Position:
        return cls(legs=tuple(legs), **meta)

    @classmethod
    def from_dicts(cls, legs: Sequence[dict], **meta) -> Position:
        """Build a Position from a list of plain dicts (the JSON entry point)."""
        return cls(legs=tuple(parse_legs(legs)), **meta)

    @classmethod
    def from_dict(cls, d: dict) -> Position:
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
    def expirations(self) -> list[str | None]:
        return [lg.expiration for lg in self.legs]

    # ---- (de)serialization ----

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "symbol": self.symbol,
            "notes": self.notes,
            "legs": [lg.to_dict() for lg in self.legs],
        }

    def __str__(self) -> str:  # pragma: no cover — display only
        head = self.name or f"{len(self.legs)}-leg position"
        return head + "\n  " + "\n  ".join(str(lg) for lg in self.legs)
