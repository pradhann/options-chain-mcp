"""CLI verbs for the sourced data feeds.

    optionslab pretrade SU 2027-04-16 [--order JSON] [--json]
    optionslab refresh-all [--tickers SU VLO]
    optionslab weekly-check
    optionslab ledger open|close|marks|review ...
    optionslab feed <name> [args]          any single feed, JSON envelope

`pretrade` prints a readable page by default: each section's headline
numbers with their as-of and source, then every not_verified line. `--json`
prints the full envelope. Set OPTIONSLAB_OFFLINE=1 to read snapshots only.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from dataclasses import asdict

from ..feeds import (
    bars,
    book,
    calendar,
    chain_history,
    chains,
    cot,
    edgar,
    eia,
    etf_roll,
    fundamentals,
    futures,
    jobs,
    polymarket,
    pretrade,
)
from ..storage import ledger


def _print_json(payload) -> None:
    print(json.dumps(payload, indent=2, default=str))


# ---------- pretrade page rendering ----------

def _stamp(section: dict) -> str:
    p = section["provenance"]
    return f"[{p.get('quality')} · {p.get('asof')} · {p.get('source')}]"


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:.4g}"
    return str(v)


def _walls(side: list[dict]) -> str:
    return ", ".join(f"{x['strike']:g}:{x['oi']} ({x['pct_of_side']}%)" for x in side)


def _spot_lines(d: dict) -> list[str]:
    return [f"  feed {_fmt(d['feed_spot'])}  cboe {_fmt(d['cboe_spot'])}  "
            f"parity {_fmt(d['parity_spot'])}  (expiry {d['expiration_used']})",
            f"  flag: {d['flag'] or 'none'}  breaches: {', '.join(d['breaches']) or '—'}"]


def _chain_lines(d: dict) -> list[str]:
    if d.get("listed") is False:
        return [f"  {d['expiration']} not listed; neighbours: " + ", ".join(
            f"{n['expiration']} {n['usability']['verdict']}" for n in d["neighbours"].values())]
    u = d["usability"]
    return [f"  ATM IV {_fmt(d['atm_iv_pct'])}%",
            f"  usability {u['verdict']} ({u['reason']}): median spread "
            f"{_fmt(u['median_spread_pct'])}%, OI>=500 share {_fmt(u['share_strikes_oi_ge_500'])}",
            f"  call walls {_walls(d['walls']['call'])}",
            f"  put walls  {_walls(d['walls']['put'])}"]


def _rv_lines(d: dict) -> list[str]:
    head = "  " + " " * 16 + "  ".join(f"{w:>6}" for w in d["windows"])
    rows = [f"  {est:<16}" + "  ".join(f"{_fmt(v):>6}" for v in cells.values())
            for est, cells in d["matrix"].items()]
    worst_long, worst_short, gap = d["worst_for_long"], d["worst_for_short"], d["gap_signature"]
    return [head, *rows,
            f"  worst for a long:  {worst_long['estimator']} {worst_long['window']}d "
            f"{worst_long['rv_pct']}%",
            f"  worst for a short: {worst_short['estimator']} {worst_short['window']}d "
            f"{worst_short['rv_pct']}%",
            f"  close-to-close minus Parkinson: {gap['c2c_minus_parkinson_pts']} "
            f"flag {gap['flag'] or 'none'}"]


def _crack_lines(d: dict) -> list[str]:
    return [f"  {c['name']:<12} {_fmt(c['value'])} {c['unit']}  ({c['delivery_month']}, "
            f"{c['value_date']})  5-sess {_fmt(c['change_5'])}  21-sess {_fmt(c['change_21'])}"
            for c in d["cracks"]]


def _lowest_since(low: dict) -> str:
    same = low["same_week"]
    same_text = (f"same week: lowest since {same['week_ending']}" if same["week_ending"]
                 else f"same week: {same.get('note', '—')}")
    return f"all weeks: lower on {low['all_weeks']['week_ending'] or '—'}; {same_text}"


def _physical_lines(d: list[dict]) -> list[str]:
    return [f"  {x['series']} {x['region']}: {_fmt(x['value'])} {x['unit']} (week "
            f"{x['week_ending']}), 5y-band pctile {_fmt(x['percentile'])}\n"
            f"    {_lowest_since(x['lowest_since'])}" if x else "  —"
            for x in (item["data"] for item in d)]


def _positioning_lines(d: list[dict]) -> list[str]:
    return [f"  {x['root']}: MM net {x['managed_money']['net']:,} ({_fmt(x['net_pct_oi'])}% OI)"
            f"  1y pctile {_fmt(x['percentiles']['net']['1y'])}  printed {x['report_date']}"
            f"  released {x['release_date']}{'  STALE' if x['stale'] else ''}" if x else "  —"
            for x in (item["data"] for item in d)]


def _calendar_lines(d: dict) -> list[str]:
    return [f"  {e['date']}  {'✓' if e['verified'] else '?'}  {e['event']}  {e['source_url']}"
            for e in d["events"]]


def _market_lines(d: dict) -> list[str]:
    def change(c: dict | None) -> str:
        return _fmt(c["value"]) if c else "—"
    return [f"  {m.get('label')}: {_fmt(m['probability'])} (24h {change(m['change_24h'])}, "
            f"7d {change(m['change_7d'])}) — resolves: {str(m['resolution_text'])[:160]}"
            for m in d["markets"]]


def _sweep_lines(d: dict) -> list[str]:
    return [f"  {ln['status']:<13} {'HIT ' if ln.get('found') else '    '}{ln['item']}  "
            f"{ln.get('url') or ''}" for ln in d["lines"]]


def _order_lines(d: dict) -> list[str]:
    if "odds" not in d:
        return [f"  stock order {d['order']}  notional {_fmt(d['notional'])}"]
    p, liq, h, o, v = d["price"], d["liquidity"], d["hurdle"], d["odds"], d["vol"]
    lines = [f"  {d['order']['side']} {d['order']['qty']:g} x {d['order']['strike']:g} "
             f"{d['order']['instrument']} {d['order']['expiration']}: bid {_fmt(p['bid'])} "
             f"ask {_fmt(p['ask'])} -> fill {_fmt(p['fill_assumed'])}, premium {_fmt(p['premium'])}",
             f"  liquidity: {_fmt(liq['qty_pct_of_oi'])}% of strike OI {_fmt(liq['strike_oi'])}, "
             f"spread {_fmt(liq['spread_pct'])}%, round trip {_fmt(liq['round_trip_spread_cost'])}",
             f"  hurdle: breakeven move {_fmt(h['move_to_breakeven_pct'])}% vs implied move "
             f"{_fmt(h['implied_move_pct'])}% ({h['implied_move_basis']})"]
    if o:
        tail = (f"P(total loss) {o['p_total_loss']:.0%}" if "p_total_loss" in o
                else f"P(assigned) {o['p_assigned']:.0%}")
        lines.append(f"  odds (risk-neutral): median P&L {_fmt(o['median_pnl_per_contract'])}/contract, "
                     f"{tail}, P(profit) {o['p_profit']:.0%}")
    if v:
        lines.append(f"  vol: IV {v['iv_mid_pct']}% = worst-for-long RV "
                     f"{v['worst_for_long']['rv_pct']}% + {v['premium_over_worst_for_long_pts']} pts")
    return lines


def _book_lines(d: dict) -> list[str]:
    if "order" not in d:
        return [f"  {n}: delta-notional {_fmt(v['delta_notional'])}  "
                f"{_fmt(v['pct_of_book'])}% of book{'  BREACH' if v['breach'] else ''}"
                for n, v in d["per_name"].items()] or ["  no open positions"]
    m = d["order_mark"]
    return [f"  order {d['order']}",
            f"  mark {_fmt(m['mark'])}  delta {_fmt(m['delta'])}  delta-notional "
            f"{_fmt(m['delta_notional'])}  flags {m['flags'] or '—'}",
            f"  {d['symbol']} delta-notional {_fmt(d['delta_notional_before'])} -> "
            f"{_fmt(d['delta_notional_after'])}  = {_fmt(d['pct_of_book_after'])}% of book "
            f"(cap {d['cap_pct']}%){'  BREACH' if d['breach'] else ''}"]


RENDERERS: dict[str, Callable[[dict], list[str]]] = {
    "spot": _spot_lines, "chain": _chain_lines, "order": _order_lines,
    "realized_vol": _rv_lines, "crack": _crack_lines, "physical": _physical_lines,
    "positioning": _positioning_lines, "sweep": _sweep_lines, "calendar": _calendar_lines,
    "markets": _market_lines, "book": _book_lines,
}


def _section_lines(name: str, sec: dict) -> list[str]:
    """Headline numbers per section; the JSON (`--json`) carries the rest."""
    if sec["data"] is None:
        return ["  not verified — see below"]
    return RENDERERS[name](sec["data"])


def render_pretrade(page: dict) -> str:
    d = page["data"]
    out = [f"PRETRADE {d['symbol']} {d['expiration']}  (asof {d['asof_date']}"
           f"{', OFFLINE' if d['offline'] else ''})", "",
           f"RED FLAGS ({len(d['flags'])})"]
    out += [f"  ✗ {f['flag']:<22} [{f['section']}] {f['detail']}" for f in d["flags"]] or ["  none"]
    out.append("")
    for name, sec in d["sections"].items():
        out.append(f"{name.upper()}  {sec['status']}  {_stamp(sec)}")
        out += _section_lines(name, sec)
        out += [f"  ! {w}" for w in sec["warnings"]]
        out.append("")
    out.append(f"NOT VERIFIED ({len(d['unsourced'])})")
    out += [f"  [{u['section']}] {u['item']}: {u['reason']}  {u.get('url') or ''}"
            for u in d["unsourced"]]
    return "\n".join(out)


# ---------- commands ----------

def cmd_pretrade(args: argparse.Namespace) -> None:
    order = json.loads(args.order) if args.order else None
    page = pretrade.pretrade(args.ticker, args.expiration, order)
    if args.json:
        _print_json(page)
    else:
        print(render_pretrade(page))


def cmd_ledger(args: argparse.Namespace) -> None:
    if args.op == "open":
        row = ledger.open_position(
            symbol=args.symbol.upper(), instrument=args.instrument, side=args.side,
            thesis_id=args.thesis, exit_condition=args.exit_condition,
            recommended_qty=args.recommended, executed_qty=args.executed,
            entry_price=args.price, strike=args.strike, expiration=args.expiration)
        _print_json(asdict(row))
    elif args.op == "close":
        row = ledger.close_position(args.id, exit_price=args.price, outcome=args.outcome,
                                    right_for_reason=args.right_for_reason,
                                    autopsy=args.autopsy)
        _print_json(asdict(row))
    elif args.op == "marks":
        _print_json(book.mark_positions())
    else:
        _print_json(book.ledger_review())


# name -> (callable, positional argument names)
FEEDS: dict[str, tuple[Callable[..., dict], tuple[str, ...]]] = {
    "quote": (fundamentals.spot_quote, ("ticker",)),
    "chain": (chains.chain_report, ("ticker", "expiration")),
    "skew-history": (chain_history.skew_history, ("ticker", "expiration")),
    "oi-change": (chain_history.oi_change, ("ticker", "expiration")),
    "rv-matrix": (bars.rv_matrix, ("ticker",)),
    "actions": (bars.corporate_actions, ("ticker",)),
    "dividends": (fundamentals.dividend_policy, ("ticker",)),
    "targets": (fundamentals.analyst_targets, ("ticker",)),
    "curve": (futures.curve, ("root",)),
    "prompt-spread": (futures.prompt_spread, ("root",)),
    "cracks": (futures.cracks, ()),
    "etf-holdings": (etf_roll.fund_holdings, ("fund",)),
    "etf-roll": (etf_roll.roll_rule, ("fund",)),
    "eia": (eia.weekly_series, ("series", "region")),
    "eia-summary": (eia.wpsr_summary, ()),
    "steo": (eia.steo, ()),
    "cot": (cot.managed_money, ("root",)),
    "sweep": (edgar.structural_sweep, ("ticker",)),
    "insiders": (edgar.insider_trades, ("ticker",)),
    "earnings": (calendar.next_earnings, ("ticker",)),
    "ex-dividend": (calendar.ex_dividend, ("ticker",)),
    "macro-calendar": (calendar.macro_calendar, ()),
    "oi-history": (chain_history.oi_history, ("ticker", "expiration")),
    "shares": (edgar.share_count_trend, ("ticker",)),
    "calendar": (calendar.events_between, ("start", "end")),
    "polymarket": (polymarket.watchlist_markets, ()),
    "polymarket-search": (polymarket.search, ("query",)),
}


def cmd_feed(args: argparse.Namespace) -> None:
    fn, names = FEEDS[args.feed]
    if len(args.args) != len(names):
        raise ValueError(f"feed {args.feed} takes {len(names)} argument(s): {' '.join(names)}")
    _print_json(fn(*args.args))


def cmd_plot(args: argparse.Namespace) -> None:
    from .charts import render

    order = json.loads(args.order) if args.order else None
    path, _ = render(args.kind, args.args, order=order, out=args.out)
    print(f"saved {path}")


def add_parsers(sub: argparse._SubParsersAction) -> None:
    pt = sub.add_parser("pretrade", help="the one-call pre-trade page (sourced inputs only)")
    pt.add_argument("ticker")
    pt.add_argument("expiration", help="YYYY-MM-DD")
    pt.add_argument("--order", help='proposed order JSON: {"symbol","instrument","side","qty",'
                                    '"strike"?,"expiration"?}')
    pt.add_argument("--json", action="store_true", help="print the full JSON envelope")
    pt.set_defaults(func=cmd_pretrade)

    ra = sub.add_parser("refresh-all", help="snapshot every feed for watchlist + open positions")
    ra.add_argument("--tickers", nargs="*", default=None)
    ra.set_defaults(func=lambda a: _print_json(jobs.refresh_all(a.tickers)))

    wc = sub.add_parser("weekly-check", help="the Sunday read, with alerts")
    wc.set_defaults(func=lambda a: _print_json(jobs.weekly_check()))

    lg = sub.add_parser("ledger", help="append-only trade ledger")
    lg_sub = lg.add_subparsers(dest="op", required=True)
    lo = lg_sub.add_parser("open", help="record an opened position")
    for flag in ("--symbol", "--instrument", "--side", "--thesis", "--exit-condition"):
        lo.add_argument(flag, required=True)
    for flag in ("--recommended", "--executed", "--price"):
        lo.add_argument(flag, type=float, required=True)
    lo.add_argument("--strike", type=float)
    lo.add_argument("--expiration")
    lc = lg_sub.add_parser("close", help="close with outcome, right-for-reason, autopsy")
    lc.add_argument("--id", required=True)
    lc.add_argument("--price", type=float, required=True)
    for flag in ("--outcome", "--right-for-reason", "--autopsy"):
        lc.add_argument(flag, required=True)
    lg_sub.add_parser("marks", help="open positions marked, delta-notional per name")
    lg_sub.add_parser("review", help="blank reviews and oversize executions")
    lg.set_defaults(func=cmd_ledger)

    from .charts import CHARTS

    pl = sub.add_parser("plot", help="draw a sourced-data chart (sheet, chain, rv, curve, "
                                     "cracks, cot, order) to PNG")
    pl.add_argument("kind", choices=sorted(CHARTS))
    pl.add_argument("args", nargs="*", help="e.g. `sheet SU 2027-03-19`, `curve CL`")
    pl.add_argument("--order", help="order JSON (for `order` and `sheet`)")
    pl.add_argument("--out", help="output PNG (default .optionslab/charts/)")
    pl.set_defaults(func=cmd_plot)

    fd = sub.add_parser("feed", help="run one data feed, print its JSON envelope")
    fd.add_argument("feed", choices=sorted(FEEDS))
    fd.add_argument("args", nargs="*")
    fd.set_defaults(func=cmd_feed)
