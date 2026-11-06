"""Synthetic position construction + verification.

The six canonical synthetics implied by put-call parity:

  synthetic long stock   = long call(K)  + short put(K)
  synthetic short stock  = short call(K) + long put(K)
  synthetic long call    = long stock    + long put(K)
  synthetic short call   = short stock   + short put(K)
  synthetic long put     = short stock   + long call(K)
  synthetic short put    = long stock    + short call(K)

The Leg model is options-only — we can't represent a "stock leg" as a
Leg. Stock targets/components are expressed as linear payoff functions
of S_T, and the verifier compares them numerically across a sweep of
S_T to whatever option legs form the synthetic.

`verify_synthetic` returns a max-abs-difference plus a per-S_T table so
you can see the construction holds within numerical tolerance (the
gap is the net premium offset — the synthetic has the same shape but
a parallel offset equal to the cost of the option-leg combo, which is
exactly what parity prices).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence, Union

import numpy as np

from ..core.leg import Leg
from ..core.position import Position
from ..errors import OptionsLabError
from .payoff import expiration_payoff


PayoffFn = Callable[[np.ndarray], np.ndarray]


# ---------- target-payoff helpers (stock side) ----------

def long_stock_payoff(S_ref: float) -> PayoffFn:
    """Long-100-shares per-contract: profit = (S_T − S_ref)."""
    return lambda s: np.asarray(s, dtype=float) - S_ref


def short_stock_payoff(S_ref: float) -> PayoffFn:
    """Short-100-shares per-contract: profit = (S_ref − S_T)."""
    return lambda s: S_ref - np.asarray(s, dtype=float)


# ---------- the six canonical synthetics ----------

def synthetic_long_stock(strike: float, call_premium: float, put_premium: float,
                         expiration: Optional[str] = None) -> Position:
    """Long call + short put at the same K: replicates +1 share unit."""
    return Position.from_legs([
        Leg("long",  "call", strike, call_premium, 1, expiration),
        Leg("short", "put",  strike, put_premium,  1, expiration),
    ], name="synth_long_stock")


def synthetic_short_stock(strike: float, call_premium: float, put_premium: float,
                          expiration: Optional[str] = None) -> Position:
    """Short call + long put at the same K: replicates −1 share unit."""
    return Position.from_legs([
        Leg("short", "call", strike, call_premium, 1, expiration),
        Leg("long",  "put",  strike, put_premium,  1, expiration),
    ], name="synth_short_stock")


def synthetic_long_call(strike: float, put_premium: float,
                        expiration: Optional[str] = None) -> Position:
    """Long stock + long put: replicates a long call. Stock leg is virtual."""
    return Position.from_legs([
        Leg("long", "put", strike, put_premium, 1, expiration),
    ], name="synth_long_call_minus_stock")


def synthetic_short_call(strike: float, put_premium: float,
                         expiration: Optional[str] = None) -> Position:
    """Short stock + short put: replicates a short call."""
    return Position.from_legs([
        Leg("short", "put", strike, put_premium, 1, expiration),
    ], name="synth_short_call_plus_stock")


def synthetic_long_put(strike: float, call_premium: float,
                       expiration: Optional[str] = None) -> Position:
    """Short stock + long call: replicates a long put."""
    return Position.from_legs([
        Leg("long", "call", strike, call_premium, 1, expiration),
    ], name="synth_long_put_plus_stock")


def synthetic_short_put(strike: float, call_premium: float,
                        expiration: Optional[str] = None) -> Position:
    """Long stock + short call: replicates a short put (covered call)."""
    return Position.from_legs([
        Leg("short", "call", strike, call_premium, 1, expiration),
    ], name="synth_short_put_minus_stock")


# ---------- verification ----------

@dataclass(frozen=True, slots=True)
class SyntheticVerifyResult:
    """Per-S_T comparison + summary error metrics."""

    s_t: list[float]
    target_payoff: list[float]
    synthetic_payoff: list[float]
    parallel_offset: float        # mean (target − synthetic); the parity constant
    residual_max: float           # max |target − synthetic − offset|
    matches_within_tol: bool

    def to_dict(self) -> dict:
        return {
            "s_t": [round(x, 4) for x in self.s_t],
            "target_payoff": [round(x, 4) for x in self.target_payoff],
            "synthetic_payoff": [round(x, 4) for x in self.synthetic_payoff],
            "parallel_offset": round(self.parallel_offset, 6),
            "residual_max": round(self.residual_max, 6),
            "matches_within_tol": self.matches_within_tol,
            "interpretation": (
                "synthetic matches target up to a parallel premium offset "
                "(this offset IS the no-arbitrage cost of the synthetic; "
                "set offset to 0 by put-call-parity-consistent premiums)"
            ),
        }


def verify_synthetic(
    target: Union[Position, PayoffFn],
    synthetic: Position,
    *,
    s_range: tuple[float, float],
    points: int = 25,
    tol: float = 1e-6,
) -> SyntheticVerifyResult:
    """Compare two payoffs across a sweep of S_T.

    `target` is either another `Position` or a callable S_T → payoff
    (use the `long_stock_payoff` / `short_stock_payoff` helpers for
    stock-side targets, since the Leg model is options-only).

    "Matches within tol" means residual_max — the max deviation AFTER
    removing the constant parallel offset — is below `tol`. The parallel
    offset itself is the synthetic's net entry cost, which is exactly
    what put-call parity prices.
    """
    s = np.linspace(s_range[0], s_range[1], points)

    if callable(target):
        target_vals = np.asarray(target(s), dtype=float)
    elif isinstance(target, Position):
        target_vals = np.asarray(
            expiration_payoff(target, s).total_per_share, dtype=float
        )
    else:
        raise OptionsLabError(
            "target must be a Position or a callable PayoffFn"
        )

    synth_vals = np.asarray(
        expiration_payoff(synthetic, s).total_per_share, dtype=float
    )

    offset = float(np.mean(target_vals - synth_vals))
    residual = float(np.max(np.abs(target_vals - synth_vals - offset)))
    return SyntheticVerifyResult(
        s_t=s.tolist(),
        target_payoff=target_vals.tolist(),
        synthetic_payoff=synth_vals.tolist(),
        parallel_offset=offset,
        residual_max=residual,
        matches_within_tol=residual <= tol,
    )
