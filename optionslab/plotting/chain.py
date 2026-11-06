"""Chain-level charts (currently: extrinsic value by strike).

Both call and put tents peak near spot — that's where optionality is
richest. By put-call parity the two curves should nearly overlay; gaps
are bid/ask noise, carry, or expected dividends.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd
from matplotlib.axes import Axes
import matplotlib.pyplot as plt

from .style import (
    CALL_COLOR, PUT_COLOR, SPOT_COLOR,
    apply_style, dollar_axis, save_if_requested,
)


def _annotate_peak(ax: Axes, df: pd.DataFrame, color: str, label: str,
                   text_xy: tuple[float, float]) -> None:
    if df.empty:
        return
    row = df.loc[df["Extrinsic"].idxmax()]
    ax.annotate(
        f"{label} peak  ${row['Extrinsic']:.2f} @ {row['Strike']:.0f}",
        xy=(row["Strike"], row["Extrinsic"]),
        xycoords="data",
        xytext=text_xy, textcoords="axes fraction",
        ha="center", fontsize=9, color=color,
        arrowprops=dict(arrowstyle="-", color=color, alpha=0.45, lw=1,
                        connectionstyle="arc3,rad=0.2"),
    )


def plot_extrinsic(
    calls: pd.DataFrame,
    puts: pd.DataFrame,
    spot: float,
    symbol: str,
    expiration: str,
    *,
    ax: Optional[Axes] = None,
    save_path: Optional[str] = None,
) -> Axes:
    """Extrinsic value vs strike for calls (blue) and puts (red)."""
    with apply_style():
        if ax is None:
            _, ax = plt.subplots()
        ax.plot(calls["Strike"], calls["Extrinsic"],
                marker="o", markersize=4, linewidth=1.6,
                label="Calls", color=CALL_COLOR)
        ax.plot(puts["Strike"], puts["Extrinsic"],
                marker="o", markersize=4, linewidth=1.6,
                label="Puts", color=PUT_COLOR)
        ax.axvline(spot, color=SPOT_COLOR, linestyle="--", linewidth=1.2,
                   alpha=0.8, label=f"Spot ${spot:,.2f}")
        ax.axhline(0, color="gray", linewidth=0.6, alpha=0.5)

        _annotate_peak(ax, calls, CALL_COLOR, "Call", text_xy=(0.28, 0.80))
        _annotate_peak(ax, puts, PUT_COLOR, "Put", text_xy=(0.30, 0.62))

        ax.set_xlabel("Strike price ($)")
        ax.set_ylabel("Extrinsic (time) value ($)")
        ax.set_title(
            f"{symbol} · extrinsic value by strike · exp {expiration}",
            pad=12,
        )
        dollar_axis(ax, axis="x", decimals=0)
        dollar_axis(ax, axis="y", decimals=2)
        ax.margins(x=0.02)
        ax.legend(loc="upper right")
        ax.figure.tight_layout()
        save_if_requested(ax.figure, save_path)
    return ax
