"""Item 13: the book, marked from chain snapshots, sized in delta-notional.

Each open ledger position is marked:
  stock   mark = feed spot, delta = 1 per share
  option  mark = chain mid (two-sided quotes only), delta = Black-Scholes
          delta at the strike's own mid-IV, from that day's chain snapshot

  delta_notional = delta x quantity x multiplier x spot x (+1 long, -1 short)

Per name, options and stock are summed and expressed as a percent of
config["book_value"]; names above config["delta_notional_cap_pct"] are
breaches. A position that cannot be marked is listed as not_verified and
excluded from the totals; the totals say how many were excluded.
"""

from __future__ import annotations

from collections import defaultdict
from functools import lru_cache

from ..pricing import bs_greeks
from ..storage import ledger
from ..storage import snapshots as snap
from ..storage.ledger import LedgerRow
from . import config
from .chains import chain_report
from .envelope import envelope, provenance, unverified
from .fundamentals import spot_quote

MULTIPLIER = {"stock": 1, "call": 100, "put": 100}
SOURCE = "ledger.csv marked from chain snapshots / yfinance quote"


@lru_cache(maxsize=64)
def _report(symbol: str, expiration: str) -> dict:
    return chain_report(symbol, expiration)


def _mark_stock(symbol: str) -> dict:
    q = spot_quote(symbol)
    if q["data"] is None:
        return unverified(f"{symbol} stock mark", "no spot", q["provenance"].get("source_url"))
    spot = q["data"]["spot"]
    return {"mark": spot, "delta": 1.0, "spot": spot, "asof": q["provenance"]["asof"],
            "flags": []}


def _mark_option(symbol: str, instrument: str, strike: float, expiration: str) -> dict:
    rep = _report(symbol, expiration)
    item = f"{symbol} {expiration} {strike:g} {instrument} mark"
    if rep["data"] is None:
        reason = rep["not_verified"][0]["reason"] if rep["not_verified"] else "no chain"
        return unverified(item, reason, rep["provenance"].get("source_url"))
    d = rep["data"]
    match = [r for r in d["rows"] if r["type"] == instrument and r["strike"] == strike]
    if not match:
        return unverified(item, "strike not in the chain snapshot", rep["provenance"]["source_url"])
    row = match[0]
    if row["mid"] is None or row["iv_mid_pct"] is None:
        return unverified(item, "no two-sided quote / IV at this strike", rep["provenance"]["source_url"])
    spot = d["spot_check"]["feed_spot"]
    delta = float(bs_greeks(spot, strike, d["T_years"], d["r"], row["iv_mid_pct"] / 100,
                            instrument, d["q"])["delta"])
    return {"mark": row["mid"], "delta": round(delta, 4), "spot": spot,
            "asof": rep["provenance"]["asof"],
            "flags": [d["spot_check"]["flag"]] if d["spot_check"]["flag"] else []}


def mark_exposure(symbol: str, instrument: str, side: str, qty: float,
                  strike: float | None = None, expiration: str | None = None) -> dict:
    """Mark and delta-notional for one exposure (a position or a proposed order)."""
    symbol = symbol.upper()
    m = (_mark_stock(symbol) if instrument == "stock"
         else _mark_option(symbol, instrument, float(strike), expiration))
    if m.get("status") == "not_verified":
        return m
    sign = 1 if side == "long" else -1
    delta_shares = m["delta"] * qty * MULTIPLIER[instrument] * sign
    return {**m, "delta_shares": round(delta_shares, 2),
            "delta_notional": round(delta_shares * m["spot"], 2)}


def _mark_row(row: LedgerRow) -> dict:
    m = mark_exposure(row.symbol, row.instrument, row.side, row.executed_qty,
                      row.strike, row.expiration)
    if m.get("status") == "not_verified":
        return {**m, "id": row.id}
    sign = 1 if row.side == "long" else -1
    pnl = (m["mark"] - row.entry_price) * row.executed_qty * MULTIPLIER[row.instrument] * sign
    return {"id": row.id, "symbol": row.symbol, "instrument": row.instrument,
            "side": row.side, "strike": row.strike, "expiration": row.expiration,
            "thesis_id": row.thesis_id, "qty": row.executed_qty,
            "entry_price": row.entry_price, **m, "pnl": round(pnl, 2)}


def _per_name(marks: list[dict], book_value: float | None, cap_pct: float) -> dict:
    by_name: dict[str, float] = defaultdict(float)
    for m in marks:
        by_name[m["symbol"]] += m["delta_notional"]
    out = {}
    for name, dn in sorted(by_name.items()):
        pct = round(abs(dn) / book_value * 100, 2) if book_value else None
        out[name] = {"delta_notional": round(dn, 2), "pct_of_book": pct,
                     "breach": pct is not None and pct > cap_pct}
    return out


def mark_positions() -> dict:
    """Every open position marked, with per-name delta-notional vs the cap."""
    _report.cache_clear()
    cfg = config.load()
    book_value, cap = cfg["book_value"], cfg["delta_notional_cap_pct"]
    marks, not_verified = [], []
    for row in ledger.current().values():
        if row.is_open:
            m = _mark_row(row)
            (not_verified if m.get("status") == "not_verified" else marks).append(m)
    unmarked = len(not_verified)
    if book_value is None:
        not_verified.append(unverified("book_value", "set book_value in .optionslab/config.json; "
                               "percent of book not computed"))
    data = {
        "positions": marks,
        "per_name": _per_name(marks, book_value, cap),
        "book_value": book_value, "cap_pct": cap,
        "total_pnl": round(sum(m["pnl"] for m in marks), 2),
        "unmarked_positions": unmarked,
    }
    prov = provenance(source=SOURCE, source_url=str(ledger.ledger_path()),
                      quality="snapshot" if snap.is_offline() else "live")
    return envelope(data, prov=prov, not_verified=not_verified)


def delta_notional_after(order: dict) -> dict:
    """Per-name delta-notional before and after a proposed order.

    `order`: {symbol, instrument, side, qty, strike?, expiration?}.
    """
    book = mark_positions()
    added = mark_exposure(order["symbol"], order["instrument"], order["side"],
                          float(order["qty"]), order.get("strike"), order.get("expiration"))
    if added.get("status") == "not_verified":
        return envelope(None, prov=book["provenance"], not_verified=[added, *book["not_verified"]])
    name = order["symbol"].upper()
    before = book["data"]["per_name"].get(name, {"delta_notional": 0.0})["delta_notional"]
    after = before + added["delta_notional"]
    bv, cap = book["data"]["book_value"], book["data"]["cap_pct"]
    pct = round(abs(after) / bv * 100, 2) if bv else None
    data = {"symbol": name, "order": order, "order_mark": added,
            "delta_notional_before": round(before, 2), "delta_notional_after": round(after, 2),
            "pct_of_book_after": pct, "cap_pct": cap,
            "breach": pct is not None and pct > cap,
            "unmarked_positions_in_book": book["data"]["unmarked_positions"]}
    return envelope(data, prov=book["provenance"], warnings=book["warnings"],
                    not_verified=book["not_verified"])


def ledger_review() -> dict:
    """Closed positions with blank reviews, and executed-above-recommended size."""
    rows = list(ledger.current().values())
    blanks = ledger.blank_reviews()
    oversize = [{"id": r.id, "symbol": r.symbol, "recommended_qty": r.recommended_qty,
                 "executed_qty": r.executed_qty} for r in rows
                if r.executed_qty > r.recommended_qty]
    data = {"positions": len(rows), "open": sum(r.is_open for r in rows),
            "closed_without_review": [r.id for r in blanks],
            "executed_above_recommended": oversize}
    return envelope(data, prov=provenance(source="ledger.csv",
                                          source_url=str(ledger.ledger_path())))
