"""Mid-flight valuation: Position value (dollars) and portfolio Greeks.

Two free functions, both `(position, market, *, ivs)`:

  value(position, market, *, ivs)  -> ValueResult
  greeks(position, market, *, ivs) -> GreeksResult

`ivs` accepts a scalar (broadcast), a list (aligned with legs), or None
(auto-pull from the chain via `analysis._ivs.resolve_ivs`).
"""

from __future__ import annotations

from ..core.market import MarketContext
from ..core.position import Position
from ..core.results import GreeksResult, ValueResult
from ..pricing import bs_greeks, bs_price, year_fraction
from ._ivs import IvSpec, resolve_ivs

CONTRACT_SIZE = 100


def _leg_T(leg, market: MarketContext) -> float:
    """Years to expiry for a leg, anchored at `market.asof`."""
    if leg.expiration is None:
        raise ValueError(
            f"leg {leg} has no expiration; required for mid-flight valuation"
        )
    return year_fraction(leg.expiration, market.asof)


def value(
    position: Position,
    market: MarketContext,
    *,
    ivs: IvSpec = None,
) -> ValueResult:
    """Mark-to-model value of a position (dollars).

    `leg_values_dollars` = sign · qty · 100 · model_price per leg.
    `leg_pnls_vs_entry_dollars` = that minus the sign-adjusted entry cost.
    """
    if market.spot is None:
        raise ValueError("market.spot is required for valuation")
    ivs_used = resolve_ivs(position, ivs)

    leg_values: list[float] = []
    leg_pnls: list[float] = []
    for lg, iv in zip(position.legs, ivs_used, strict=True):
        T = _leg_T(lg, market)
        price = float(bs_price(
            market.spot, lg.strike, T, market.r, iv, lg.option_type, market.q
        ))
        v = lg.sign * lg.qty * CONTRACT_SIZE * price
        entry = lg.sign * lg.qty * CONTRACT_SIZE * lg.premium
        leg_values.append(round(v, 4))
        leg_pnls.append(round(v - entry, 4))

    return ValueResult(
        spot=float(market.spot),
        asof=market.asof.isoformat(timespec="seconds"),
        leg_values_dollars=leg_values,
        total_value_dollars=round(sum(leg_values), 4),
        leg_pnls_vs_entry_dollars=leg_pnls,
        total_pnl_vs_entry_dollars=round(sum(leg_pnls), 4),
    )


def greeks(
    position: Position,
    market: MarketContext,
    *,
    ivs: IvSpec = None,
) -> GreeksResult:
    """Qty-weighted, side-signed portfolio Greeks.

    Aggregation:  X_portfolio = Σ sign_i · qty_i · X_i (BS leg Greek X).
    Dollar versions multiply by the contract size (100). Units are stated
    in the `conventions` field of the returned object so downstream
    consumers never have to guess.
    """
    if market.spot is None:
        raise ValueError("market.spot is required for Greeks")
    ivs_used = resolve_ivs(position, ivs)

    keys = ("delta", "gamma", "theta", "vega", "rho")
    totals = {k: 0.0 for k in keys}
    per_leg: list[dict] = []
    for i, (lg, iv) in enumerate(zip(position.legs, ivs_used, strict=True)):
        T = _leg_T(lg, market)
        g = bs_greeks(market.spot, lg.strike, T, market.r, iv,
                      lg.option_type, market.q)
        weight = lg.sign * lg.qty
        contrib = {k: weight * float(g[k]) for k in keys}
        for k in keys:
            totals[k] += contrib[k]
        per_leg.append({
            "leg": i,
            "side": lg.side,
            "option_type": lg.option_type,
            "strike": lg.strike,
            "qty": lg.qty,
            "expiration": lg.expiration,
            "T_years": round(T, 5),
            "sigma": round(iv, 4),
            **{k: round(float(g[k]), 6) for k in keys},
            **{f"contribution_{k}_dollars": round(contrib[k] * CONTRACT_SIZE, 4)
               for k in keys},
        })

    return GreeksResult(
        spot=float(market.spot),
        asof=market.asof.isoformat(timespec="seconds"),
        delta=totals["delta"], gamma=totals["gamma"], theta=totals["theta"],
        vega=totals["vega"], rho=totals["rho"],
        delta_dollars=totals["delta"] * CONTRACT_SIZE,
        gamma_dollars=totals["gamma"] * CONTRACT_SIZE,
        theta_dollars=totals["theta"] * CONTRACT_SIZE,
        vega_dollars=totals["vega"] * CONTRACT_SIZE,
        rho_dollars=totals["rho"] * CONTRACT_SIZE,
        per_leg=per_leg,
    )
