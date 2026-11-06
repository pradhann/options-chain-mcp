"""MCP server — same verbs as the CLI, JSON-typed at the edge.

Run:
    python -m optionslab.mcp_server

The naming convention here mirrors the CLI:
  chain / payoff / value / greeks / metrics / scenario  → analysis
  chart_*                                                → plotting
  positions_*                                            → storage
  realized_vol, iv_term_structure, ...                   → data

Position-taking tools accept a `position` dict shaped like
`Position.to_dict()` — `{"legs": [...], "name?": ..., ...}` — OR a bare
list of leg dicts. They also accept `position_name` to load from
`.optionslab/positions.json`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")  # headless before any pyplot import

from mcp.server.fastmcp import FastMCP, Image

from ..analysis.metrics import position_metrics as _metrics
from ..analysis.payoff import expiration_payoff
from ..analysis.scenario import scenario_grid, theoretical_price as _theoretical
from ..analysis.valuation import greeks as _greeks
from ..analysis.valuation import value as _value
from ..core.market import MarketContext
from ..core.position import Position
from ..data.chain import filter_liquid, filter_near_money, load_chain
from ..data.events import analyst_targets as _targets
from ..data.events import next_earnings as _earnings
from ..data.events import recent_news as _news
from ..data.quotes import (
    get_dividend_yield, get_risk_free_rate, get_spot, list_expirations,
    make_ticker,
)
from ..analysis.parity import parity_check as _parity_check
from ..analysis.synthetics import (
    long_stock_payoff, short_stock_payoff,
    synthetic_long_call, synthetic_long_put, synthetic_long_stock,
    synthetic_short_call, synthetic_short_put, synthetic_short_stock,
    verify_synthetic as _verify_synthetic,
)
from ..analysis.vol.dashboard import vol_dashboard as _vol_dashboard
from ..analysis.vol.event import event_implied_move as _event_implied_move
from ..analysis.vol.skew import skew_metrics as _skew_metrics
from ..analysis.vol.strip import vix_strip as _vix_strip
from ..analysis.vol.term import term_structure as _term_structure
from ..analysis.vol.vrp import vrp_history as _vrp_history
from ..analysis.vol.vrp import vrp_today as _vrp_today
from ..data.vol import iv_term_structure as _iv_term
from ..data.vol import realized_vol as _rv
from ..data.vol import realized_vol_all_estimators as _rv_all
from ..errors import OptionsLabError
from ..plotting.chain import plot_extrinsic
from ..plotting.greek import plot_greek_vs_spot
from ..plotting.payoff import plot_payoff, plot_primitives, plot_vertical_spreads
from ..plotting.scenario import plot_pnl_grid
from ..plotting.style import render_to_png_bytes
from ..pricing import year_fraction
from ..storage.positions import (
    delete_position, get_position, list_positions, save_position,
)


mcp = FastMCP("optionslab")


# ---------- helpers ----------

def _resolve_position(position, position_name) -> Position:
    """Accept dict, list-of-leg-dicts, or a saved name."""
    if (position is None) == (position_name is None):
        raise ValueError(
            "supply exactly one of `position` (dict|list) or `position_name`"
        )
    if position_name is not None:
        return get_position(position_name)
    if isinstance(position, dict):
        return Position.from_dict(position)
    if isinstance(position, list):
        return Position.from_dicts(position)
    raise ValueError("`position` must be a dict or a list of leg dicts")


def _market(spot: float, r: Optional[float], q: float) -> MarketContext:
    return MarketContext.explicit(
        spot=spot,
        r=r if r is not None else get_risk_free_rate(),
        q=q,
    )


def _deliver(fig, default_name: str, save_path: Optional[str]) -> list:
    """Return [inline image, saved-path note] and always write a copy."""
    png = render_to_png_bytes(fig)
    path = Path(save_path) if save_path else Path.cwd() / default_name
    path.write_bytes(png)
    return [Image(data=png, format="png"), f"Saved a copy to {path}"]


# ---------- data ----------

@mcp.tool()
def get_spot_price(ticker: str) -> float:
    """Current underlying price."""
    return get_spot(make_ticker(ticker))


@mcp.tool()
def list_expirations_tool(ticker: str) -> list[str]:
    """All listed option expiration dates, nearest first."""
    return list_expirations(make_ticker(ticker))


@mcp.tool()
def chain(
    ticker: str,
    expiration: Optional[str] = None,
    exp_index: Optional[int] = None,
    near_money: int = 0,
) -> dict:
    """Decomposed options chain with Greeks per strike.

    Columns: Bid/Ask/Mid/Intrinsic/Extrinsic/IV%/OI/Vol +
    Delta/Gamma/Theta/Vega/Rho. r=live T-bill, q=trailing div yield.
    Conventions: delta per $1, theta per day, vega per IV point.
    """
    ch = load_chain(ticker, expiration=expiration, exp_index=exp_index)
    if near_money > 0:
        from dataclasses import replace
        ch = replace(
            ch,
            calls=filter_near_money(ch.calls, ch.spot, near_money),
            puts=filter_near_money(ch.puts, ch.spot, near_money),
        )
    return ch.to_records()


@mcp.tool()
def realized_vol(ticker: str, window_days: int = 30) -> dict:
    """Annualized realized volatility (%)."""
    return {"ticker": ticker.upper(), "window_days": window_days,
            "realized_vol_pct": _rv(ticker, window_days)}


@mcp.tool()
def iv_term_structure(ticker: str, max_expirations: int = 12) -> dict:
    """ATM IV (%) by expiration."""
    return {"ticker": ticker.upper(),
            "atm_iv_pct_by_expiration": _iv_term(ticker, max_expirations)}


@mcp.tool()
def next_earnings(ticker: str) -> dict:
    """Next earnings date + EPS/revenue estimates."""
    return _earnings(make_ticker(ticker))


@mcp.tool()
def recent_news(ticker: str, n: int = 10) -> list:
    """Recent headlines."""
    return _news(make_ticker(ticker), n)


@mcp.tool()
def analyst_targets(ticker: str) -> dict:
    """Analyst price targets + rating."""
    return _targets(make_ticker(ticker))


# ---------- analysis: positions ----------

@mcp.tool()
def payoff(
    s_t: float,
    position: Optional[object] = None,
    position_name: Optional[str] = None,
) -> dict:
    """Expiration P&L for a position at price `s_t`."""
    pos = _resolve_position(position, position_name)
    return expiration_payoff(pos, s_t).to_dict()


@mcp.tool()
def metrics(
    position: Optional[object] = None,
    position_name: Optional[str] = None,
) -> dict:
    """Closed-form max profit, max loss, breakeven(s), net entry cost."""
    pos = _resolve_position(position, position_name)
    return _metrics(pos).to_dict()


@mcp.tool()
def value(
    spot: float,
    sigma: Optional[float] = None,
    r: Optional[float] = None,
    q: float = 0.0,
    position: Optional[object] = None,
    position_name: Optional[str] = None,
) -> dict:
    """Mark-to-model dollar value (+ P&L vs entry) for a position."""
    pos = _resolve_position(position, position_name)
    return _value(pos, _market(spot, r, q), ivs=sigma).to_dict()


@mcp.tool()
def greeks(
    spot: float,
    sigma: Optional[float] = None,
    r: Optional[float] = None,
    q: float = 0.0,
    position: Optional[object] = None,
    position_name: Optional[str] = None,
) -> dict:
    """Portfolio Greeks (delta/gamma/theta/vega/rho) with $ versions."""
    pos = _resolve_position(position, position_name)
    return _greeks(pos, _market(spot, r, q), ivs=sigma).to_dict()


@mcp.tool()
def scenario(
    spot: float,
    sigma: Optional[float] = None,
    r: Optional[float] = None,
    q: float = 0.0,
    position: Optional[object] = None,
    position_name: Optional[str] = None,
    spot_pcts: Optional[list[float]] = None,
    days_forward: Optional[list[float]] = None,
) -> dict:
    """Dollar P&L matrix: spot moves × days forward."""
    pos = _resolve_position(position, position_name)
    res = scenario_grid(pos, _market(spot, r, q),
                        spot_pcts=spot_pcts, days_forward=days_forward,
                        ivs=sigma)
    return res.to_dict()


@mcp.tool()
def theoretical_price(
    ticker: str,
    strike: float,
    expiration: str,
    option_type: str,
    s_now: Optional[float] = None,
    s_future: Optional[float] = None,
    days_forward: float = 0.0,
    iv: Optional[float] = None,
    r: Optional[float] = None,
    q: Optional[float] = None,
) -> dict:
    """Black-Scholes value + Greeks at a hypothetical spot/time."""
    return _theoretical(
        ticker, strike, expiration, option_type,
        s_now=s_now, s_future=s_future, days_forward=days_forward,
        iv=iv, r=r, q=q,
    )


# ---------- positions storage ----------

@mcp.tool()
def positions_list() -> list[str]:
    """List names of saved positions in .optionslab/positions.json."""
    return list_positions()


@mcp.tool()
def positions_get(name: str) -> dict:
    """Load a saved position by name."""
    return get_position(name).to_dict()


@mcp.tool()
def positions_save(
    name: str,
    legs: list[dict],
    symbol: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    """Save (or overwrite) a named position."""
    pos = Position.from_dicts(legs, name=name, symbol=symbol, notes=notes)
    path = save_position(pos)
    return {"saved": name, "path": str(path)}


@mcp.tool()
def positions_delete(name: str) -> dict:
    """Delete a saved position by name."""
    ok = delete_position(name)
    return {"deleted": ok, "name": name}


# ---------- charts ----------

@mcp.tool()
def chart_payoff(
    position: Optional[object] = None,
    position_name: Optional[str] = None,
    title: Optional[str] = None,
    save_path: Optional[str] = None,
) -> list:
    """Expiration payoff curve (any position). Inline image + saved copy."""
    pos = _resolve_position(position, position_name)
    ax = plot_payoff(pos, title=title)
    name = f"optionslab_payoff_{(pos.name or 'position').replace(' ', '_')}.png"
    return _deliver(ax.figure, name, save_path)


@mcp.tool()
def chart_primitives(
    strike: float = 100.0,
    premium: float = 5.0,
    save_path: Optional[str] = None,
) -> list:
    """2x2 grid of the four payoff primitives."""
    fig = plot_primitives(strike, premium)
    return _deliver(fig, "optionslab_primitives.png", save_path)


@mcp.tool()
def chart_verticals(
    k1: float, k2: float,
    call_p1: float, call_p2: float,
    put_p1: float, put_p2: float,
    save_path: Optional[str] = None,
) -> list:
    """2x2 grid of bull/bear call/put vertical spreads at (k1, k2)."""
    fig = plot_vertical_spreads(k1, k2, call_p1, call_p2, put_p1, put_p2)
    return _deliver(fig, f"optionslab_verticals_{int(k1)}_{int(k2)}.png", save_path)


@mcp.tool()
def chart_greek(
    strike: float,
    option_type: str,
    sigma: float,
    expiration: Optional[str] = None,
    T_years: Optional[float] = None,
    greek: str = "delta",
    r: Optional[float] = None,
    q: float = 0.0,
    overlay_oracle: bool = False,
    save_path: Optional[str] = None,
) -> list:
    """Greek vs spot for fixed K, T, r, σ. Default: delta sigmoid."""
    if expiration is None and T_years is None:
        raise ValueError("provide expiration (YYYY-MM-DD) or T_years")
    T = T_years if T_years is not None else year_fraction(expiration)
    r_used = get_risk_free_rate() if r is None else r
    ax = plot_greek_vs_spot(strike, T, r_used, sigma, option_type, q,
                            greek=greek, overlay_oracle=overlay_oracle)
    name = f"optionslab_{greek}_{option_type}_K{int(strike)}.png"
    return _deliver(ax.figure, name, save_path)


@mcp.tool()
def chart_extrinsic(
    ticker: str,
    expiration: Optional[str] = None,
    exp_index: Optional[int] = None,
    save_path: Optional[str] = None,
) -> list:
    """Extrinsic-value-by-strike chart for one expiration."""
    ch = load_chain(ticker, expiration=expiration, exp_index=exp_index,
                    greeks=False)
    calls = filter_liquid(ch.calls)
    puts = filter_liquid(ch.puts)
    if calls.empty or puts.empty:
        raise OptionsLabError("not enough liquid strikes to plot")
    ax = plot_extrinsic(calls, puts, ch.spot, ch.symbol, ch.expiration)
    return _deliver(ax.figure, f"optionslab_extrinsic_{ch.symbol}.png", save_path)


@mcp.tool()
def chart_scenario(
    spot: float,
    sigma: Optional[float] = None,
    r: Optional[float] = None,
    q: float = 0.0,
    position: Optional[object] = None,
    position_name: Optional[str] = None,
    spot_pcts: Optional[list[float]] = None,
    days_forward: Optional[list[float]] = None,
    save_path: Optional[str] = None,
) -> list:
    """P&L heatmap: rows = spot moves, cols = days forward."""
    pos = _resolve_position(position, position_name)
    res = scenario_grid(pos, _market(spot, r, q),
                        spot_pcts=spot_pcts, days_forward=days_forward,
                        ivs=sigma)
    ax = plot_pnl_grid(res)
    name = f"optionslab_scenario_{(pos.name or 'position').replace(' ', '_')}.png"
    return _deliver(ax.figure, name, save_path)


# ---------- Week 3 volatility tools ----------

@mcp.tool()
def realized_vol_extended(
    ticker: str,
    window_days: int = 21,
    estimator: str = "close_to_close",
    all_estimators: bool = False,
) -> dict:
    """Annualized realized vol (%). `estimator` ∈ {close_to_close, parkinson,
    garman_klass, rogers_satchell, yang_zhang}. `all_estimators=True`
    reports all five side-by-side for today.
    """
    if all_estimators:
        df = _rv_all(ticker, window_days)
        return {"ticker": ticker.upper(), "window_days": window_days,
                "latest_pct_by_estimator":
                    {k: round(float(v), 3) for k, v in df.iloc[-1].items()}}
    return {"ticker": ticker.upper(), "estimator": estimator,
            "window_days": window_days,
            "realized_vol_pct": _rv(ticker, window_days, estimator=estimator)}


@mcp.tool()
def vrp_today() -> dict:
    """Today's VRP (VIX − trailing-21d SPX RV) with 2-year percentile."""
    return _vrp_today().to_dict()


@mcp.tool()
def vrp_history(years: int = 15) -> dict:
    """VRP summary stats over `years` of history (without the dense series)."""
    return _vrp_history(years=years).to_dict()


@mcp.tool()
def vix_term_structure() -> dict:
    """Today's VIX-family curve, VIX3M/VIX ratio, regime label, percentile."""
    return _term_structure().to_dict()


@mcp.tool()
def skew_metrics(
    ticker: str,
    expiration: Optional[str] = None,
    exp_index: Optional[int] = None,
    save_history: bool = False,
) -> dict:
    """25Δ Risk Reversal + 25Δ Butterfly + ATM IV for one expiration."""
    return _skew_metrics(ticker, expiration=expiration, exp_index=exp_index,
                         save_history=save_history).to_dict()


@mcp.tool()
def vix_strip(ticker: str = "SPX", target_days: int = 30,
              wing_boost_pct: float = 20.0) -> dict:
    """Model-free VIX replication from a 1/K² OTM-strip + wing sensitivity."""
    return _vix_strip(symbol=ticker, target_days=target_days,
                      wing_boost_pct=wing_boost_pct).to_dict()


@mcp.tool()
def event_implied_move(ticker: str, front_expiration: str,
                       back_expiration: str) -> dict:
    """Forward-variance event-implied 1-day move from two expirations."""
    return _event_implied_move(ticker, front_expiration,
                               back_expiration).to_dict()


@mcp.tool()
def vol_dashboard(
    skew_symbol: str = "SPY",
    skew_exp_index: int = 4,
    save_history: bool = True,
    yesterdays_call: Optional[str] = None,
    vol_view: Optional[str] = None,
) -> dict:
    """The daily Vol Dashboard — five computed fields + two human passthrough.

    `save_history=True` appends the 25Δ RR / BF snapshots to project-local
    CSV history (the only way to build percentiles for the skew block).
    """
    return _vol_dashboard(
        skew_symbol=skew_symbol, skew_exp_index=skew_exp_index,
        save_history=save_history,
        yesterdays_call=yesterdays_call, vol_view=vol_view,
    ).to_dict()


# ---------- Week 3 charts ----------

@mcp.tool()
def chart_rv(
    ticker: str,
    window_days: int = 21,
    lookback_days: int = 1260,
    save_path: Optional[str] = None,
) -> list:
    """RV-estimator-comparison chart — all five estimators on one panel."""
    from ..data.vol import realized_vol_all_estimators
    from ..plotting.vol import plot_rv_estimators
    df = realized_vol_all_estimators(ticker, window_days, lookback_days=lookback_days)
    ax = plot_rv_estimators(df, title=f"RV estimators — {ticker.upper()}")
    return _deliver(ax.figure, f"optionslab_rv_{ticker.upper()}.png", save_path)


@mcp.tool()
def chart_smile(
    ticker: str,
    expiration: Optional[str] = None,
    exp_index: Optional[int] = None,
    save_path: Optional[str] = None,
) -> list:
    """IV smile, 3-panel: vs strike / log-moneyness / delta."""
    from ..plotting.vol import plot_iv_smile
    ch = load_chain(ticker, expiration=expiration, exp_index=exp_index)
    fig = plot_iv_smile(ch)
    return _deliver(fig, f"optionslab_smile_{ch.symbol}_{ch.expiration}.png",
                    save_path)


@mcp.tool()
def chart_vrp(years: int = 15, save_path: Optional[str] = None) -> list:
    """VRP time series + histogram with marquee dates."""
    from ..plotting.vol import plot_vrp
    res = _vrp_history(years=years)
    fig = plot_vrp(res.series, marks={
        "Volmageddon": "2018-02-05",
        "COVID-19":    "2020-03-16",
        "Aug 5 2024":  "2024-08-05",
    })
    return _deliver(fig, "optionslab_vrp.png", save_path)


@mcp.tool()
def chart_term(save_path: Optional[str] = None) -> list:
    """VIX term-structure history + VIX3M/VIX ratio history."""
    from ..analysis.vol.term import term_structure_history
    from ..plotting.vol import plot_term_structure
    hist = term_structure_history(lookback_days=1260)
    fig = plot_term_structure(hist)
    return _deliver(fig, "optionslab_term.png", save_path)


@mcp.tool()
def chart_skew_curve(
    ticker: str,
    expiration: Optional[str] = None,
    exp_index: Optional[int] = None,
    save_path: Optional[str] = None,
) -> list:
    """IV(K) snapshot with 25Δ markers."""
    from ..plotting.vol import plot_skew_curve
    ch = load_chain(ticker, expiration=expiration, exp_index=exp_index)
    fig = plot_skew_curve(ch)
    return _deliver(fig, f"optionslab_skew_{ch.symbol}_{ch.expiration}.png",
                    save_path)


@mcp.tool()
def chart_vix_strip(
    ticker: str = "SPX",
    target_days: int = 30,
    save_path: Optional[str] = None,
) -> list:
    """Replicated VIX vs published + wing-boost sensitivity bar chart."""
    from ..plotting.vol import plot_vix_strip_overlay
    res = _vix_strip(symbol=ticker, target_days=target_days,
                     wing_boost_pct=20.0)
    fig = plot_vix_strip_overlay(res)
    return _deliver(fig, f"optionslab_vix_strip_{ticker.upper()}.png",
                    save_path)


@mcp.tool()
def chart_dashboard(
    skew_symbol: str = "SPY",
    skew_exp_index: int = 4,
    save_path: Optional[str] = None,
) -> list:
    """Compact panel of all 5 dashboard fields with percentile bars."""
    from ..plotting.vol import plot_dashboard_panel
    res = _vol_dashboard(skew_symbol=skew_symbol,
                         skew_exp_index=skew_exp_index,
                         save_history=True)
    fig = plot_dashboard_panel(res)
    return _deliver(fig, "optionslab_dashboard.png", save_path)


@mcp.tool()
def parity_check(
    ticker: str,
    strike: float,
    expiration: str,
    r: Optional[float] = None,
    q: Optional[float] = None,
) -> dict:
    """W1.D5 — Put-call parity from a live chain at one strike.

    Returns the LHS (C_mid − P_mid), the RHS (S·e^(−qT) − K·e^(−rT)),
    the gap, and whether it sits inside the combined bid-ask spread
    (benign vs actionable).
    """
    return _parity_check(ticker, strike, expiration, r=r, q=q).to_dict()


@mcp.tool()
def verify_synthetic_stock(
    direction: str,
    strike: float,
    call_premium: float,
    put_premium: float,
    s_min: Optional[float] = None,
    s_max: Optional[float] = None,
    points: int = 21,
) -> dict:
    """W1.D6 — Build a synthetic stock (long or short) and verify against
    the linear stock payoff across a range of S_T.

    `direction` ∈ {"long", "short"}. Reports parallel offset (the
    no-arbitrage cost) and max residual after removing the offset
    (should be ~0).
    """
    if direction == "long":
        synth = synthetic_long_stock(strike, call_premium, put_premium)
        target = long_stock_payoff(strike)
    elif direction == "short":
        synth = synthetic_short_stock(strike, call_premium, put_premium)
        target = short_stock_payoff(strike)
    else:
        raise ValueError("direction must be 'long' or 'short'")
    lo = s_min if s_min is not None else 0.5 * strike
    hi = s_max if s_max is not None else 1.5 * strike
    return _verify_synthetic(target, synth, s_range=(lo, hi),
                             points=points).to_dict()


@mcp.tool()
def chart_greek_time(
    strike: float,
    spot: float,
    option_type: str,
    sigma: float,
    greek: str = "gamma",
    r: Optional[float] = None,
    q: float = 0.0,
    t_min_days: float = 1.0,
    t_max_days: float = 365.0,
    save_path: Optional[str] = None,
) -> list:
    """W2.D3/D4/D5 — A Greek as a function of days-to-expiry.

    Default is gamma (peak grows and narrows as T→0); use greek="theta"
    for the asymptote-near-expiry shape and "vega" for the √T scaling.
    """
    from ..plotting.greek import plot_greek_vs_time
    r_used = _get_risk_free_rate() if r is None else r
    ax = plot_greek_vs_time(
        strike, spot, r_used, sigma, option_type, q,
        greek=greek, t_range_days=(t_min_days, t_max_days),
    )
    name = f"optionslab_{greek}_vs_time_K{int(strike)}.png"
    return _deliver(ax.figure, name, save_path)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
