"""Thin wrappers that match the Week 1 / Week 2 syllabus signatures.

These are aliases over the new typed API so a student following the
curriculum can verify their own code against ours one-to-one. Pick
whichever name is more comfortable for the day's homework:

  Syllabus name                         | Underlying engine
  --------------------------------------|----------------------------
  option_payoff(S_T, K, t, p, prem)     | expiration_payoff on Position(len=1)
  portfolio_payoff(S_T, legs)           | expiration_payoff(parse_legs(...))
  position_delta(legs, S, T, r, sigma)  | greeks(..., ivs=sigma).delta

Library callers should prefer the typed API directly; these helpers
exist for syllabus-symmetry, not as the public surface.
"""

from __future__ import annotations

from typing import Sequence, Union

import numpy as np

from ..core.leg import Leg
from ..core.market import MarketContext
from ..core.position import Position
from ..pricing import bs_greeks
from .payoff import expiration_payoff


def option_payoff(
    s_t: Union[float, np.ndarray, list[float]],
    strike: float,
    option_type: str,
    position: str,
    premium: float,
) -> Union[float, np.ndarray]:
    """Per-share expiration P&L for one option leg (W1.D3 signature).

    `position` is 'long' or 'short'. Matches the `Leg.side` vocabulary.

    Returns a Python float for scalar `s_t`, a numpy array otherwise.
    Sign convention identical to `core.leg.Leg`.
    """
    pos = Position.from_legs([
        Leg(side=position, option_type=option_type,
            strike=strike, premium=premium),
    ])
    res = expiration_payoff(pos, s_t)
    # per_leg_per_share[0] is exactly the single-leg per-share number.
    return res.per_leg_per_share[0]


def portfolio_payoff(
    s_t: Union[float, np.ndarray, list[float]],
    legs: Sequence,
) -> dict:
    """Multi-leg expiration P&L (W1.D4 signature).

    `legs` accepts either `Leg` instances or list[dict] (the JSON shape).
    Returns the full PayoffResult as a dict so the syllabus's
    expected "sum of leg payoffs" + totals are visible.
    """
    if not legs:
        raise ValueError("legs must be a non-empty sequence")
    if isinstance(legs[0], Leg):
        pos = Position.from_legs(legs)
    else:
        pos = Position.from_dicts(list(legs))
    return expiration_payoff(pos, s_t).to_dict()


def position_delta(
    legs: Sequence,
    S: float,
    T: float,
    r: float,
    sigma: float,
    q: float = 0.0,
) -> float:
    """Qty-weighted portfolio delta (W2.D2 signature).

    Calls `bs_greeks` per leg directly (so this is a self-contained
    teaching reference that doesn't require building a MarketContext).
    """
    if not legs:
        raise ValueError("legs must be a non-empty sequence")
    if isinstance(legs[0], Leg):
        parsed = list(legs)
    else:
        parsed = [Leg.from_dict(d) for d in legs]
    total = 0.0
    for lg in parsed:
        d = float(bs_greeks(S, lg.strike, T, r, sigma,
                            lg.option_type, q)["delta"])
        total += lg.sign * lg.qty * d
    return total
