"""Closed-form structural metrics: max profit, max loss, breakeven(s).

Works for any piecewise-linear position (any combination of long/short
calls/puts at any strikes). The algorithm:

  1. Compute the asymptotic slopes of payoff(S) as S→0+ and S→∞ from
     the qty-weighted sums of call/put indicators per leg.
  2. Evaluate payoff exactly at S=0, every strike (the only kinks), and
     a very large S. Walk the segments to find max, min, and zero
     crossings.

This is exact (not a search) and works whether you've got a butterfly,
a calendar (treated as same-T here), or a custom 7-leg structure.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np

from ..core.position import Position
from ..core.results import MetricsResult
from .payoff import _leg_payoff


CONTRACT_SIZE = 100


def _slopes(position: Position) -> tuple[float, float]:
    """Asymptotic slopes (per-share) at S→0+ and S→∞, qty- and side-signed."""
    s_left = s_right = 0.0
    for lg in position.legs:
        sign = lg.sign
        if lg.is_call:
            s_right += sign * lg.qty            # call slope → +1 at S→∞
        else:
            s_left += -sign * lg.qty            # put slope → -1 at S→0+
    return s_left, s_right


def _payoff_at_per_share(position: Position, s: float) -> float:
    """Per-share P&L at S (qty-weighted), no contract multiplier."""
    total = 0.0
    for lg in position.legs:
        total += lg.qty * _leg_payoff(
            lg.option_type, lg.side, lg.strike, lg.premium, s
        )
    return float(total)


def _zero_crossing(s_a: float, p_a: float, s_b: float, p_b: float):
    """Linear root of pnl(S) on segment [a,b]; None if no sign change."""
    if p_a == 0.0:
        return s_a
    if p_b == 0.0:
        return s_b
    if (p_a > 0) == (p_b > 0):
        return None
    return s_a + (-p_a) * (s_b - s_a) / (p_b - p_a)


def position_metrics(position: Position) -> MetricsResult:
    """Closed-form max P, max L, breakeven(s), and net entry cost.

    `max_profit`/`max_loss` are returned in DOLLARS (not per-share),
    with the string `'Unlimited'` when unbounded. `net_entry_cost_dollars`
    is positive for a debit position, negative for a credit.
    """
    strikes = sorted(set(lg.strike for lg in position.legs))
    s_left, s_right = _slopes(position)

    # Evaluation grid: 0, every strike (the only kinks), and a far-right point.
    far = max(strikes) * 4 if strikes else 1.0
    eval_pts = [0.0] + strikes + [far]
    payoffs = [_payoff_at_per_share(position, s) for s in eval_pts]

    # Max/min on the kink set + tail behavior.
    if s_right > 0:
        max_p: float | str = "Unlimited"
    elif s_right < 0:
        max_p = round(max(payoffs) * CONTRACT_SIZE, 4)
    else:
        max_p = round(max(max(payoffs), payoffs[-1]) * CONTRACT_SIZE, 4)

    if s_left < 0:
        max_l: float | str = "Unlimited"
    elif s_left > 0:
        max_l = round(min(payoffs) * CONTRACT_SIZE, 4)
    else:
        max_l = round(min(min(payoffs), payoffs[0]) * CONTRACT_SIZE, 4)

    # Breakevens: zero crossings on every segment.
    breakevens: list[float] = []
    for (sa, pa), (sb, pb) in zip(
        zip(eval_pts, payoffs), zip(eval_pts[1:], payoffs[1:])
    ):
        z = _zero_crossing(sa, pa, sb, pb)
        if z is not None and (not breakevens or abs(z - breakevens[-1]) > 1e-6):
            breakevens.append(round(z, 4))

    # Net entry cost (debit = +, credit = -).
    net_entry = sum(
        (1 if lg.side == "long" else -1) * lg.premium * lg.qty * CONTRACT_SIZE
        for lg in position.legs
    )

    return MetricsResult(
        max_profit=max_p,
        max_loss=max_l,
        breakevens=breakevens,
        net_entry_cost_dollars=round(net_entry, 4),
    )
