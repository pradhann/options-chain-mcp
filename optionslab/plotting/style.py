"""Shared plotting style + small helpers.

Every chart in this package wraps its work in `apply_style()` so colors,
fonts, and spines are consistent without each function copy-pasting an
rc_context block.
"""

from __future__ import annotations

import io
from contextlib import contextmanager

import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.ticker import FormatStrFormatter

# Brand-ish palette — kept small and stable.
CALL_COLOR = "#1f77b4"
PUT_COLOR = "#d62728"
SPOT_COLOR = "#222222"
PROFIT_COLOR = "#2ca02c"
LOSS_COLOR = "#d62728"
ACCENT = "#444"

_STYLE = {
    "figure.figsize": (11, 6.5),
    "figure.dpi": 120,
    "axes.titlesize": 15,
    "axes.titleweight": "bold",
    "axes.labelsize": 12,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "legend.frameon": False,
    "font.size": 11,
}


@contextmanager
def apply_style(overrides: dict | None = None):
    """Context manager: apply the shared style for the duration of a chart."""
    style = {**_STYLE, **(overrides or {})}
    with plt.rc_context(style):
        yield


def dollar_axis(ax, *, axis: str = "x", decimals: int = 0) -> None:
    """Format an axis with a $ prefix and fixed decimals."""
    fmt = FormatStrFormatter(f"$%.{decimals}f")
    (ax.xaxis if axis == "x" else ax.yaxis).set_major_formatter(fmt)


def render_to_png_bytes(fig: Figure, dpi: int = 120) -> bytes:
    """Serialize a Matplotlib figure to PNG bytes (for MCP image responses)."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=dpi)
    return buf.getvalue()


def save_if_requested(fig: Figure, save_path: str | None) -> None:
    """Tiny helper — half the chart functions had this two-liner inline."""
    if save_path:
        fig.savefig(save_path, bbox_inches="tight")
