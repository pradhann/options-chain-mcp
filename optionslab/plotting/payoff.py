"""Expiration payoff diagrams.

`plot_payoff(position, ...)` covers:
  * a single-leg position (fully annotated: breakeven, max P/L box)
  * any multi-leg position (curve + breakeven lines + metrics annotation)

`plot_primitives(strike, premium)` produces the curriculum 2x2 grid of
long/short × call/put.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from matplotlib.axes import Axes
from matplotlib.figure import Figure
import matplotlib.pyplot as plt

from ..analysis.metrics import position_metrics
from ..analysis.payoff import expiration_payoff
from ..core.leg import Leg
from ..core.position import Position
from .style import (
    ACCENT, CALL_COLOR, LOSS_COLOR, PROFIT_COLOR, SPOT_COLOR,
    apply_style, dollar_axis, save_if_requested,
)


def _domain(strikes: list[float], pad: float = 0.5) -> tuple[float, float]:
    """A sensible S_T range around the position's strikes."""
    lo = min(strikes) * (1 - pad)
    hi = max(strikes) * (1 + pad)
    return max(lo, 0.0), hi


def _fmt_money(v) -> str:
    if v == "Unlimited":
        return "Unlimited"
    return f"${v:,.2f}"


def plot_payoff(
    position: Position,
    *,
    s_range: Optional[tuple[float, float]] = None,
    title: Optional[str] = None,
    ax: Optional[Axes] = None,
    save_path: Optional[str] = None,
) -> Axes:
    """Expiration P&L curve for any position. Returns the Axes.

    The chart shades profit/loss regions, draws vertical lines at each
    breakeven (with labels), and prints max profit / max loss / net cost
    in a corner box.
    """
    strikes = position.strikes
    lo, hi = s_range or _domain(strikes)
    S = np.linspace(lo, hi, 400)
    res = expiration_payoff(position, S)
    m = position_metrics(position)
    pnl = np.asarray(res.total_dollars)

    with apply_style():
        if ax is None:
            _, ax = plt.subplots()
        ax.plot(S, pnl, color=CALL_COLOR, linewidth=2)
        ax.fill_between(S, pnl, 0, where=(pnl >= 0),
                        color=PROFIT_COLOR, alpha=0.15)
        ax.fill_between(S, pnl, 0, where=(pnl < 0),
                        color=LOSS_COLOR, alpha=0.15)
        ax.axhline(0, color="gray", linewidth=0.8, alpha=0.6)
        for k in sorted(set(strikes)):
            ax.axvline(k, color="#888", linestyle=":", linewidth=1, alpha=0.6)
        for be in m.breakevens:
            ax.axvline(be, color=SPOT_COLOR, linestyle="--", linewidth=1.2,
                       alpha=0.8)
            ax.annotate(f"BE ${be:,.2f}", xy=(be, 0),
                        xytext=(4, 8), textcoords="offset points",
                        fontsize=9, color=ACCENT)

        box = (f"Max profit: {_fmt_money(m.max_profit)}\n"
               f"Max loss:  {_fmt_money(m.max_loss)}\n"
               f"Net entry: {_fmt_money(m.net_entry_cost_dollars)}")
        ax.text(0.02, 0.97, box, transform=ax.transAxes,
                va="top", ha="left", fontsize=9, family="monospace",
                bbox=dict(boxstyle="round", facecolor="white",
                          edgecolor="#cccccc", alpha=0.9))

        ax.set_xlabel("Underlying at expiration ($)")
        ax.set_ylabel("Position P&L ($)")
        ax.set_title(title or (position.name or "Position payoff"), pad=8)
        dollar_axis(ax, axis="x", decimals=0)
        dollar_axis(ax, axis="y", decimals=0)
        ax.margins(x=0.02)

        ax.figure.tight_layout()
        save_if_requested(ax.figure, save_path)
    return ax


# ---------- 2x2 primitives grid (curriculum: long/short × call/put) ----------

def _primitive_position(option_type: str, side: str,
                        strike: float, premium: float) -> Position:
    return Position.from_legs([Leg(side=side, option_type=option_type,
                                   strike=strike, premium=premium)],
                              name=f"{side} {option_type}")


def plot_primitives(
    strike: float = 100.0,
    premium: float = 5.0,
    *,
    save_path: Optional[str] = None,
) -> Figure:
    """2x2 grid of the four primitives (long/short × call/put).

    Day-3 curriculum chart. Each panel uses the same `plot_payoff` so it
    inherits the breakeven and max P/L annotations consistently.
    """
    grid_specs = [
        ("call", "long",  0, 0), ("call", "short", 0, 1),
        ("put",  "long",  1, 0), ("put",  "short", 1, 1),
    ]
    with apply_style({"figure.figsize": (13, 9)}):
        fig, axes = plt.subplots(2, 2, sharex=True)
        for ot, side, r_idx, c_idx in grid_specs:
            pos = _primitive_position(ot, side, strike, premium)
            plot_payoff(pos, ax=axes[r_idx, c_idx],
                        title=f"{side.capitalize()} {ot}",
                        s_range=(0.0, 2.0 * strike))
        fig.suptitle(
            f"Option payoff primitives  ·  K=${strike:,.0f}  ·  premium=${premium:,.0f}",
            fontsize=16, fontweight="bold",
        )
        fig.tight_layout(rect=(0, 0, 1, 0.97))
        save_if_requested(fig, save_path)
    return fig


# ---------- vertical-spreads 2x2 grid ----------

def _make_vertical(kind: str, K1: float, K2: float,
                   p1: float, p2: float) -> Position:
    """Build a vertical spread from kind + strikes + premiums."""
    from ..core.leg import Leg as _Leg
    if kind == "bull-call":
        legs = [_Leg("long", "call", K1, p1), _Leg("short", "call", K2, p2)]
        nm = f"Bull call {K1}/{K2}"
    elif kind == "bear-call":
        legs = [_Leg("short", "call", K1, p1), _Leg("long", "call", K2, p2)]
        nm = f"Bear call {K1}/{K2}"
    elif kind == "bull-put":
        legs = [_Leg("long", "put", K1, p1), _Leg("short", "put", K2, p2)]
        nm = f"Bull put {K1}/{K2}"
    elif kind == "bear-put":
        legs = [_Leg("short", "put", K1, p1), _Leg("long", "put", K2, p2)]
        nm = f"Bear put {K1}/{K2}"
    else:
        raise ValueError(f"unknown spread kind {kind!r}")
    return Position.from_legs(legs, name=nm)


def plot_vertical_spreads(
    K1: float, K2: float,
    call_p1: float, call_p2: float,
    put_p1: float, put_p2: float,
    *,
    save_path: Optional[str] = None,
) -> Figure:
    """Day-4 curriculum grid: all four vertical spreads at (K1, K2)."""
    if K1 >= K2:
        raise ValueError(f"K1 must be < K2, got K1={K1} K2={K2}")
    with apply_style({"figure.figsize": (13, 9)}):
        fig, axes = plt.subplots(2, 2, sharex=True)
        cases = [
            ("bull-call", call_p1, call_p2, axes[0, 0], "Bull call (debit)"),
            ("bear-call", call_p1, call_p2, axes[0, 1], "Bear call (credit)"),
            ("bull-put",  put_p1,  put_p2,  axes[1, 0], "Bull put (credit)"),
            ("bear-put",  put_p1,  put_p2,  axes[1, 1], "Bear put (debit)"),
        ]
        for kind, p1, p2, ax, label in cases:
            pos = _make_vertical(kind, K1, K2, p1, p2)
            plot_payoff(pos, ax=ax, title=label,
                        s_range=(K1 * 0.7, K2 * 1.3))
        fig.suptitle(
            f"Vertical spreads  ·  K1={K1:,.0f}  ·  K2={K2:,.0f}",
            fontsize=16, fontweight="bold",
        )
        fig.tight_layout(rect=(0, 0, 1, 0.97))
        save_if_requested(fig, save_path)
    return fig
