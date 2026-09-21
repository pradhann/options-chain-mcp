"""The economics of one proposed order, from the chain snapshot it would trade on.

For an option order this answers, in order:

  price      bid / ask / mid, and the fill assumed (ask to buy, bid to sell)
  liquidity  quantity as a percent of the strike's open interest, the
             spread as a percent of mid, and the dollar cost of crossing
             half the spread now and the full spread round trip
  vol        the strike's mid-IV against every realized-vol cell, and the
             premium over the cell least favourable to a long
  hurdle     breakeven at expiry, the move from spot it needs, and the
             market's implied move (ATM straddle / spot) to compare
  odds       RISK-NEUTRAL odds from the strike's own mid-IV: probability
             of finishing in the money, of any profit at expiry, of total
             loss, and the P&L at the median terminal price. Risk-neutral
             odds are what the price implies, not a forecast; they are
             reported in the order median, P(total loss), then the rest.

Stock orders get the price and liquidity lines only (no chain needed).
Every figure is derived from the snapshot named in provenance.
"""

from __future__ import annotations

import math

from scipy.stats import norm

from . import bars
from .chains import atm_row_pair, chain_report
from .envelope import envelope, unsourced, unverified
from .fundamentals import spot_quote

CONTRACT = 100


def _rn_odds(spot: float, strike: float, T: float, r: float, q: float, sigma: float,
             side: str, kind: str, fill: float) -> dict:
    """Risk-neutral terminal odds for one option leg at a given fill price."""
    fwd = spot * math.exp((r - q) * T)
    sd = sigma * math.sqrt(T)

    def prob_above(level: float) -> float:
        return float(norm.cdf((math.log(fwd / level) - 0.5 * sd * sd) / sd))

    breakeven = strike + fill if kind == "call" else strike - fill
    p_itm = prob_above(strike) if kind == "call" else 1 - prob_above(strike)
    p_beyond_be = prob_above(breakeven) if kind == "call" else (
        1 - prob_above(breakeven) if breakeven > 0 else 0.0)
    median_price = fwd * math.exp(-0.5 * sd * sd)
    intrinsic = max(median_price - strike, 0) if kind == "call" else max(strike - median_price, 0)
    sign = 1 if side == "long" else -1
    median_pnl = sign * (intrinsic - fill) * CONTRACT
    out = {
        "median_terminal_price": round(median_price, 4),
        "median_pnl_per_contract": round(median_pnl, 2),
        "p_itm": round(p_itm, 4),
        "breakeven": round(breakeven, 4),
        "move_to_breakeven_pct": round((breakeven / spot - 1) * 100, 2),
    }
    if side == "long":
        out["p_total_loss"] = round(1 - p_itm, 4)
        out["p_profit"] = round(p_beyond_be, 4)
    else:
        out["p_assigned"] = round(p_itm, 4)
        out["p_profit"] = round(1 - p_beyond_be, 4)
    return out


def _vol_line(symbol: str, iv_pct: float | None) -> tuple[dict | None, list[dict]]:
    rv = bars.rv_matrix(symbol)
    if rv["data"] is None or iv_pct is None:
        return None, rv["not_verified"] or [unverified("IV vs RV", "no IV at this strike")]
    d = rv["data"]
    cells = {f"{est}_{w}": round(iv_pct - v, 2)
             for est, row in d["matrix"].items() for w, v in row.items() if v is not None}
    worst = d["worst_for_long"]
    return {"iv_mid_pct": iv_pct, "iv_minus_rv_pts": cells,
            "premium_over_worst_for_long_pts": round(iv_pct - worst["rv_pct"], 2),
            "worst_for_long": worst, "gap_flag": d["gap_signature"]["flag"]}, []


def _stock_check(order: dict) -> dict:
    q = spot_quote(order["symbol"])
    if q["data"] is None:
        return q
    data = {"order": order, "price": {"spot": q["data"]["spot"]},
            "notional": round(q["data"]["spot"] * float(order["qty"]), 2)}
    return envelope(data, prov=q["provenance"], warnings=q["warnings"])


def check_order(order: dict) -> dict:
    """Price, liquidity, vol, hurdle and risk-neutral odds for `order`.

    `order`: {symbol, instrument: stock|call|put, side: long|short, qty,
    strike?, expiration?}.
    """
    if order["instrument"] == "stock":
        return _stock_check(order)
    symbol, kind, side = order["symbol"].upper(), order["instrument"], order["side"]
    strike, qty = float(order["strike"]), float(order["qty"])
    rep = chain_report(symbol, order["expiration"])
    if rep["data"] is None:
        return rep
    d = rep["data"]
    row = next((r for r in d["rows"] if r["type"] == kind and r["strike"] == strike), None)
    if row is None:
        return unsourced(f"{symbol} {order['expiration']} {strike:g} {kind}",
                      "strike not in the chain snapshot", rep["provenance"]["source_url"],
                      source=rep["provenance"]["source"])
    fill = row["ask"] if side == "long" else row["bid"]
    not_verified = list(rep["not_verified"])
    spot = d["spot_check"]["feed_spot"]

    liquidity = {
        "strike_oi": row["oi"],
        "qty_pct_of_oi": round(qty / row["oi"] * 100, 2) if row["oi"] else None,
        "spread_pct": row["spread_pct"],
        "half_spread_cost": (round((row["ask"] - row["mid"]) * qty * CONTRACT, 2)
                             if row["mid"] is not None else None),
        "round_trip_spread_cost": (round((row["ask"] - row["bid"]) * qty * CONTRACT, 2)
                                   if row["mid"] is not None else None),
    }
    vol, vol_nv = _vol_line(symbol, row["iv_mid_pct"])
    not_verified += vol_nv

    pair = atm_row_pair(d["rows"], spot)
    implied_move = (round((pair[0]["mid"] + pair[1]["mid"]) / spot * 100, 2)
                    if pair and pair[0]["mid"] and pair[1]["mid"] else None)
    if fill is None or row["iv_mid_pct"] is None or d["r"] is None:
        odds = None
        not_verified.append(unverified("risk-neutral odds", "no two-sided quote / IV / rate at this "
                               "strike", rep["provenance"]["source_url"]))
    else:
        odds = _rn_odds(spot, strike, d["T_years"], d["r"], d["q"], row["iv_mid_pct"] / 100,
                        side, kind, fill)
    data = {
        "order": order,
        "price": {"bid": row["bid"], "ask": row["ask"], "mid": row["mid"], "fill_assumed": fill,
                  "premium": round(fill * qty * CONTRACT, 2) if fill else None,
                  "spot": spot, "spot_flag": d["spot_check"]["flag"]},
        "liquidity": liquidity,
        "vol": vol,
        "hurdle": {"implied_move_pct": implied_move,
                   "implied_move_basis": "ATM straddle mid / spot, to expiry",
                   "move_to_breakeven_pct": odds["move_to_breakeven_pct"] if odds else None},
        "odds": odds,
        "inputs": {"T_years": d["T_years"], "r": d["r"], "q": d["q"],
                   "iv_mid_pct": row["iv_mid_pct"], "expiration": order["expiration"]},
        "odds_basis": "risk-neutral, lognormal at the strike's own mid-IV; the price's "
                      "implied odds, not a forecast",
    }
    prov = {**rep["provenance"], "source": f"order check on {rep['provenance']['source']}"}
    return envelope(data, prov=prov, warnings=rep["warnings"], not_verified=not_verified)
