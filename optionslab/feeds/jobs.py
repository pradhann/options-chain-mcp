"""Scheduled jobs: `refresh_all` writes today's snapshots; `weekly_check` reads them.

refresh_all  runs every feed for the watchlist and every open ledger
             position, so the next offline read (and `pretrade`) has
             today's data: chains for every listed expiry (item 1),
             daily OHLC (3), futures curves and cracks (4), ETF roll
             (5), EIA WPSR (6), COT (7), SEC sweep (8), calendar (9),
             Polymarket (10). Meant for 15:45 ET on trading days.

weekly_check the Sunday read: prompt spreads, the configured EIA series,
             COT staleness, Form 4 codes, calendar inside 45 days, and
             Polymarket alerts. Returns alerts, each with its source.

A job that raises is recorded with its error and the rest still run; a
refresh never fails silently and never stops half way.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

from ..storage import ledger
from ..storage import snapshots as snap
from . import (
    bars,
    calendar,
    chain_history,
    chains,
    config,
    cot,
    edgar,
    eia,
    etf_roll,
    futures,
    polymarket,
)
from .envelope import envelope, iso, provenance

USCF_FUNDS = ("USO", "BNO", "UGA", "UNG")
CALENDAR_HORIZON_DAYS = 45


def tracked_symbols() -> list[str]:
    """Watchlist plus every symbol with an open ledger position."""
    open_names = {r.symbol for r in ledger.current().values() if r.is_open}
    return sorted({s.upper() for s in config.get("watchlist")} | open_names)


def _run(jobs: list[tuple[str, Callable[[], dict]]]) -> list[dict]:
    out = []
    for name, job in jobs:
        try:
            env = job()
            out.append({"job": name, "status": env["status"],
                        "not_verified": len(env["not_verified"])})
        except Exception as e:  # orchestration boundary: record and continue
            out.append({"job": name, "status": "error", "error": f"{type(e).__name__}: {e}"})
    return out


def refresh_all(symbols: list[str] | None = None) -> dict:
    """Snapshot every feed for `symbols` (default: tracked_symbols())."""
    if snap.is_offline():
        return envelope(None, prov=provenance(source="refresh_all", quality="unavailable"),
                        warnings=["offline mode: refresh_all writes nothing"])
    names = symbols or tracked_symbols()
    ctx = config.get("pretrade_context")
    jobs: list[tuple[str, Callable[[], dict]]] = []
    for sym in names:
        jobs += [(f"chains {sym}", lambda s=sym: chains.record_chains(s)),
                 (f"ohlc {sym}", lambda s=sym: bars.daily_bars(s)),
                 (f"sweep {sym}", lambda s=sym: edgar.structural_sweep(s))]
    jobs += [(f"curve {root}", lambda r=root: futures.curve(r)) for root in futures.ROOTS]
    jobs += [("cracks", futures.cracks)]
    jobs += [(f"roll {fund}", lambda f=fund: etf_roll.roll_rule(f)) for fund in USCF_FUNDS]
    jobs += [("eia wpsr", eia.wpsr_summary), ("eia steo", eia.steo),
             ("cot", cot.positioning_summary),
             ("calendar", lambda: calendar.events_between(
                 snap.today_et(), snap.today_et() + timedelta(days=366), tickers=names,
                 extra_events=[*futures.contract_events(ctx["curve_roots"]),
                               *etf_roll.roll_events(USCF_FUNDS)])),
             ("polymarket", polymarket.watchlist_markets)]
    jobs.append(("migrate legacy skew history", chain_history.migrate_legacy_skew))
    results = _run(jobs)
    failed = [r for r in results if r["status"] in ("error", "not_verified")]
    return envelope({"symbols": names, "jobs": results, "failed": len(failed)},
                    prov=provenance(source="refresh_all", asof=iso()),
                    warnings=[f"{r['job']}: {r.get('error') or r['status']}" for r in failed])


def _alert(kind: str, message: str, section: dict) -> dict:
    return {"kind": kind, "message": message,
            "source_url": section["provenance"].get("source_url"),
            "asof": section["provenance"].get("asof")}


def weekly_check() -> dict:
    """The Sunday read over the latest snapshots (or live, when online)."""
    ctx = config.get("pretrade_context")
    today = snap.asof_date()
    reads = {
        "prompt_spreads": {r: futures.prompt_spread(r) for r in ("CL", "BZ")},
        "physical": [eia.weekly_series(s["series"], s["region"]) for s in ctx["eia_series"]],
        "cot": cot.positioning_summary(),
        "insiders": {s: edgar.insider_trades(s) for s in tracked_symbols()},
        "calendar": calendar.events_between(
            today, today + timedelta(days=CALENDAR_HORIZON_DAYS), tickers=tracked_symbols(),
            extra_events=[*futures.contract_events(ctx["curve_roots"]),
                          *etf_roll.roll_events(ctx["roll_funds"])]),
        "markets": polymarket.watchlist_markets(),
    }
    alerts = []
    for root, env in reads["prompt_spreads"].items():
        health = (env["data"] or {}).get("health")
        if health and health != "healthy":
            alerts.append(_alert("prompt_spread", f"{root} prompt spread is {health}", env))
    for row in (reads["cot"]["data"] or {}).get("rows", []):
        if row.get("stale"):
            alerts.append(_alert("cot_stale", f"{row['root']} COT print {row['report_date']} "
                                 "is stale", reads["cot"]))
    for sym, env in reads["insiders"].items():
        codes = (env["data"] or {}).get("count_by_code") or {}
        if codes:
            alerts.append(_alert("form4", f"{sym} Form 4 codes: {codes}", env))
    for m in (reads["markets"]["data"] or {}).get("markets", []):
        if m.get("alert"):
            alerts.append(_alert("polymarket", f"{m['label']}: {m['probability']} above "
                                 f"{m['alert_above']} ({m['question']})", reads["markets"]))
    unsourced = [line for env in _flatten(reads) for line in env["not_verified"]]
    return envelope({"asof_date": today.isoformat(), "alerts": alerts, "reads": reads},
                    prov=provenance(source="weekly_check",
                                    quality="snapshot" if snap.is_offline() else "live"),
                    not_verified=unsourced)


def _flatten(reads: dict) -> list[dict]:
    out = []
    for v in reads.values():
        if isinstance(v, dict) and "provenance" in v:
            out.append(v)
        elif isinstance(v, dict):
            out.extend(v.values())
        else:
            out.extend(v)
    return out
