"""Greek-vs-spot teaching/verification chart.

Default plots delta — the classic sigmoid. Pass `greek` to swap to
gamma/theta/vega/rho. Optional `overlay_oracle=True` draws py_vollib
markers so the analytic curve can be visibly cross-checked.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.axes import Axes

from ..pricing import bs_greeks
from .style import CALL_COLOR, PUT_COLOR, SPOT_COLOR, apply_style, dollar_axis, save_if_requested

_GREEK_LABEL = {
    "delta": ("Delta", "per +$1 spot"),
    "gamma": ("Gamma", "per +$1 spot"),
    "theta": ("Theta", "per +1 calendar day"),
    "vega":  ("Vega",  "per +1 IV point (0.01 σ)"),
    "rho":   ("Rho",   "per +1 percentage point in r"),
}


def _pyvollib_oracle(greek, S_arr, K, T, r, sigma, option_type, q):
    """py_vollib values at S_arr, or None if unavailable."""
    try:
        from py_vollib.black_scholes_merton.greeks import analytical as pvg
    except Exception:
        return None
    flag = "c" if option_type == "call" else "p"
    fn = {"delta": pvg.delta, "gamma": pvg.gamma, "theta": pvg.theta,
          "vega": pvg.vega, "rho": pvg.rho}.get(greek)
    if fn is None:
        return None
    try:
        return np.array([float(fn(flag, float(S), K, T, r, sigma, q))
                         for S in S_arr])
    except Exception:
        return None


def plot_greek_vs_spot(
    strike: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str,
    q: float = 0.0,
    *,
    greek: str = "delta",
    s_range: tuple[float, float] | None = None,
    points: int = 200,
    overlay_oracle: bool = False,
    ax: Axes | None = None,
    save_path: str | None = None,
) -> Axes:
    """A Greek as a function of spot, for fixed K, T, r, σ, q."""
    greek = greek.lower()
    if greek not in _GREEK_LABEL:
        raise ValueError(
            f"greek must be one of {list(_GREEK_LABEL)}, got {greek!r}"
        )

    lo, hi = s_range or (0.5 * strike, 1.5 * strike)
    S = np.linspace(lo, hi, points)
    g = bs_greeks(S, strike, T, r, sigma, option_type, q)
    y = g[greek]
    label, conv = _GREEK_LABEL[greek]
    color = CALL_COLOR if option_type == "call" else PUT_COLOR

    with apply_style():
        if ax is None:
            _, ax = plt.subplots()
        ax.plot(S, y, color=color, linewidth=2,
                label=f"{option_type.capitalize()} {greek}")
        ax.axhline(0, color="gray", linewidth=0.6, alpha=0.5)
        ax.axvline(strike, color=SPOT_COLOR, linestyle="--", linewidth=1.2,
                   alpha=0.7, label=f"Strike ${strike:,.0f}")

        if greek == "delta":
            atm = bs_greeks(strike, strike, T, r, sigma, option_type, q)["delta"]
            otm_S = lo if option_type == "call" else hi
            itm_S = hi if option_type == "call" else lo
            otm_val = bs_greeks(otm_S, strike, T, r, sigma, option_type, q)["delta"]
            itm_val = bs_greeks(itm_S, strike, T, r, sigma, option_type, q)["delta"]
            for sx, sy, tag in (
                (otm_S, otm_val, f"Deep OTM\n{otm_val:+.2f}"),
                (strike, atm, f"ATM ≈ {atm:+.2f}"),
                (itm_S, itm_val, f"Deep ITM\n{itm_val:+.2f}"),
            ):
                ax.scatter([sx], [sy], color=color, zorder=5, s=35)
                ax.annotate(tag, xy=(sx, sy), xytext=(0, 14),
                            textcoords="offset points", ha="center",
                            fontsize=9, color="#444")

        if overlay_oracle:
            step = max(1, points // 20)
            oracle = _pyvollib_oracle(greek, S[::step], strike, T, r,
                                      sigma, option_type, q)
            if oracle is not None:
                ax.scatter(S[::step], oracle, marker="x", color="black",
                           alpha=0.7, s=30, label="py_vollib oracle",
                           zorder=4)

        ax.set_xlabel("Spot price S ($)")
        ax.set_ylabel(f"{label}  ({conv})")
        ax.set_title(
            f"{label} vs spot  ·  {option_type}  ·  K=${strike:,.0f}"
            f"  ·  T={T:.2f}y  ·  σ={sigma:.0%}  ·  r={r:.1%}"
            + (f"  ·  q={q:.1%}" if q else ""),
            pad=10,
        )
        dollar_axis(ax, axis="x", decimals=0)
        ax.margins(x=0.02)
        ax.legend(loc="best", fontsize=9)
        ax.figure.tight_layout()
        save_if_requested(ax.figure, save_path)
    return ax


def plot_greek_vs_time(
    strike: float,
    spot: float,
    r: float,
    sigma: float,
    option_type: str,
    q: float = 0.0,
    *,
    greek: str = "gamma",
    t_range_days: tuple[float, float] | None = None,
    points: int = 200,
    ax: Axes | None = None,
    save_path: str | None = None,
) -> Axes:
    """A Greek as a function of time to expiry, for fixed S, K, r, σ, q.

    Covers the canonical curriculum views:
      * gamma vs T : peak grows and narrows as T → 0 (W2.D3)
      * theta vs T : asymptotic acceleration toward expiry (W2.D4)
      * vega vs T  : ~√T scaling (W2.D5)

    `t_range_days` defaults to (1, 365). Days-to-expiry on the x-axis;
    Greek on the y-axis.
    """
    greek = greek.lower()
    if greek not in _GREEK_LABEL:
        raise ValueError(
            f"greek must be one of {list(_GREEK_LABEL)}, got {greek!r}"
        )

    lo_days, hi_days = t_range_days or (1.0, 365.0)
    if lo_days <= 0:
        lo_days = 0.5     # avoid the T=0 singularity in some greeks
    days = np.linspace(lo_days, hi_days, points)
    T = days / 365.0
    g = bs_greeks(spot, strike, T, r, sigma, option_type, q)
    y = g[greek]

    label, conv = _GREEK_LABEL[greek]
    color = CALL_COLOR if option_type == "call" else PUT_COLOR

    with apply_style():
        if ax is None:
            _, ax = plt.subplots()
        ax.plot(days, y, color=color, linewidth=2,
                label=f"{option_type.capitalize()} {greek}")
        ax.axhline(0, color="gray", linewidth=0.6, alpha=0.5)

        ax.set_xlabel("Days to expiration")
        ax.set_ylabel(f"{label}  ({conv})")
        ax.set_title(
            f"{label} vs time  ·  {option_type}  ·  S=${spot:,.0f}"
            f"  ·  K=${strike:,.0f}  ·  σ={sigma:.0%}  ·  r={r:.1%}"
            + (f"  ·  q={q:.1%}" if q else ""),
            pad=10,
        )
        ax.margins(x=0.02)
        ax.legend(loc="best", fontsize=9)
        ax.figure.tight_layout()
        save_if_requested(ax.figure, save_path)
    return ax
