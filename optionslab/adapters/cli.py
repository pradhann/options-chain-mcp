"""Command-line interface — one verb per analysis.

Every verb that operates on a Position accepts the SAME position spec:

    --position NAME      a saved position from .optionslab/positions.json
    --legs JSON          inline JSON array of legs

Verbs:

    chain      fetch + decompose an options chain (+ Greeks)
    payoff     expiration P&L at a price
    value      mark-to-model value at S, σ, t
    greeks     portfolio Greeks at S, σ, t
    metrics    closed-form max P / max L / breakevens
    scenario   P&L grid over spot × time
    chart      unified chart dispatcher
    positions  list/show/save/delete saved positions
    data       realized vol / IV term / earnings / news / targets
    interactive   conversational shell

Run `python -m optionslab <verb> --help` for verb-specific options.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from ..core.market import MarketContext
from ..core.position import Position
from ..errors import OptionsLabError
from ..storage.positions import (
    delete_position,
    get_position,
    list_positions,
    positions_path,
    save_position,
)


WELCOME = """\
optionslab — options analytics toolkit.

Position analysis
  optionslab chain --ticker SPY
  optionslab payoff --legs '[{"side":"long","option_type":"call","strike":100,"premium":6}]' --at 110
  optionslab metrics --position bull-call-100-110
  optionslab greeks  --position bull-call-100-110 --spot 105 --sigma 0.30
  optionslab scenario --position bull-call-100-110 --spot 100 --sigma 0.30 --plot

Volatility (Week 3)
  optionslab dashboard            # the daily vol-dashboard ritual
  optionslab vrp                  # VRP today + 2y percentile
  optionslab term-structure       # VIX curve + regime
  optionslab skew --ticker SPY --exp-index 4
  optionslab vix-strip            # model-free VIX replication
  optionslab event-vol --ticker NVDA --front-exp ... --back-exp ...
  optionslab rv --ticker SPY --estimator yang_zhang

Charts (unified dispatcher)
  optionslab chart --kind primitives --strike 100 --premium 5
  optionslab chart --kind smile --ticker SPY --exp-index 4
  optionslab chart --kind vrp --save-plot vrp.png
  optionslab chart --kind term --save-plot term.png
  optionslab chart --kind skew-curve --ticker SPY --exp-index 4
  optionslab chart --kind dashboard --save-plot dashboard.png

Storage: positions in .optionslab/positions.json; vol-percentile history in
.optionslab/history/<key>.csv (grows as you run the dashboard daily).
"""


# ---------- shared helpers ----------

def _build_position(args: argparse.Namespace) -> Position:
    """Resolve the --position spec into a Position."""
    name = getattr(args, "position", None)
    legs_json = getattr(args, "legs", None)
    if (name is None) == (legs_json is None):
        raise OptionsLabError(
            "supply exactly one of --position NAME or --legs JSON"
        )
    if name is not None:
        return get_position(name)
    legs = json.loads(legs_json)
    return Position.from_dicts(legs)


def _market_from_args(args: argparse.Namespace) -> MarketContext:
    """Build a MarketContext from CLI args (--spot / --r / --q)."""
    return MarketContext.explicit(
        spot=getattr(args, "spot", None),
        r=getattr(args, "r", None) if getattr(args, "r", None) is not None else 0.045,
        q=getattr(args, "q", 0.0),
    )


def _print(payload) -> None:
    """Pretty-print a result dataclass or dict as JSON."""
    if hasattr(payload, "to_dict"):
        payload = payload.to_dict()
    print(json.dumps(payload, indent=2, default=str))


def _add_position_arg(parser: argparse.ArgumentParser) -> None:
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--position", help="Saved position name")
    g.add_argument("--legs", help="Inline JSON array of legs")


def _add_market_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--spot", type=float, required=True,
                        help="Underlying spot price")
    parser.add_argument("--r", type=float, default=None,
                        help="Risk-free rate decimal (default 0.045)")
    parser.add_argument("--q", type=float, default=0.0,
                        help="Dividend yield decimal (default 0)")
    parser.add_argument("--sigma", type=float, default=None,
                        help="Single IV decimal broadcast to every leg "
                             "(omit to auto-resolve from chain)")


# ---------- commands ----------

def cmd_chain(args: argparse.Namespace) -> None:
    from ..data.chain import filter_near_money, load_chain, PRINT_COLUMNS
    import pandas as pd

    chain = load_chain(args.ticker, expiration=args.expiration,
                       exp_index=args.exp_index)
    print(f"\n{chain.symbol}: {len(chain.expirations)} expirations")
    for i, exp in enumerate(chain.expirations):
        marker = "  *" if exp == chain.expiration else "   "
        print(f"{marker} [{i:2d}] {exp}")
    print(f"\n{chain.symbol} spot: ${chain.spot:,.2f}  "
          f"r={chain.r:.2%}  q={chain.q:.2%}  exp {chain.expiration}")

    calls, puts = chain.calls, chain.puts
    if args.near_money:
        calls = filter_near_money(calls, chain.spot, args.near_money)
        puts = filter_near_money(puts, chain.spot, args.near_money)
    cols = PRINT_COLUMNS + (["Delta", "Gamma", "Theta", "Vega"]
                            if "Delta" in calls.columns else [])
    print(f"\n===== CALLS  {chain.symbol}  {chain.expiration} =====")
    print(calls[cols].to_string(index=False))
    print(f"\n===== PUTS   {chain.symbol}  {chain.expiration} =====")
    print(puts[cols].to_string(index=False))


def cmd_payoff(args: argparse.Namespace) -> None:
    from ..analysis.payoff import expiration_payoff
    pos = _build_position(args)
    _print(expiration_payoff(pos, args.at))


def cmd_value(args: argparse.Namespace) -> None:
    from ..analysis.valuation import value
    pos = _build_position(args)
    market = _market_from_args(args)
    _print(value(pos, market, ivs=args.sigma))


def cmd_greeks(args: argparse.Namespace) -> None:
    from ..analysis.valuation import greeks
    pos = _build_position(args)
    market = _market_from_args(args)
    _print(greeks(pos, market, ivs=args.sigma))


def cmd_metrics(args: argparse.Namespace) -> None:
    from ..analysis.metrics import position_metrics
    pos = _build_position(args)
    _print(position_metrics(pos))


def cmd_scenario(args: argparse.Namespace) -> None:
    from ..analysis.scenario import scenario_grid
    pos = _build_position(args)
    market = _market_from_args(args)
    spot_pcts = json.loads(args.spot_pcts) if args.spot_pcts else None
    days = json.loads(args.days_forward) if args.days_forward else None
    result = scenario_grid(pos, market, spot_pcts=spot_pcts,
                           days_forward=days, ivs=args.sigma)
    _print(result)
    if args.plot or args.save_plot:
        import matplotlib.pyplot as plt
        from ..plotting.scenario import plot_pnl_grid
        plot_pnl_grid(result, save_path=args.save_plot)
        if args.plot:
            plt.show()


def cmd_chart(args: argparse.Namespace) -> None:
    """Unified chart dispatcher. Pick a kind, supply its inputs."""
    import matplotlib.pyplot as plt

    kind = args.kind
    if kind == "payoff":
        from ..plotting.payoff import plot_payoff
        pos = _build_position(args)
        plot_payoff(pos, save_path=args.save_plot, title=args.title)
    elif kind == "primitives":
        from ..plotting.payoff import plot_primitives
        plot_primitives(strike=args.strike, premium=args.premium,
                        save_path=args.save_plot)
    elif kind == "verticals":
        from ..plotting.payoff import plot_vertical_spreads
        plot_vertical_spreads(args.k1, args.k2,
                              args.c1, args.c2, args.p1, args.p2,
                              save_path=args.save_plot)
    elif kind == "greek":
        from ..plotting.greek import plot_greek_vs_spot
        from ..pricing import year_fraction
        T = (year_fraction(args.expiration) if args.expiration
             else args.days / 365.0)
        r = args.r if args.r is not None else 0.045
        plot_greek_vs_spot(args.strike, T, r, args.sigma, args.type, args.q,
                           greek=args.greek, overlay_oracle=args.verify,
                           save_path=args.save_plot)
    elif kind == "chain":
        from ..data.chain import filter_liquid, load_chain
        from ..plotting.chain import plot_extrinsic
        chain = load_chain(args.ticker, expiration=args.expiration,
                           exp_index=args.exp_index, greeks=False)
        calls = filter_liquid(chain.calls)
        puts = filter_liquid(chain.puts)
        plot_extrinsic(calls, puts, chain.spot, chain.symbol,
                       chain.expiration, save_path=args.save_plot)
    elif kind == "scenario":
        from ..analysis.scenario import scenario_grid
        from ..plotting.scenario import plot_pnl_grid
        pos = _build_position(args)
        market = _market_from_args(args)
        result = scenario_grid(pos, market, ivs=args.sigma)
        plot_pnl_grid(result, save_path=args.save_plot)
    elif kind == "smile":
        from ..data.chain import load_chain
        from ..plotting.vol import plot_iv_smile
        ch = load_chain(args.ticker, expiration=args.expiration,
                        exp_index=args.exp_index)
        plot_iv_smile(ch, save_path=args.save_plot)
    elif kind == "rv":
        from ..data.vol import realized_vol_all_estimators
        from ..plotting.vol import plot_rv_estimators
        df = realized_vol_all_estimators(args.ticker, args.days_int or 21,
                                         lookback_days=1260)
        plot_rv_estimators(df, save_path=args.save_plot)
    elif kind == "vrp":
        from ..analysis.vol.vrp import vrp_history
        from ..plotting.vol import plot_vrp
        res = vrp_history(years=15)
        plot_vrp(res.series, marks={"Volmageddon": "2018-02-05",
                                     "Covid": "2020-03-16",
                                     "Aug 5 2024": "2024-08-05"},
                 save_path=args.save_plot)
    elif kind == "term":
        from ..analysis.vol.term import term_structure_history
        from ..plotting.vol import plot_term_structure
        hist = term_structure_history(lookback_days=1260)
        plot_term_structure(hist, save_path=args.save_plot)
    elif kind == "skew-curve":
        from ..data.chain import load_chain
        from ..plotting.vol import plot_skew_curve
        ch = load_chain(args.ticker, expiration=args.expiration,
                        exp_index=args.exp_index)
        plot_skew_curve(ch, save_path=args.save_plot)
    elif kind == "vix-strip":
        from ..analysis.vol.strip import vix_strip
        from ..plotting.vol import plot_vix_strip_overlay
        res = vix_strip(symbol=args.ticker, target_days=args.days_int or 30,
                        wing_boost_pct=20.0)
        plot_vix_strip_overlay(res, save_path=args.save_plot)
    elif kind == "greek-time":
        from ..data.quotes import get_risk_free_rate
        from ..plotting.greek import plot_greek_vs_time
        r = args.r if args.r is not None else get_risk_free_rate()
        spot = args.spot if args.spot is not None else args.strike
        plot_greek_vs_time(
            args.strike, spot, r, args.sigma, args.type, args.q,
            greek=args.greek, save_path=args.save_plot,
        )
    elif kind == "dashboard":
        from ..analysis.vol.dashboard import vol_dashboard
        from ..plotting.vol import plot_dashboard_panel
        res = vol_dashboard()
        plot_dashboard_panel(res, save_path=args.save_plot)
    else:
        raise OptionsLabError(f"unknown chart kind {kind!r}")

    if args.save_plot:
        print(f"Saved to {args.save_plot}")
    if args.plot:
        plt.show()


def cmd_positions(args: argparse.Namespace) -> None:
    op = args.op
    if op == "list":
        names = list_positions()
        if not names:
            print(f"(no positions saved at {positions_path()})")
            return
        for n in names:
            print(n)
    elif op == "show":
        pos = get_position(args.name)
        _print(pos.to_dict())
    elif op == "save":
        pos = Position.from_dicts(json.loads(args.legs),
                                  name=args.name, symbol=args.symbol,
                                  notes=args.notes)
        path = save_position(pos)
        print(f"saved {args.name} to {path}")
    elif op == "delete":
        ok = delete_position(args.name)
        print("deleted" if ok else "not found")
    elif op == "path":
        print(positions_path())
    else:
        raise OptionsLabError(f"unknown positions op {op!r}")


def cmd_data(args: argparse.Namespace) -> None:
    op = args.op
    if op == "realized-vol":
        from ..data.vol import realized_vol
        print(json.dumps({"ticker": args.ticker.upper(),
                          "window_days": args.window,
                          "realized_vol_pct": realized_vol(args.ticker, args.window)},
                         indent=2))
    elif op == "iv-term":
        from ..data.vol import iv_term_structure
        print(json.dumps(iv_term_structure(args.ticker, args.max),
                         indent=2))
    elif op == "earnings":
        from ..data.events import next_earnings
        from ..data.quotes import make_ticker
        print(json.dumps(next_earnings(make_ticker(args.ticker)),
                         indent=2, default=str))
    elif op == "news":
        from ..data.events import recent_news
        from ..data.quotes import make_ticker
        print(json.dumps(recent_news(make_ticker(args.ticker), args.n),
                         indent=2))
    elif op == "targets":
        from ..data.events import analyst_targets
        from ..data.quotes import make_ticker
        print(json.dumps(analyst_targets(make_ticker(args.ticker)),
                         indent=2, default=str))
    else:
        raise OptionsLabError(f"unknown data op {op!r}")


# ---------- Week 3 commands ----------

def cmd_rv(args: argparse.Namespace) -> None:
    """Realized volatility: single value or full estimator zoo."""
    from ..data.vol import (
        ESTIMATORS, realized_vol, realized_vol_all_estimators,
    )
    if args.all:
        df = realized_vol_all_estimators(args.ticker, args.window,
                                         lookback_days=args.lookback)
        latest = df.iloc[-1].to_dict()
        print(json.dumps({
            "ticker": args.ticker.upper(),
            "window_days": args.window,
            "latest_pct_by_estimator": {k: round(v, 3) for k, v in latest.items()},
        }, indent=2))
    else:
        if args.estimator not in ESTIMATORS:
            raise OptionsLabError(
                f"--estimator must be one of {ESTIMATORS}, got {args.estimator!r}"
            )
        v = realized_vol(args.ticker, args.window, estimator=args.estimator)
        print(json.dumps({
            "ticker": args.ticker.upper(),
            "estimator": args.estimator,
            "window_days": args.window,
            "realized_vol_pct": v,
        }, indent=2))


def cmd_vrp(args: argparse.Namespace) -> None:
    from ..analysis.vol.vrp import vrp_history, vrp_today
    if args.history:
        res = vrp_history(years=args.years)
        _print(res)
    else:
        _print(vrp_today())


def cmd_term_structure(args: argparse.Namespace) -> None:
    from ..analysis.vol.term import term_structure
    _print(term_structure())


def cmd_skew(args: argparse.Namespace) -> None:
    from ..analysis.vol.skew import skew_metrics
    res = skew_metrics(
        args.ticker, expiration=args.expiration, exp_index=args.exp_index,
        save_history=args.save_history,
    )
    _print(res)


def cmd_vix_strip(args: argparse.Namespace) -> None:
    from ..analysis.vol.strip import vix_strip
    _print(vix_strip(symbol=args.ticker, target_days=args.target_days,
                     wing_boost_pct=args.wing_boost))


def cmd_event_vol(args: argparse.Namespace) -> None:
    from ..analysis.vol.event import event_implied_move
    _print(event_implied_move(args.ticker, args.front_exp, args.back_exp))


def cmd_dashboard(args: argparse.Namespace) -> None:
    from ..analysis.vol.dashboard import format_dashboard, vol_dashboard
    from ..plotting.vol import plot_dashboard_panel
    res = vol_dashboard(
        skew_symbol=args.skew_ticker,
        skew_exp_index=args.skew_exp_index,
        save_history=not args.no_save_history,
        yesterdays_call=args.yesterdays_call,
        vol_view=args.vol_view,
    )
    if args.json:
        _print(res)
    else:
        print(format_dashboard(res))
    if args.save_plot:
        plot_dashboard_panel(res, save_path=args.save_plot)
        print(f"\nPanel saved to {args.save_plot}")


# ---------- Week 1 & 2 curriculum additions ----------

def cmd_parity(args: argparse.Namespace) -> None:
    """W1.D5: put-call parity check at one strike / expiration."""
    from ..analysis.parity import parity_check
    _print(parity_check(args.ticker, args.strike, args.expiration,
                        r=args.r, q=args.q))


def cmd_synthetic(args: argparse.Namespace) -> None:
    """W1.D6: build a canonical synthetic and verify against the target."""
    from ..analysis.synthetics import (
        long_stock_payoff, short_stock_payoff,
        synthetic_long_call, synthetic_long_put, synthetic_long_stock,
        synthetic_short_call, synthetic_short_put, synthetic_short_stock,
        verify_synthetic,
    )
    K = args.strike
    cp = args.call_premium
    pp = args.put_premium
    s_lo = args.s_min if args.s_min is not None else 0.5 * K
    s_hi = args.s_max if args.s_max is not None else 1.5 * K

    builders = {
        "long-stock":  (lambda: synthetic_long_stock(K, cp, pp),
                        long_stock_payoff(K)),
        "short-stock": (lambda: synthetic_short_stock(K, cp, pp),
                        short_stock_payoff(K)),
        "long-call":   (lambda: synthetic_long_call(K, pp),
                        None),    # target = the call's payoff vs stock+put
        "short-call":  (lambda: synthetic_short_call(K, pp), None),
        "long-put":    (lambda: synthetic_long_put(K, cp), None),
        "short-put":   (lambda: synthetic_short_put(K, cp), None),
    }
    if args.kind not in builders:
        raise OptionsLabError(
            f"--kind must be one of {sorted(builders)}, got {args.kind!r}"
        )
    build, target = builders[args.kind]
    if target is None:
        raise OptionsLabError(
            f"synthetic '{args.kind}' has a stock component; pass --strike, "
            f"--call-premium, --put-premium and we'll verify long/short-stock "
            f"forms in this command. The other four synthetics are verified "
            f"by inverting parity — use `optionslab parity` for that side."
        )
    synth = build()
    res = verify_synthetic(target, synth, s_range=(s_lo, s_hi),
                           points=args.points)
    _print(res)


def cmd_interactive(_args: argparse.Namespace) -> None:
    from .interactive import run_interactive
    raise SystemExit(run_interactive())


# ---------- parser ----------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="optionslab",
        description=WELCOME,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command")

    # chain
    c = sub.add_parser("chain", help="fetch + decompose an options chain")
    c.add_argument("--ticker", required=True)
    cg = c.add_mutually_exclusive_group()
    cg.add_argument("--expiration", default=None, help="YYYY-MM-DD")
    cg.add_argument("--exp-index", type=int, default=None,
                    help="0 = nearest")
    c.add_argument("--near-money", type=int, default=0)
    c.set_defaults(func=cmd_chain)

    # payoff
    py = sub.add_parser("payoff", help="expiration P&L at a price")
    _add_position_arg(py)
    py.add_argument("--at", type=float, required=True,
                    help="Underlying price at expiration")
    py.set_defaults(func=cmd_payoff)

    # value
    v = sub.add_parser("value", help="mark-to-model value (and PnL vs entry)")
    _add_position_arg(v)
    _add_market_args(v)
    v.set_defaults(func=cmd_value)

    # greeks
    gk = sub.add_parser("greeks", help="portfolio Greeks (delta/gamma/theta/vega/rho)")
    _add_position_arg(gk)
    _add_market_args(gk)
    gk.set_defaults(func=cmd_greeks)

    # metrics
    m = sub.add_parser("metrics",
                       help="closed-form max profit / max loss / breakevens")
    _add_position_arg(m)
    m.set_defaults(func=cmd_metrics)

    # scenario
    sc = sub.add_parser("scenario", help="P&L grid over spot moves x days forward")
    _add_position_arg(sc)
    _add_market_args(sc)
    sc.add_argument("--spot-pcts", default=None,
                    help="JSON list, e.g. '[-20,-10,0,10,20]'")
    sc.add_argument("--days-forward", default=None,
                    help="JSON list, e.g. '[0,7,14,30]'")
    sc.add_argument("--plot", action="store_true")
    sc.add_argument("--save-plot", default=None)
    sc.set_defaults(func=cmd_scenario)

    # chart (unified)
    ch = sub.add_parser(
        "chart",
        help="render a chart (--kind payoff|primitives|verticals|greek|chain|scenario)",
    )
    ch.add_argument(
        "--kind", required=True,
        choices=[
            # position charts
            "payoff", "primitives", "verticals", "greek", "chain", "scenario",
            # Week 2 (time-axis)
            "greek-time",
            # Week 3 charts
            "smile", "rv", "vrp", "term", "skew-curve", "vix-strip",
            "dashboard",
        ],
    )
    # position spec is optional here — only some kinds need it
    ch.add_argument("--position", default=None)
    ch.add_argument("--legs", default=None)
    # all the per-kind knobs
    ch.add_argument("--title", default=None)
    ch.add_argument("--strike", type=float, default=100.0)
    ch.add_argument("--premium", type=float, default=5.0)
    ch.add_argument("--k1", type=float, default=None)
    ch.add_argument("--k2", type=float, default=None)
    ch.add_argument("--c1", type=float, default=None)
    ch.add_argument("--c2", type=float, default=None)
    ch.add_argument("--p1", type=float, default=None)
    ch.add_argument("--p2", type=float, default=None)
    ch.add_argument("--type", choices=["call", "put"], default="call")
    ch.add_argument("--sigma", type=float, default=0.30)
    ch.add_argument("--days", type=float, default=30)
    ch.add_argument("--expiration", default=None)
    ch.add_argument("--greek", default="delta")
    ch.add_argument("--r", type=float, default=None)
    ch.add_argument("--q", type=float, default=0.0)
    ch.add_argument("--spot", type=float, default=None)
    ch.add_argument("--verify", action="store_true",
                    help="overlay py_vollib oracle (greek charts)")
    ch.add_argument("--ticker", default=None)
    ch.add_argument("--exp-index", type=int, default=None)
    ch.add_argument("--days-int", type=int, default=None,
                    help="integer day param (e.g. RV window, strip target days)")
    ch.add_argument("--plot", action="store_true")
    ch.add_argument("--save-plot", default=None)
    ch.set_defaults(func=cmd_chart)

    # positions
    ps = sub.add_parser("positions", help="manage saved Positions")
    ps_sub = ps.add_subparsers(dest="op", required=True)
    ps_sub.add_parser("list", help="list saved position names")
    ps_show = ps_sub.add_parser("show", help="print a saved position")
    ps_show.add_argument("name")
    ps_save = ps_sub.add_parser("save", help="save a position by name")
    ps_save.add_argument("name")
    ps_save.add_argument("--legs", required=True,
                         help="JSON array of legs")
    ps_save.add_argument("--symbol", default=None)
    ps_save.add_argument("--notes", default=None)
    ps_del = ps_sub.add_parser("delete", help="delete a saved position")
    ps_del.add_argument("name")
    ps_sub.add_parser("path", help="print the positions.json path")
    ps.set_defaults(func=cmd_positions)

    # data
    da = sub.add_parser("data", help="miscellaneous live data fetchers")
    da_sub = da.add_subparsers(dest="op", required=True)
    da_rv = da_sub.add_parser("realized-vol")
    da_rv.add_argument("--ticker", required=True)
    da_rv.add_argument("--window", type=int, default=30)
    da_iv = da_sub.add_parser("iv-term")
    da_iv.add_argument("--ticker", required=True)
    da_iv.add_argument("--max", type=int, default=12)
    da_e = da_sub.add_parser("earnings")
    da_e.add_argument("--ticker", required=True)
    da_n = da_sub.add_parser("news")
    da_n.add_argument("--ticker", required=True)
    da_n.add_argument("--n", type=int, default=10)
    da_t = da_sub.add_parser("targets")
    da_t.add_argument("--ticker", required=True)
    da.set_defaults(func=cmd_data)

    # ---------- Week 3 ----------

    rv = sub.add_parser("rv", help="annualized realized vol (estimator zoo)")
    rv.add_argument("--ticker", required=True)
    rv.add_argument("--window", type=int, default=21)
    rv.add_argument("--estimator", default="close_to_close")
    rv.add_argument("--lookback", type=int, default=1260)
    rv.add_argument("--all", action="store_true",
                    help="report all five estimators side-by-side")
    rv.set_defaults(func=cmd_rv)

    vrp = sub.add_parser("vrp", help="variance risk premium (today or history)")
    vrp.add_argument("--history", action="store_true")
    vrp.add_argument("--years", type=int, default=15)
    vrp.set_defaults(func=cmd_vrp)

    ts_cmd = sub.add_parser("term-structure",
                            help="VIX-family curve + regime")
    ts_cmd.set_defaults(func=cmd_term_structure)

    sk = sub.add_parser("skew",
                        help="25Δ Risk Reversal + 25Δ Butterfly for an expiration")
    sk.add_argument("--ticker", required=True)
    sg = sk.add_mutually_exclusive_group()
    sg.add_argument("--expiration", default=None)
    sg.add_argument("--exp-index", type=int, default=None)
    sk.add_argument("--save-history", action="store_true",
                    help="append today's RR/BF to .optionslab/history/")
    sk.set_defaults(func=cmd_skew)

    vs = sub.add_parser("vix-strip",
                        help="model-free VIX replication from an OTM strip")
    vs.add_argument("--ticker", default="SPX")
    vs.add_argument("--target-days", type=int, default=30)
    vs.add_argument("--wing-boost", type=float, default=20.0)
    vs.set_defaults(func=cmd_vix_strip)

    ev = sub.add_parser("event-vol",
                        help="forward-variance event-implied 1-day move")
    ev.add_argument("--ticker", required=True)
    ev.add_argument("--front-exp", required=True,
                    help="YYYY-MM-DD; expires immediately after the event")
    ev.add_argument("--back-exp", required=True,
                    help="YYYY-MM-DD; expires later")
    ev.set_defaults(func=cmd_event_vol)

    db = sub.add_parser("dashboard", help="Vol Dashboard (the daily ritual)")
    db.add_argument("--skew-ticker", default="SPY")
    db.add_argument("--skew-exp-index", type=int, default=4)
    db.add_argument("--no-save-history", action="store_true",
                    help="skip writing today's RR/BF to history file")
    db.add_argument("--yesterdays-call", default=None,
                    help="one-line self-grade on the prior day's call")
    db.add_argument("--vol-view", default=None,
                    help="150-word vol view synthesizing today's reads")
    db.add_argument("--json", action="store_true",
                    help="emit structured JSON instead of the pretty table")
    db.add_argument("--save-plot", default=None,
                    help="also save the dashboard panel as a PNG")
    db.set_defaults(func=cmd_dashboard)

    # ---------- Week 1 + 2 curriculum additions ----------

    par = sub.add_parser("parity",
                         help="W1.D5 put-call parity check at one strike")
    par.add_argument("--ticker", required=True)
    par.add_argument("--strike", type=float, required=True)
    par.add_argument("--expiration", required=True, help="YYYY-MM-DD")
    par.add_argument("--r", type=float, default=None,
                     help="risk-free decimal; default = live 13-week T-bill")
    par.add_argument("--q", type=float, default=None,
                     help="dividend yield decimal; default = trailing")
    par.set_defaults(func=cmd_parity)

    syn = sub.add_parser("synthetic",
                         help="W1.D6 build + verify a canonical synthetic")
    syn.add_argument(
        "--kind", required=True,
        choices=["long-stock", "short-stock"],
        help=("synthetic-long/short-stock are the parity-pure stock-side "
              "synthetics. The four call/put synthetics share the same math "
              "— use `parity` for those."),
    )
    syn.add_argument("--strike", type=float, required=True)
    syn.add_argument("--call-premium", type=float, required=True)
    syn.add_argument("--put-premium", type=float, required=True)
    syn.add_argument("--s-min", type=float, default=None)
    syn.add_argument("--s-max", type=float, default=None)
    syn.add_argument("--points", type=int, default=21)
    syn.set_defaults(func=cmd_synthetic)

    # interactive
    it = sub.add_parser("interactive", help="conversational shell")
    it.set_defaults(func=cmd_interactive)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        from .interactive import run_interactive
        return run_interactive()
    try:
        args.func(args)
    except OptionsLabError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
