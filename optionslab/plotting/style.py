"""The one chart theme: palette, typography, titles, money axes, provenance footer.

Every chart in `optionslab.plotting` follows the same layout:

  * a left-aligned bold title that names the thing, and a grey subtitle
    that states the takeaway in numbers (`title_block`)
  * a footer with source, as-of and data quality (`stamp`), so a chart
    pasted anywhere still says where its numbers came from
  * the palette below; calls are blue, puts red, spot ink, parity violet,
    ranges and bands light slate, the thing to look at amber
"""

from __future__ import annotations

import io
import textwrap
from contextlib import contextmanager
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

INK = "#1f2933"
MUTED = "#6b7280"
GRID = "#e5e7eb"
CALL_COLOR = "#2563eb"
PUT_COLOR = "#dc2626"
SPOT_COLOR = "#111827"
PARITY_COLOR = "#7c3aed"
PROFIT_COLOR = "#16a34a"
LOSS_COLOR = "#dc2626"
BAND_COLOR = "#cbd5e1"
HIGHLIGHT = "#d97706"
ACCENT = MUTED
SERIES = (CALL_COLOR, "#0d9488", HIGHLIGHT, PARITY_COLOR, MUTED)

_STYLE = {
    "figure.figsize": (11, 6.2),
    "figure.dpi": 110,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "axes.edgecolor": "#9ca3af",
    "axes.labelcolor": INK,
    "axes.titlesize": 14,
    "axes.titleweight": "bold",
    "axes.titlelocation": "left",
    "axes.labelsize": 11,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "axes.prop_cycle": plt.cycler(color=SERIES),
    "grid.color": GRID,
    "grid.linewidth": 0.8,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "legend.frameon": False,
    "legend.fontsize": 9.5,
    "font.size": 10.5,
    "lines.linewidth": 2.0,
}
SAVE_DPI = 160
CHART_DIR = "charts"


@contextmanager
def apply_style(overrides: dict | None = None):
    """Apply the shared theme for the duration of a chart."""
    with plt.rc_context({**_STYLE, **(overrides or {})}):
        yield


def title_block(ax: Axes, title: str, subtitle: str | None = None, *,
                width: int = 110) -> None:
    """Bold left title; the subtitle carries the takeaway in grey, wrapped at `width`."""
    lines = textwrap.wrap(subtitle, width) if subtitle else []
    ax.set_title(title, loc="left", pad=10 + 14 * len(lines), color=INK)
    if lines:
        ax.text(0, 1.015, "\n".join(lines), transform=ax.transAxes, ha="left",
                va="bottom", fontsize=9.5, color=MUTED, linespacing=1.3)


def stamp(fig: Figure, provenance: dict | None) -> None:
    """Footer: source · as of · quality (left) and the package name (right)."""
    if provenance:
        parts = [provenance.get("source"), f"as of {provenance.get('asof')}",
                 provenance.get("quality")]
        fig.text(0.01, 0.005, "Source: " + " · ".join(p for p in parts if p),
                 fontsize=7.5, color=MUTED, ha="left", va="bottom")
    fig.text(0.99, 0.005, "optionslab", fontsize=7.5, color=MUTED, ha="right", va="bottom")


def _money(v: float, decimals: int = 0) -> str:
    sign = "−" if v < 0 else ""
    return f"{sign}${abs(v):,.{decimals}f}"


def dollar_axis(ax: Axes, *, axis: str = "x", decimals: int = 0) -> None:
    """Dollar tick labels with a true minus sign: −$400, not $-400."""
    fmt = FuncFormatter(lambda v, _pos: _money(v, decimals))
    (ax.xaxis if axis == "x" else ax.yaxis).set_major_formatter(fmt)


def percent_axis(ax: Axes, *, axis: str = "y", decimals: int = 0) -> None:
    fmt = FuncFormatter(lambda v, _pos: f"{v:.{decimals}f}%")
    (ax.xaxis if axis == "x" else ax.yaxis).set_major_formatter(fmt)


def vline(ax: Axes, x: float, label: str, color: str = SPOT_COLOR, style: str = "-",
          row: int = 0) -> None:
    """A labelled vertical reference line; `row` staggers labels of nearby lines."""
    ax.axvline(x, color=color, linestyle=style, linewidth=1.2, alpha=0.9)
    if label:
        ax.annotate(label, (x, 1), xycoords=("data", "axes fraction"),
                    xytext=(4, -12 - 13 * row), textcoords="offset points", fontsize=8.5,
                    color=color, bbox={"boxstyle": "square,pad=0.1", "fc": "white",
                                       "ec": "none", "alpha": 0.8})


def money(v: float, decimals: int = 0) -> str:
    """−$5,500 style (true minus) for titles and labels."""
    return _money(v, decimals)


def date_ticks(ax: Axes) -> None:
    """Rotate date tick labels on this axes only (fig.autofmt_xdate hides other rows)."""
    for label in ax.get_xticklabels():
        label.set_rotation(30)
        label.set_ha("right")


def render_to_png_bytes(fig: Figure, dpi: int = SAVE_DPI) -> bytes:
    """Serialize a figure to PNG bytes (MCP image responses)."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=dpi)
    return buf.getvalue()


def save_if_requested(fig: Figure, save_path: str | None) -> None:
    if save_path:
        fig.savefig(save_path, bbox_inches="tight", dpi=SAVE_DPI)


def default_chart_path(name: str) -> Path:
    """`.optionslab/charts/<name>.png` (created on demand), never the repo root."""
    from ..storage.snapshots import home

    out = home() / CHART_DIR
    out.mkdir(parents=True, exist_ok=True)
    return out / f"{name}.png"
