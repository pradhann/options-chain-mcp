"""Scenario chart: dollar P&L heatmap over spot moves × days forward.

A faithful visual of a `ScenarioResult` matrix. Green = profit, red =
loss. Cell labels show the dollar amount so the chart works as a
reference at a glance.
"""

from __future__ import annotations

from typing import Optional

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.axes import Axes
from matplotlib.colors import TwoSlopeNorm

from ..core.results import ScenarioResult
from .style import apply_style, save_if_requested


def plot_pnl_grid(
    result: ScenarioResult,
    *,
    title: Optional[str] = None,
    ax: Optional[Axes] = None,
    save_path: Optional[str] = None,
) -> Axes:
    """Render a `ScenarioResult` as a diverging heatmap with cell labels.

    Rows = spot moves (% from current). Columns = days forward.
    """
    grid = np.asarray(result.pnl_dollars, dtype=float)
    vmax = float(np.nanmax(np.abs(grid))) or 1.0
    norm = TwoSlopeNorm(vmin=-vmax, vcenter=0.0, vmax=vmax)

    with apply_style({"figure.figsize": (max(8, 1.5 * grid.shape[1] + 4),
                                          max(5, 0.5 * grid.shape[0] + 3))}):
        if ax is None:
            _, ax = plt.subplots()
        im = ax.imshow(grid, cmap="RdYlGn", norm=norm, aspect="auto")

        ax.set_xticks(range(len(result.days_forward)))
        ax.set_xticklabels([f"{int(d)}d" for d in result.days_forward])
        ax.set_yticks(range(len(result.spot_pcts)))
        ax.set_yticklabels([f"{p:+d}%" if float(p).is_integer()
                            else f"{p:+.1f}%" for p in result.spot_pcts])

        for i in range(grid.shape[0]):
            for j in range(grid.shape[1]):
                v = grid[i, j]
                ax.text(j, i, f"${v:,.0f}", ha="center", va="center",
                        fontsize=9,
                        color="black" if abs(v) < 0.4 * vmax else "white")

        ax.set_xlabel("Days forward")
        ax.set_ylabel("Spot move from now")
        ax.set_title(title or f"Position P&L  ·  spot ${result.spot:,.2f}", pad=10)
        cbar = ax.figure.colorbar(im, ax=ax, shrink=0.85)
        cbar.set_label("P&L ($)")

        ax.figure.tight_layout()
        save_if_requested(ax.figure, save_path)
    return ax
