"""Expiration P&L for a Position.

Single function: `expiration_payoff(position, s_t) -> PayoffResult`.
Vectorized — pass a scalar S_T for a single readout, or an array for
plot data. The single-leg case is identical: `Position` of length 1.
"""

from __future__ import annotations

from typing import Union

import numpy as np

from ..core.position import Position
from ..core.results import PayoffResult


CONTRACT_SIZE = 100


def _leg_payoff(option_type: str, side: str, strike: float, premium: float,
                s_t):
    """Per-share P&L at expiration for one leg (vectorized over s_t)."""
    s = np.asarray(s_t, dtype=float)
    intrinsic = (np.maximum(s - strike, 0.0) if option_type == "call"
                 else np.maximum(strike - s, 0.0))
    if side == "long":
        pnl = intrinsic - premium
    else:
        pnl = premium - intrinsic
    return float(pnl) if np.isscalar(s_t) else pnl


def expiration_payoff(
    position: Position,
    s_t: Union[float, np.ndarray, list[float]],
) -> PayoffResult:
    """P&L of a Position at expiration, evaluated at one or many S_T.

    `per_leg_per_share` ignores qty (so spreads read cleanly per
    contract). `total_*` fold in qty and the 100x contract multiplier.
    """
    per_leg = [
        _leg_payoff(lg.option_type, lg.side, lg.strike, lg.premium, s_t)
        for lg in position.legs
    ]
    total_ps = sum(pl * lg.qty for pl, lg in zip(per_leg, position.legs))
    total_dollars = (total_ps * CONTRACT_SIZE
                     if not np.isscalar(total_ps) else float(total_ps * CONTRACT_SIZE))

    # If we got arrays back, keep them as lists for clean JSON later.
    def _listify(x):
        return x.tolist() if isinstance(x, np.ndarray) else x

    return PayoffResult(
        s_t=_listify(np.asarray(s_t)) if not np.isscalar(s_t) else float(s_t),
        per_leg_per_share=[_listify(x) for x in per_leg],
        total_per_share=_listify(total_ps),
        total_dollars=_listify(total_dollars),
    )
