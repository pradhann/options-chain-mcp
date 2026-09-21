"""Item 2: history built from our own daily chain snapshots.

No free provider returns historical IV or open interest, so every series
here exists only for the dates we recorded (snapshots/chains/{SYMBOL}/).
Each series reports how many snapshot dates it rests on.

  atm_iv_history   ATM IV per snapshot date for one expiry: mean of call and
                   put mid-IV at the strike nearest that day's feed spot
  skew_history     25-delta risk reversal and butterfly per date (vol points),
                   IV interpolated linearly in delta space; legacy rows
                   migrated from .optionslab/history/ are kept, labeled
  oi_history       open interest by strike over time (date x strike)
  oi_change        per-strike OI change day-over-day and week-over-week,
                   naming the two snapshot dates compared
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from ..pricing import bs_greeks, year_fraction
from ..storage import snapshots as snap
from .chains import derive_quote_fields
from .envelope import ET, envelope, num, provenance, unsourced

SOURCE = "own daily chain snapshots (yfinance quotes)"
LEGACY_DIR = "history"
LEGACY_KIND = "skew_legacy"
_LEGACY_NAME = re.compile(r"^skew_(?P<sym>[A-Z0-9^.]+)_(?P<exp>\d{4}-\d{2}-\d{2})_(?P<kind>RR|BF)\.csv$")


def _day_inputs(frame: pd.DataFrame, meta: dict, expiration: str):
    """Rows with derived IVs for one snapshot day, plus spot, T, r, q."""
    spot = (meta["quote"].get("data") or {}).get("spot")
    r = (meta["rate"].get("data") or {}).get("r")
    if spot is None or r is None:
        return None
    q = meta.get("trailing_dividend_yield") or 0.0
    fetched = datetime.fromisoformat(meta["fetched_at"]).astimezone(ET).replace(tzinfo=None)
    T = year_fraction(expiration, now=fetched)
    if T <= 0:
        return None
    return derive_quote_fields(frame, spot, T, r, q), spot, T, r, q


def _atm_iv(rows: pd.DataFrame, spot: float) -> float | None:
    ivs = []
    for side in ("call", "put"):
        s = rows[(rows["type"] == side) & rows["iv_mid_pct"].notna()]
        if not s.empty:
            ivs.append(float(s.loc[(s["strike"] - spot).abs().idxmin(), "iv_mid_pct"]))
    return round(sum(ivs) / len(ivs), 2) if ivs else None


def _iv_at_delta(rows: pd.DataFrame, side: str, target: float,
                 spot: float, T: float, r: float, q: float) -> float | None:
    """Mid-IV at a target delta, linear in delta between bracketing strikes."""
    s = rows[(rows["type"] == side) & rows["iv_mid_pct"].notna()].sort_values("strike")
    if len(s) < 2:
        return None
    sigma = s["iv_mid_pct"].to_numpy() / 100
    deltas = bs_greeks(np.full(len(s), spot), s["strike"].to_numpy(), T, r, sigma, side, q)["delta"]
    order = np.argsort(deltas)
    d, iv = deltas[order], s["iv_mid_pct"].to_numpy()[order]
    if not d[0] <= target <= d[-1]:
        return None
    return round(float(np.interp(target, d, iv)), 3)


def _skew(rows, spot, T, r, q) -> dict:
    atm = _atm_iv(rows, spot)
    c25 = _iv_at_delta(rows, "call", 0.25, spot, T, r, q)
    p25 = _iv_at_delta(rows, "put", -0.25, spot, T, r, q)
    rr = round(c25 - p25, 3) if c25 is not None and p25 is not None else None
    bf = round((c25 + p25) / 2 - atm, 3) if rr is not None and atm is not None else None
    return {"atm_iv_pct": atm, "iv_25d_call_pct": c25, "iv_25d_put_pct": p25,
            "rr_25d_pts": rr, "bf_25d_pts": bf}


def _history_envelope(symbol: str, expiration: str, records: list[dict], item: str) -> dict:
    if not records:
        return unsourced(f"{symbol} {expiration} {item}", "no chain snapshots recorded",
                      source=SOURCE)
    prov = provenance(source=SOURCE, quality="snapshot",
                      first_date=records[0]["date"], last_date=records[-1]["date"],
                      snapshot_dates=len(records))
    return envelope(records, prov=prov)


def atm_iv_history(symbol: str, expiration: str) -> dict:
    """ATM mid-IV (percent) for one expiry on every snapshot date."""
    symbol = symbol.upper()
    out = []
    for day, frame, meta in snap.history("chains", symbol, expiration):
        inputs = _day_inputs(frame, meta, expiration)
        if inputs:
            rows, spot, *_ = inputs
            out.append({"date": day.isoformat(), "spot": spot, "atm_iv_pct": _atm_iv(rows, spot)})
    return _history_envelope(symbol, expiration, out, "ATM IV history")


def skew_history(symbol: str, expiration: str) -> dict:
    """25-delta RR/BF per snapshot date, plus migrated legacy rows."""
    symbol = symbol.upper()
    out = [{"date": d, **vals, "method": "legacy skew_metrics (pre-0.2)"}
           for d, vals in _legacy_rows(symbol, expiration)]
    for day, frame, meta in snap.history("chains", symbol, expiration):
        inputs = _day_inputs(frame, meta, expiration)
        if inputs:
            out.append({"date": day.isoformat(), **_skew(*inputs),
                        "method": "delta-space interpolation of mid-IV"})
    out.sort(key=lambda r: r["date"])
    return _history_envelope(symbol, expiration, out, "skew history")


def oi_history(symbol: str, expiration: str, side: str = "call") -> dict:
    """Open interest by strike for each snapshot date (one side)."""
    symbol = symbol.upper()
    out = []
    for day, frame, _ in snap.history("chains", symbol, expiration):
        s = frame[frame["type"] == side].dropna(subset=["oi"])
        out.append({"date": day.isoformat(),
                    "oi_by_strike": {float(k): int(v) for k, v in zip(s["strike"], s["oi"], strict=True)}})
    return _history_envelope(symbol, expiration, out, f"{side} OI history")


def _oi_frame(frame: pd.DataFrame) -> pd.Series:
    f = frame.dropna(subset=["oi"])
    return f.set_index(["type", "strike"])["oi"].astype(int)


def _change(latest: pd.Series, prior: pd.Series, top: int) -> list[dict]:
    joined = pd.concat([latest.rename("now"), prior.rename("then")], axis=1).dropna()
    joined["change"] = joined["now"] - joined["then"]
    joined = joined.reindex(joined["change"].abs().sort_values(ascending=False).index).head(top)
    return [{"type": t, "strike": k, "oi_then": int(r.then), "oi_now": int(r.now),
             "change": int(r.change)} for (t, k), r in joined.iterrows()]


def oi_change(symbol: str, expiration: str, top: int = 10) -> dict:
    """Largest per-strike OI changes vs the previous snapshot and ~1 week ago.

    Week-over-week compares with the latest snapshot at least 7 calendar
    days older than the newest one.
    """
    symbol = symbol.upper()
    hist = snap.history("chains", symbol, expiration)
    if len(hist) < 2:
        return unsourced(f"{symbol} {expiration} OI change",
                      f"{len(hist)} snapshot(s); need two dates", source=SOURCE)
    newest_day, newest, _ = hist[-1]
    latest = _oi_frame(newest)

    def compare(prior_day: date, prior_frame: pd.DataFrame) -> dict:
        return {"from": prior_day.isoformat(), "to": newest_day.isoformat(),
                "largest_changes": _change(latest, _oi_frame(prior_frame), top)}

    prev_day, prev, _ = hist[-2]
    week = [(d, f) for d, f, _ in hist if d <= newest_day - timedelta(days=7)]
    data = {"day_over_day": compare(prev_day, prev),
            "week_over_week": compare(*week[-1]) if week else None}
    warnings = [] if week else ["no snapshot 7+ days older than the newest; week-over-week unavailable"]
    prov = provenance(source=SOURCE, quality="snapshot", snapshot_dates=len(hist))
    return envelope(data, prov=prov, warnings=warnings)


# ---------- legacy migration ----------

def migrate_legacy_skew() -> dict:
    """Copy `.optionslab/history/skew_*_{RR,BF}.csv` into the snapshot store.

    Idempotent: rows land in snapshots/skew_legacy/{SYMBOL}/{date}/{expiry}.csv,
    one file per recording date. The legacy files are left in place.
    """
    legacy = snap.home() / LEGACY_DIR
    series: dict[tuple[str, str], dict[str, pd.Series]] = {}
    for path in sorted(legacy.glob("skew_*.csv")) if legacy.is_dir() else []:
        m = _LEGACY_NAME.match(path.name)
        if not m:
            continue
        df = pd.read_csv(path, parse_dates=["timestamp"])
        series.setdefault((m["sym"], m["exp"]), {})[m["kind"]] = df.set_index("timestamp")["value"]

    written = 0
    for (sym, exp), kinds in series.items():
        both = pd.DataFrame({"rr_25d_pts": kinds.get("RR"), "bf_25d_pts": kinds.get("BF")})
        for day, rows in both.groupby(both.index.date):
            last = rows.iloc[[-1]].reset_index(names="timestamp")
            snap.write_table(LEGACY_KIND, sym, exp, last, day=day)
            written += 1
    return envelope({"series": len(series), "dated_rows_written": written},
                    prov=provenance(source=f"{legacy}", quality="snapshot"))


def _legacy_rows(symbol: str, expiration: str) -> list[tuple[str, dict]]:
    out = []
    for day, frame, _ in snap.history(LEGACY_KIND, symbol, expiration):
        rec = frame.iloc[-1]
        out.append((day.isoformat(), {"rr_25d_pts": num(rec.get("rr_25d_pts")),
                                      "bf_25d_pts": num(rec.get("bf_25d_pts"))}))
    return out
