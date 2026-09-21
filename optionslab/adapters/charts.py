"""The sourced-data charts, by name, shared by the CLI (`optionslab plot`) and MCP.

Each entry fetches its feed envelope(s) and draws the matching figure from
`optionslab.plotting`. `render()` saves a PNG (default
`.optionslab/charts/<name>_<args>.png`) and returns its path and bytes.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from matplotlib.figure import Figure

from ..feeds import bars, chains, cot, eia, futures, order_check, pretrade
from ..plotting import market
from ..plotting.sheet import plot_pretrade_sheet
from ..plotting.style import default_chart_path, render_to_png_bytes


def _rv(ticker: str, expiration: str | None = None) -> Figure:
    iv = None
    if expiration:
        chain = chains.chain_report(ticker, expiration, include_rows=False)
        iv = (chain["data"] or {}).get("atm_iv_pct")
    return market.plot_rv_matrix(bars.rv_matrix(ticker), iv_pct=iv)


# name -> (builder, positional argument names); `order` is passed separately.
CHARTS: dict[str, tuple[Callable[..., Figure], tuple[str, ...]]] = {
    "sheet": (lambda ticker, expiration, order=None: plot_pretrade_sheet(
        pretrade.chart_inputs(ticker, expiration, order)), ("ticker", "expiration")),
    "chain": (lambda ticker, expiration: market.plot_chain(
        chains.chain_report(ticker, expiration, include_rows=True)),
        ("ticker", "expiration")),
    "rv": (_rv, ("ticker",)),
    "curve": (lambda root: market.plot_curve_overlay(futures.curve_overlay(root)), ("root",)),
    "cracks": (lambda: market.plot_crack_history(futures.crack_history()), ()),
    "cot": (lambda root: market.plot_cot_history(cot.net_history(root)), ("root",)),
    "eia": (lambda series, region="US": market.plot_eia_band(
        eia.seasonal_history(series, region)), ("series",)),
    "order": (lambda order: market.plot_order(order_check.check_order(order)), ()),
}


def render(name: str, args: list[str], *, order: dict | None = None,
           out: str | None = None) -> tuple[Path, bytes]:
    """Draw chart `name`, save it, return (path, png bytes)."""
    builder, names = CHARTS[name]
    if len(args) < len(names):
        raise ValueError(f"chart {name} needs: {' '.join(names)}")
    if name == "order":
        if order is None:
            raise ValueError("chart order needs an order")
        fig = builder(order)
    elif name == "sheet":
        fig = builder(*args, order=order)
    else:
        fig = builder(*args)
    png = render_to_png_bytes(fig)
    path = Path(out) if out else default_chart_path("_".join([name, *args]) or name)
    path.write_bytes(png)
    return path, png
