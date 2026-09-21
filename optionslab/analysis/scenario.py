"""Scenario engine: theoretical option price + position P&L grid.

Two entry points:

  theoretical_price(...)       — value + Greeks for ONE leg at a
                                 hypothetical (spot, time, iv).
  scenario_grid(position, ...) — dollar P&L matrix of a Position over
                                 a sweep of spot moves × days forward.

Pure compositions over `pricing` and `valuation`.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..core.market import MarketContext
from ..core.position import Position
from ..core.results import ScenarioResult
from ..pricing import bs_greeks, bs_price, year_fraction
from ._ivs import IvSpec, resolve_ivs
from .valuation import value as _value

DEFAULT_SPOT_PCTS = [-30, -20, -10, 0, 10, 20, 30, 40]
DEFAULT_DAYS_FORWARD = [0, 7, 14, 30, 60, 90]


def theoretical_price(
    symbol: str,
    strike: float,
    expiration: str,
    option_type: str,
    *,
    s_now: float | None = None,
    s_future: float | None = None,
    days_forward: float = 0.0,
    iv: float | None = None,
    r: float | None = None,
    q: float | None = None,
) -> dict:
    """Theoretical option value + Greeks at a hypothetical spot/time.

    Defaults are live: spot from quote, IV from the chain at that strike,
    r from the 13-week T-bill, q from trailing dividend yield. Override
    any of them to model a scenario.

    Returns a plain dict (this isn't a Position-level result, just a
    single-leg readout).
    """
    from ..data.chain import iv_at_strike, load_chain
    from ..data.quotes import get_dividend_yield, get_risk_free_rate, get_spot, make_ticker

    ticker = make_ticker(symbol)
    spot_now = s_now if s_now is not None else get_spot(ticker)
    S_eval = s_future if s_future is not None else spot_now
    r_used = get_risk_free_rate() if r is None else r
    q_used = get_dividend_yield(ticker) if q is None else q

    if iv is None:
        ch = load_chain(symbol, expiration=expiration, greeks=False)
        df = ch.calls if option_type == "call" else ch.puts
        iv = iv_at_strike(df, strike)
        if iv is None:
            raise ValueError(
                f"No usable IV at {symbol} {strike} {option_type}; pass iv="
            )

    T = max(year_fraction(expiration) - days_forward / 365.0, 0.0)
    price = float(bs_price(S_eval, strike, T, r_used, iv, option_type, q_used))
    g = bs_greeks(S_eval, strike, T, r_used, iv, option_type, q_used)
    return {
        "price": round(price, 4),
        "delta": round(g["delta"], 4),
        "gamma": round(g["gamma"], 5),
        "theta": round(g["theta"], 4),
        "vega": round(g["vega"], 4),
        "rho": round(g["rho"], 4),
        "inputs": {
            "spot_now": round(spot_now, 4),
            "spot_evaluated": round(float(S_eval), 4),
            "days_forward": days_forward,
            "T_years": round(T, 5),
            "iv": round(iv, 4),
            "r": round(r_used, 4),
            "q": round(q_used, 4),
        },
        "conventions": g["conventions"],
    }


def scenario_grid(
    position: Position,
    market: MarketContext,
    *,
    spot_pcts: Sequence[float] | None = None,
    days_forward: Sequence[float] | None = None,
    ivs: IvSpec = None,
) -> ScenarioResult:
    """Dollar P&L matrix over spot moves × days forward.

    Rows = `spot_pcts` (% move from `market.spot`).
    Cols = `days_forward` (calendar days advancing `market.asof`).
    Each cell = total mark-to-model P&L vs entry (dollars).
    """
    if market.spot is None:
        raise ValueError("market.spot is required for scenario_grid")
    pcts = list(spot_pcts) if spot_pcts is not None else list(DEFAULT_SPOT_PCTS)
    days = list(days_forward) if days_forward is not None else list(DEFAULT_DAYS_FORWARD)
    ivs_used = resolve_ivs(position, ivs)

    grid: list[list[float]] = []
    for pct in pcts:
        S = market.spot * (1.0 + pct / 100.0)
        row: list[float] = []
        for d in days:
            scen = market.at_spot(S).forward(d)
            res = _value(position, scen, ivs=ivs_used)
            row.append(round(res.total_pnl_vs_entry_dollars, 2))
        grid.append(row)

    return ScenarioResult(
        spot=float(market.spot),
        spot_pcts=pcts,
        spot_prices=[round(market.spot * (1 + p / 100.0), 4) for p in pcts],
        days_forward=days,
        pnl_dollars=grid,
        r=round(market.r, 4),
        q=round(market.q, 4),
        ivs_used=[round(v, 4) for v in ivs_used],
    )
