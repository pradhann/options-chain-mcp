"""Plotting subpackage. One file per chart family, shared style.

  style       — colors, rc_context, render-to-PNG helpers
  payoff      — single + multi-leg payoff + 2x2 primitives + verticals
  greek       — greek-vs-spot teaching chart
  chain       — extrinsic-by-strike
  scenario    — P&L grid heatmap

Library functions return the Matplotlib `Axes` or `Figure` and never
call `plt.show()`. Display is the caller's concern; the CLI shows, a
notebook renders inline, an MCP tool renders to PNG via
`style.render_to_png_bytes`.
"""

from .chain import plot_extrinsic
from .greek import plot_greek_vs_spot, plot_greek_vs_time
from .payoff import plot_payoff, plot_primitives, plot_vertical_spreads
from .scenario import plot_pnl_grid
from .style import render_to_png_bytes
from .vol import (
    plot_dashboard_panel,
    plot_iv_smile,
    plot_rv_estimators,
    plot_skew_curve,
    plot_term_structure,
    plot_vix_strip_overlay,
    plot_vrp,
)

__all__ = [
    "plot_payoff",
    "plot_primitives",
    "plot_vertical_spreads",
    "plot_greek_vs_spot",
    "plot_greek_vs_time",
    "plot_extrinsic",
    "plot_pnl_grid",
    "render_to_png_bytes",
    # Week 3
    "plot_rv_estimators", "plot_iv_smile", "plot_vrp",
    "plot_term_structure", "plot_skew_curve", "plot_vix_strip_overlay",
    "plot_dashboard_panel",
]
