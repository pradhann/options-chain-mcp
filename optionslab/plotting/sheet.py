"""The pre-trade sheet: one figure, eight panels, every panel sourced.

    ┌───────────────────────┬───────────────────────┐
    │ red flags             │ proposed order        │
    ├───────────────────────┼───────────────────────┤
    │ OI walls              │ realized-vol matrix   │
    ├───────────────────────┼───────────────────────┤
    │ crack spreads         │ COT managed money     │
    ├───────────────────────┼───────────────────────┤
    │ EIA seasonal band     │ futures curve overlay │
    └───────────────────────┴───────────────────────┘

Inputs come from `feeds.pretrade.chart_inputs`, so the figure and the
text page are drawn from the same envelopes.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
from matplotlib.figure import Figure

from .market import (
    plot_cot_history,
    plot_crack_history,
    plot_curve_overlay,
    plot_eia_band,
    plot_oi_walls,
    plot_order,
    plot_rv_matrix,
)
from .style import INK, LOSS_COLOR, MUTED, PROFIT_COLOR, apply_style, stamp, title_block


def _flags_panel(ax, page: dict) -> None:
    ax.set_axis_off()
    d = page["data"]
    flags = d["flags"]
    title_block(ax, f"{d['symbol']} {d['expiration']} — {len(flags)} red flag(s)",
                f"as of {d['asof_date']}{' (offline snapshots)' if d['offline'] else ''} · "
                f"{len(d['unsourced'])} line(s) not verified")
    if not flags:
        ax.text(0.0, 0.85, "✓ no red flags", color=PROFIT_COLOR, fontsize=12,
                transform=ax.transAxes)
        return
    for i, f in enumerate(flags[:12]):
        y = 0.92 - i * 0.075
        ax.text(0.0, y, f"✗ {f['flag']}", color=LOSS_COLOR, fontsize=10, weight="bold",
                transform=ax.transAxes)
        ax.text(0.42, y, f"[{f['section']}] {f['detail']}"[:70], color=INK, fontsize=9,
                transform=ax.transAxes)
    if len(flags) > 12:
        ax.text(0.0, 0.0, f"+{len(flags) - 12} more", color=MUTED, transform=ax.transAxes)


def plot_pretrade_sheet(inputs: dict) -> Figure:
    """Draw the sheet from `chart_inputs`: {page, chain, rv, order, cracks, cot, eia, curve}."""
    with apply_style({"font.size": 9.5, "axes.titlesize": 12}):
        fig, axes = plt.subplots(4, 2, figsize=(17, 20), layout="constrained")
        fig.get_layout_engine().set(rect=(0, 0.015, 1, 0.985), h_pad=0.25, w_pad=0.3)
        _flags_panel(axes[0, 0], inputs["page"])
        plot_order(inputs["order"], ax=axes[0, 1])
        plot_oi_walls(inputs["chain"], ax=axes[1, 0])
        atm = (inputs["chain"]["data"] or {}).get("atm_iv_pct")
        plot_rv_matrix(inputs["rv"], iv_pct=atm, ax=axes[1, 1])
        plot_crack_history(inputs["cracks"], ax=axes[2, 0])
        plot_cot_history(inputs["cot"], ax=axes[2, 1])
        plot_eia_band(inputs["eia"], ax=axes[3, 0])
        plot_curve_overlay(inputs["curve"], ax=axes[3, 1])
        stamp(fig, {"source": "optionslab pretrade (each panel: its own feed; see JSON "
                              "provenance)",
                    "asof": inputs["page"]["data"]["asof_date"],
                    "quality": inputs["page"]["provenance"]["quality"]})
        return fig
