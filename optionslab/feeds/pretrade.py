"""The pre-trade page: every sourced input for one name and expiry, in one call.

    pretrade("SU", "2027-04-16", order={...})

Sections, in the order a trade is checked:

  spot         feed vs CBOE vs parity-implied spot, FEED_SUSPECT flag
  chain        usability verdict and OI walls for the requested expiry
               (neighbouring listed expiries when it is not listed), and the
               expiry's ATM IV against the realized-vol matrix
  order        the proposed option's price, liquidity, IV vs RV, hurdle
               (breakeven vs implied move) and risk-neutral odds
  realized_vol the fifteen-cell RV matrix with the worst cells named
  crack        the configured crack spread and its 5/21-session change
  physical     the configured EIA series against their five-year bands
  positioning  COT managed money for the configured roots, with print dates
  sweep        the capital-structure sweep, every line verified or not
  calendar     sourced events from today (asof) to expiry
  markets      prediction-market reads with their resolution text
  book         delta-notional per name, before and after the proposed order

Each section is the feed's own envelope, so every number keeps its
provenance. `flags` lists every red flag found on the page (feed suspect,
thin chain, gap regime, median outcome a total loss, cap breach, unverified
sweep lines, earnings inside the window ...), and `unsourced` collects every
not_verified line; nothing on the page is a number the tool made up.

Which roots, series, and crack describe a name's physical context is the
user's choice, in config["pretrade_context"]; it is not inferred.
"""

from __future__ import annotations

from datetime import date

from ..storage import snapshots as snap
from . import (
    bars,
    book,
    calendar,
    chains,
    config,
    cot,
    edgar,
    eia,
    etf_roll,
    futures,
    polymarket,
)
from .envelope import envelope, provenance, unsourced, unverified
from .order_check import check_order

SECTIONS = ("spot", "chain", "order", "realized_vol", "crack", "physical", "positioning",
            "sweep", "calendar", "markets", "book")
MAX_QTY_PCT_OF_OI = 10.0     # flag an order above this share of the strike's open interest


def _chain_sections(symbol: str, expiration: str) -> tuple[dict, dict]:
    """(spot section, chain section). Falls back to listed neighbours for spot."""
    report = chains.chain_report(symbol, expiration, include_rows=False)
    if report["data"] is not None:
        d = report["data"]
        spot = envelope({"expiration_used": expiration, **d["spot_check"],
                         "parity": d["parity"]},
                        prov=report["provenance"], warnings=report["warnings"],
                        not_verified=report["not_verified"])
        chain = envelope({"expiration": expiration, "usability": d["usability"],
                          "walls": d["walls"], "atm_iv_pct": d["atm_iv_pct"]},
                         prov=report["provenance"])
        return spot, chain

    missing = report["not_verified"][0] if report["not_verified"] else {}
    neighbours = {k: missing.get(k) for k in ("listed_before", "listed_after") if missing.get(k)}
    if not neighbours:
        return report, report
    summaries = {}
    for label, exp in neighbours.items():
        rep = chains.chain_report(symbol, exp, include_rows=False)
        if rep["data"] is not None:
            d = rep["data"]
            summaries[label] = {"expiration": exp, "usability": d["usability"],
                                "walls": d["walls"], "spot_check": d["spot_check"],
                                "provenance": rep["provenance"]}
    nearest = summaries.get("listed_after") or summaries.get("listed_before")
    spot = (envelope({"expiration_used": nearest["expiration"], **nearest["spot_check"]},
                     prov=nearest["provenance"],
                     warnings=[f"{expiration} is not listed; spot checked on "
                               f"{nearest['expiration']}"])
            if nearest else report)
    chain = envelope({"expiration": expiration, "listed": False, "neighbours": summaries},
                     prov=report["provenance"], not_verified=report["not_verified"])
    return spot, chain


def _many(item: str, results: list[dict]) -> dict:
    """Combine several envelopes of one kind into a section."""
    if not results:
        return unsourced(item, "nothing configured in pretrade_context", source="config")
    unverified_lines = [line for r in results for line in r["not_verified"]]
    warnings = [w for r in results for w in r["warnings"]]
    data = [{"status": r["status"], "provenance": r["provenance"], "data": r["data"]}
            for r in results]
    prov = provenance(source=item, quality="snapshot" if snap.is_offline() else "live")
    return envelope(data, prov=prov, warnings=warnings, not_verified=unverified_lines)


def _book_section(order: dict | None) -> dict:
    if order is not None:
        return book.delta_notional_after(order)
    marked = book.mark_positions()
    no_order = unverified("proposed order",
                          "no order given; pass `order` to see delta-notional after it")
    return envelope(marked["data"], prov=marked["provenance"], warnings=marked["warnings"],
                    not_verified=[*marked["not_verified"], no_order])


def _order_section(order: dict | None) -> dict:
    if order is None:
        return unsourced("proposed order", "no order given; pass `order` for price, liquidity, "
                      "hurdle and odds", source="pretrade")
    return check_order(order)


def _flag(section: str, flag: str, detail: str) -> dict:
    return {"section": section, "flag": flag, "detail": detail}


def _spot_flags(sec: dict) -> list[dict]:
    d = sec["data"]
    return [_flag("spot", d["flag"], ", ".join(d["breaches"]))] if d and d.get("flag") else []


def _chain_flags(sec: dict) -> list[dict]:
    d = sec["data"]
    if not d:
        return []
    if d.get("listed") is False:
        return [_flag("chain", "EXPIRY_NOT_LISTED", d["expiration"])]
    u = d["usability"]
    return [] if u["verdict"] == "usable" else [
        _flag("chain", f"CHAIN_{u['verdict'].upper()}", u["reason"])]


def _order_flags(sec: dict) -> list[dict]:
    d = sec["data"]
    if not d or not d.get("odds"):
        return []
    odds, liq, flags = d["odds"], d["liquidity"], []
    if odds["median_pnl_per_contract"] <= -(d["price"]["fill_assumed"] or 0) * 100:
        flags.append(_flag("order", "MEDIAN_TOTAL_LOSS",
                           f"risk-neutral P(total loss) {odds.get('p_total_loss')}"))
    if liq["qty_pct_of_oi"] is not None and liq["qty_pct_of_oi"] > MAX_QTY_PCT_OF_OI:
        flags.append(_flag("order", "SIZE_VS_OI", f"{liq['qty_pct_of_oi']}% of strike OI"))
    return flags


def _rv_flags(sec: dict) -> list[dict]:
    d = sec["data"]
    if not d or not d["gap_signature"]["flag"]:
        return []
    gaps = d["gap_signature"]["c2c_minus_parkinson_pts"]
    detail = " / ".join(f"{w}d {v:+.1f}" for w, v in gaps.items() if v is not None)
    return [_flag("realized_vol", d["gap_signature"]["flag"],
                  f"close-to-close minus Parkinson {detail} vol pts")]


def _positioning_flags(sec: dict) -> list[dict]:
    return [_flag("positioning", "COT_STALE", item["data"]["root"])
            for item in sec["data"] or [] if item["data"] and item["data"]["stale"]]


def _sweep_flags(sec: dict) -> list[dict]:
    if sec["data"] is None:
        return [_flag("sweep", "SWEEP_INCOMPLETE", "sweep unavailable")]
    open_items = [ln["item"] for ln in sec["data"]["lines"] if ln["status"] != "verified"]
    return [_flag("sweep", "SWEEP_INCOMPLETE", ", ".join(open_items))] if open_items else []


def _calendar_flags(sec: dict) -> list[dict]:
    return [_flag("calendar", "EARNINGS_IN_WINDOW",
                  f"{ev['date']}{'' if ev['verified'] else ' (unverified date)'}")
            for ev in (sec["data"] or {}).get("events", []) if "earnings" in ev["event"].lower()]


def _book_flags(sec: dict) -> list[dict]:
    d = sec["data"]
    return [_flag("book", "DELTA_NOTIONAL_BREACH",
                  f"{d['pct_of_book_after']}% of book > cap {d['cap_pct']}%")
            ] if d and d.get("breach") else []


FLAG_CHECKS = {"spot": _spot_flags, "chain": _chain_flags, "order": _order_flags,
               "realized_vol": _rv_flags, "positioning": _positioning_flags,
               "sweep": _sweep_flags, "calendar": _calendar_flags, "book": _book_flags}


def red_flags(sections: dict) -> list[dict]:
    """Every red flag on the page, one line each, in section order."""
    return [f for name, check in FLAG_CHECKS.items() for f in check(sections[name])]


def pretrade(symbol: str, expiration: str, order: dict | None = None) -> dict:
    """The one-call pre-trade page. See the module docstring for sections."""
    symbol = symbol.upper()
    ctx = config.get("pretrade_context")
    today = snap.asof_date()
    spot, chain = _chain_sections(symbol, expiration)
    extra_events = [*futures.contract_events(ctx["curve_roots"]),
                    *etf_roll.roll_events(ctx["roll_funds"])]
    sections = {
        "spot": spot,
        "chain": chain,
        "order": _order_section(order),
        "realized_vol": bars.rv_matrix(symbol),
        "crack": futures.cracks(),
        "physical": _many("EIA weekly", [eia.weekly_series(s["series"], s["region"])
                                         for s in ctx["eia_series"]]),
        "positioning": _many("COT managed money", [cot.managed_money(r)
                                                   for r in ctx["cot_roots"]]),
        "sweep": edgar.structural_sweep(symbol),
        "calendar": calendar.events_between(today, date.fromisoformat(expiration),
                                            tickers=[symbol], extra_events=extra_events),
        "markets": polymarket.watchlist_markets(),
        "book": _book_section(order),
    }
    unsourced = [{"section": name, **line}
                 for name, sec in sections.items() for line in sec["not_verified"]]
    page = {"symbol": symbol, "expiration": expiration, "asof_date": today.isoformat(),
            "offline": snap.is_offline(), "flags": red_flags(sections),
            "sections": sections, "unsourced": unsourced}
    return envelope(page, prov=provenance(source="optionslab pretrade",
                                          quality="snapshot" if snap.is_offline() else "live"),
                    not_verified=unsourced)


def chart_inputs(symbol: str, expiration: str, order: dict | None = None) -> dict:
    """The envelopes the one-page chart sheet draws.

    {page, chain, rv, order, cracks, cot, eia, curve}.

    The chain panel uses the expiry the spot check used (a listed neighbour
    when `expiration` is not listed); COT, EIA, and the curve use the first
    configured root / series in pretrade_context.
    """
    page = pretrade(symbol, expiration, order)
    spot = page["data"]["sections"]["spot"]["data"] or {}
    chain_expiry = spot.get("expiration_used", expiration)
    ctx = config.get("pretrade_context")
    return {
        "page": page,
        "chain": chains.chain_report(symbol, chain_expiry, include_rows=True),
        "rv": page["data"]["sections"]["realized_vol"],
        "order": page["data"]["sections"]["order"],
        "cracks": futures.crack_history(),
        "cot": cot.net_history(ctx["cot_roots"][0]),
        "eia": eia.seasonal_history(**ctx["eia_series"][0]),
        "curve": futures.curve_overlay(ctx["curve_roots"][0]),
    }
