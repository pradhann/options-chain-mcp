"""Energy futures curves month by month: settles, prompt spreads, cracks.

What: for CL (WTI), BZ (Brent), HO (NY Harbor ULSD), RB (RBOB) and NG
(Henry Hub) every listed month out to 24 months: contract code, expiry,
daily close; the M1-M2 / M1-M6 / M1-M12 spreads and a curve-shape label;
the prompt (M1-M2) spread with its 5- and 21-session change and, for the
crude roots, a health label against the user's thresholds; and the ULSD,
RBOB, 3-2-1 and Brent-WTI cracks with their 5- and 21-session changes.

Why per contract: every change is computed on the SAME contracts at both
dates (each contract's own daily history), never on a rolled continuous
series whose "change" would include a roll gap.

Source: yfinance month-coded symbols `{ROOT}{MONTH}{YY}.NYM` (e.g.
`CLX26.NYM`), one `yf.download` of every symbol for ~45 days of history,
plus `Ticker.info['expireIsoDate']` once per contract for the expiry
(cached in a JSON snapshot; expiries do not change). The daily "Close" is
labelled a settlement PROXY: it is not the official CME settlement, whose
page is carried in provenance (`official_settlement_url`).

Symbols are requested from the NEXT calendar month: the current month's
contracts had all expired or did not resolve (U26 on 2026-09-21).

Verified live on 2026-09-21 (27 symbols per root, V26 .. Z28):
  CL, HO, RB, NG  resolve for every month V26 .. Z28.
  BZ              does NOT resolve V26 (Brent front is X26) nor
                  H28, J28, K28, N28, Q28, U28, V28, X28. Those months are
                  returned as not_verified; nothing is interpolated.
  expireIsoDate   present for every resolved contract (CLX26 -> 2026-10-20,
                  CLV26 -> 2026-09-22, HOV26/RBV26 -> 2026-09-30,
                  NGV26 -> 2026-09-28, BZX26 -> 2026-10-01).
  First notice    not published by yfinance -> not_verified, with the CME
                  contract-spec page for the root (URLs below opened in a
                  browser on 2026-09-21; cmegroup.com returns 403 to scripts).
  yfinance names  BZ is "Brent Crude Oil Last Day Financ[ial]" (NYMEX BZ),
                  matching the CME Brent Last Day Financial spec page.

Units: CL, BZ in $/bbl; HO, RB in $/gal; NG in $/MMBtu. Cracks are $/bbl
(product x 42 gal/bbl). The prompt-spread thresholds in config
(`prompt_spread_thresholds`, $/bbl) are the user's crude thresholds and are
applied to CL and BZ only.

Snapshots (kind "futures"): `{root}/closes` (per-contract close history,
enough for 21-session changes), `{root}/curve` (the day's curve) and
`expiries/expiries` (JSON). Offline (OPTIONSLAB_OFFLINE=1) everything is
read from those snapshots and yfinance is never called.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd

from ..storage import snapshots as snap
from . import config
from .calendar import event
from .envelope import envelope, iso, now_utc, num, provenance, unsourced, unverified

SOURCE = "yfinance daily close (settlement proxy; not the official CME/ICE settlement)"
EXPIRY_SOURCE = "yfinance quoteSummary expireDate"
YAHOO_QUOTE_URL = "https://finance.yahoo.com/quote/{sym}/"

MONTH_CODES = "FGHJKMNQUVXZ"
MAX_MONTHS = 24
SYMBOL_MONTHS = 27          # from next month; covers BZ's later front + 24 months
HISTORY_PERIOD = "45d"      # >= 22 sessions for the 21-session change
FRESH_MINUTES = 15          # reuse today's download for this long
EXPIRY_WORKERS = 8
GAL_PER_BBL = 42
CHANGE_SESSIONS = (5, 21)
NEAR_EXPIRY_DAYS = 5
THRESHOLD_ROOTS = ("CL", "BZ")

SNAP_KIND = "futures"
EXPIRY_KEY = "expiries"

ANNUALIZE_NOTE = (
    "Do not annualize the front spread: M1-M2 is a single one-month calendar spread "
    "driven by prompt supply/demand and M1's delivery/expiry timing; multiplying it "
    "by 12 does not give a carry or roll rate.")

SHAPE_DEFINITION = (
    "Over the priced contracts in delivery order (unpriced months skipped, never "
    "interpolated): 'backwardation' if no step rises (each contract >= the next) and "
    "the first > the last; 'contango' if no step falls and the first < the last; "
    "otherwise 'mixed'.")

_SYMBOL = re.compile(r"^([A-Z]{2})([FGHJKMNQUVXZ])(\d{2})\.NYM$")


@dataclass(frozen=True)
class Root:
    code: str
    name: str
    unit: str
    specs_url: str
    settlements_url: str


_CME = "https://www.cmegroup.com/markets/energy/"
ROOTS: dict[str, Root] = {
    "CL": Root("CL", "WTI crude oil (NYMEX CL)", "$/bbl",
               _CME + "crude-oil/light-sweet-crude.contractSpecs.html",
               _CME + "crude-oil/light-sweet-crude.settlements.html"),
    "BZ": Root("BZ", "Brent crude oil last day financial (NYMEX BZ)", "$/bbl",
               _CME + "crude-oil/brent-crude-oil-last-day.contractSpecs.html",
               _CME + "crude-oil/brent-crude-oil-last-day.settlements.html"),
    "HO": Root("HO", "NY Harbor ULSD (NYMEX HO)", "$/gal",
               _CME + "refined-products/heating-oil.contractSpecs.html",
               _CME + "refined-products/heating-oil.settlements.html"),
    "RB": Root("RB", "RBOB gasoline (NYMEX RB)", "$/gal",
               _CME + "refined-products/rbob-gasoline.contractSpecs.html",
               _CME + "refined-products/rbob-gasoline.settlements.html"),
    "NG": Root("NG", "Henry Hub natural gas (NYMEX NG)", "$/MMBtu",
               _CME + "natural-gas/natural-gas.contractSpecs.html",
               _CME + "natural-gas/natural-gas.settlements.html"),
}


@dataclass(frozen=True)
class Crack:
    name: str
    formula: str
    weights: dict[str, float]   # root -> multiplier on its close; result in $/bbl


CRACKS = (
    Crack("ulsd_crack", "HO x 42 - CL", {"HO": GAL_PER_BBL, "CL": -1.0}),
    Crack("rbob_crack", "RB x 42 - CL", {"RB": GAL_PER_BBL, "CL": -1.0}),
    Crack("crack_321", "(2 x RB x 42 + HO x 42 - 3 x CL) / 3",
          {"RB": 2 * GAL_PER_BBL / 3, "HO": GAL_PER_BBL / 3, "CL": -1.0}),
    Crack("brent_wti", "BZ - CL", {"BZ": 1.0, "CL": -1.0}),
)


class DownloadError(RuntimeError):
    """yfinance produced no usable closes."""


@dataclass(frozen=True)
class Tape:
    """One root's per-contract daily closes and what was asked for."""

    root: str
    closes: pd.DataFrame        # columns: date (YYYY-MM-DD), symbol, close
    requested: list[str]        # symbols asked of yfinance, delivery order
    expiries: dict[str, str]    # symbol -> YYYY-MM-DD
    fetched_at: str
    from_snapshot: bool
    note: str | None = None

    def wide(self) -> pd.DataFrame:
        return to_wide(self.closes)


@dataclass(frozen=True)
class Curve:
    rows: list[dict]
    window: list[str]           # symbols M1..Mn, calendar-consecutive
    curve_date: str
    warnings: list[str]
    not_verified: list[dict]


# ---------- symbols (pure) ----------

def month_symbols(root: str, start: date, n: int) -> list[str]:
    """`n` calendar-consecutive month symbols for `root` from start's month."""
    out = []
    y, m = start.year, start.month
    for _ in range(n):
        out.append(f"{root}{MONTH_CODES[m - 1]}{y % 100:02d}.NYM")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def parse_symbol(sym: str) -> tuple[str, int, int]:
    """'CLX26.NYM' -> ('CL', 2026, 11)."""
    m = _SYMBOL.match(sym)
    if not m:
        raise ValueError(f"not a month-coded futures symbol: {sym!r}")
    return m.group(1), 2000 + int(m.group(3)), MONTH_CODES.index(m.group(2)) + 1


def contract_code(sym: str) -> str:
    return sym.split(".")[0]


def delivery_month(sym: str) -> str:
    _, y, m = parse_symbol(sym)
    return f"{y:04d}-{m:02d}"


def parse_expiry(info: dict | None) -> str | None:
    """YYYY-MM-DD from yfinance `info['expireIsoDate']`, else None."""
    v = (info or {}).get("expireIsoDate")
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", v) if isinstance(v, str) else None
    return m.group(1) if m else None


# ---------- frames (pure) ----------

def closes_long(frame: pd.DataFrame | None) -> pd.DataFrame:
    """A `yf.download` frame -> long [date, symbol, close], missing rows dropped."""
    cols = ["date", "symbol", "close"]
    if frame is None or frame.empty or "Close" not in frame.columns.get_level_values(0):
        return pd.DataFrame(columns=cols)
    close = frame["Close"].copy()
    close.index = [pd.Timestamp(d).date().isoformat() for d in close.index]
    long = close.rename_axis("date").reset_index().melt(
        id_vars="date", var_name="symbol", value_name="close")
    return long.dropna(subset=["close"])[cols].reset_index(drop=True)


def to_wide(closes: pd.DataFrame) -> pd.DataFrame:
    """Long closes -> date x symbol, dates ascending (ISO strings)."""
    if closes.empty:
        return pd.DataFrame()
    return closes.pivot_table(index="date", columns="symbol", values="close",
                              aggfunc="last").sort_index()


def combine(wide: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """sum(weight x close) on the dates where every leg has a close."""
    legs = wide[list(weights)].dropna()
    return sum(legs[s] * w for s, w in weights.items())


def session_changes(series: pd.Series, sessions: list[str],
                    ns: Iterable[int] = CHANGE_SESSIONS) -> dict[int, dict]:
    """Change of the latest value vs n sessions earlier on the session calendar.

    {n: {"change", "from_date", "reason"}}; change is None (with a reason) when
    history is too short or a leg has no close on that session. The legs are
    whatever `series` was built from, so both ends use the same contracts.
    """
    out: dict[int, dict] = {}
    if series.empty:
        return {n: {"change": None, "from_date": None, "reason": "no value"} for n in ns}
    last_day = series.index[-1]
    pos = sessions.index(last_day)
    for n in ns:
        if pos - n < 0:
            out[n] = {"change": None, "from_date": None,
                      "reason": f"history holds {pos} earlier sessions, fewer than {n}"}
            continue
        then = sessions[pos - n]
        prior = num(series.get(then))
        out[n] = {"change": None if prior is None else float(series.iloc[-1]) - prior,
                  "from_date": then,
                  "reason": None if prior is not None else f"a leg has no close on {then}"}
    return out


def curve_shape(settles: list[float | None]) -> dict:
    """Label the curve; see SHAPE_DEFINITION."""
    px = [p for p in settles if p is not None]
    steps = [b - a for a, b in zip(px, px[1:], strict=False)]
    down = sum(s < 0 for s in steps)
    up = sum(s > 0 for s in steps)
    if len(px) < 2:
        label = None
    elif up == 0 and px[0] > px[-1]:
        label = "backwardation"
    elif down == 0 and px[0] < px[-1]:
        label = "contango"
    else:
        label = "mixed"
    return {"label": label, "definition": SHAPE_DEFINITION, "priced_contracts": len(px),
            "steps_down": down, "steps_up": up, "steps_flat": len(steps) - down - up}


def health_label(spread: float | None, thresholds: dict) -> str | None:
    """'healthy' above `healthy`, 'thinning' in [thinning, healthy], else 'flat_case_gone'."""
    if spread is None:
        return None
    if spread > thresholds["healthy"]:
        return "healthy"
    if spread >= thresholds["thinning"]:
        return "thinning"
    return "flat_case_gone"


def build_curve(tape: Tape, asof: date, months: int) -> Curve | None:
    """The curve from `tape` on its latest complete session; None if nothing priced."""
    wide = tape.wide()
    root = ROOTS[tape.root]

    live = [s for s in tape.requested if not _expired(tape, s, asof)]
    priced = [s for s in live if s in wide.columns]
    if not priced:
        return None
    front = priced[0]
    _, fy, fm = parse_symbol(front)
    window = month_symbols(tape.root, date(fy, fm, 1), months)
    in_window = [s for s in window if s in wide.columns]

    warnings: list[str] = []
    before_front = set(live[:live.index(front)])
    unverified_lines = [unverified(f"{s} settle", "no yfinance data: symbol did not resolve",
              YAHOO_QUOTE_URL.format(sym=s))
           for s in live if s not in wide.columns and (s in window or s in before_front)]
    complete = wide[in_window].dropna()
    if complete.empty:
        day = wide[front].dropna().index[-1]
        warnings.append(f"no session has a close for every priced contract; curve uses "
                        f"{day}, the latest close of {contract_code(front)}")
    else:
        day = complete.index[-1]
    newest = wide.index[-1]
    if newest > day:
        n_new = int(wide.loc[newest, in_window].notna().sum())
        warnings.append(f"yfinance has a partial row for {newest} ({n_new}/{len(in_window)} "
                        f"contracts); curve uses {day}, the latest session with a close "
                        f"for every priced contract")

    rows = []
    for k, sym in enumerate(window, 1):
        settle = num(wide.at[day, sym]) if sym in wide.columns else None
        if sym in wide.columns and settle is None:
            unverified_lines.append(unverified(f"{sym} settle", f"no yfinance close on {day}",
                          YAHOO_QUOTE_URL.format(sym=sym)))
        exp = tape.expiries.get(sym)
        if sym in wide.columns and exp is None:
            unverified_lines.append(unverified(f"{sym} expiry", "yfinance info has no expireIsoDate",
                          YAHOO_QUOTE_URL.format(sym=sym)))
        rows.append({
            "position": f"M{k}", "contract": contract_code(sym), "symbol": sym,
            "delivery_month": delivery_month(sym), "settle": settle, "settle_date": day,
            "unit": root.unit, "expiry": exp,
            "expiry_source": EXPIRY_SOURCE if exp else None,
            "days_to_expiry": (date.fromisoformat(exp) - asof).days if exp else None,
            "first_notice": None,
        })
    unverified_lines.append(unverified(f"{tape.root} first notice dates",
                  "first notice date is not published by yfinance; read it from the "
                  "CME contract specs / calendar", root.specs_url))
    return Curve(rows, window, day, warnings, unverified_lines)


# ---------- market data (yfinance + snapshots) ----------

def _download(symbols: list[str]) -> pd.DataFrame:
    import yfinance as yf

    try:
        frame = yf.download(symbols, period=HISTORY_PERIOD, interval="1d",
                            auto_adjust=False, progress=False, group_by="column",
                            threads=True)
    except Exception as e:  # yfinance raises assorted transport/parse errors
        raise DownloadError(str(e)) from e
    closes = closes_long(frame)
    if closes.empty:
        raise DownloadError("yfinance returned no closes")
    return closes


def _fetch_expiry(sym: str) -> tuple[str, str | None]:
    import yfinance as yf

    try:
        return sym, parse_expiry(yf.Ticker(sym).info)
    except Exception:  # an unreadable info block leaves the expiry not_verified
        return sym, None


def _cached_expiries() -> dict[str, str]:
    hit = snap.read_json(SNAP_KIND, EXPIRY_KEY, "expiries")
    return dict(hit[0]) if hit else {}


def _update_expiries(symbols: Iterable[str]) -> dict[str, str]:
    """Expiry cache, fetching only symbols never seen before."""
    cache = _cached_expiries()
    missing = sorted(set(symbols) - set(cache))
    if missing:
        with ThreadPoolExecutor(EXPIRY_WORKERS) as pool:
            cache.update({s: e for s, e in pool.map(_fetch_expiry, missing) if e})
        snap.write_json(SNAP_KIND, EXPIRY_KEY, "expiries", cache)
    return cache


def _record_all() -> dict[str, Tape]:
    """Download every root at once, snapshot per-root closes, return live tapes."""
    next_month = (snap.today_et().replace(day=1) + timedelta(days=32)).replace(day=1)
    requested = {r: month_symbols(r, next_month, SYMBOL_MONTHS) for r in ROOTS}
    closes = _download([s for syms in requested.values() for s in syms])
    expiries = _update_expiries(closes["symbol"].unique())
    fetched_at = iso(now_utc())
    tapes = {}
    for r, syms in requested.items():
        df = closes[closes["symbol"].isin(syms)].reset_index(drop=True)
        snap.write_table(SNAP_KIND, r, "closes", df,
                         meta={"fetched_at": fetched_at, "requested": syms, "source": SOURCE})
        tapes[r] = Tape(r, df, syms, expiries, fetched_at, from_snapshot=False)
    return tapes


def _read_tape(root: str, note: str | None = None) -> Tape | None:
    hit = snap.read_table(SNAP_KIND, root, "closes")
    if hit is None:
        return None
    df, meta, day = hit
    return Tape(root, df[["date", "symbol", "close"]].astype({"date": str}),
                list(meta.get("requested") or sorted(df["symbol"].unique())),
                _cached_expiries(), meta.get("fetched_at", day.isoformat()),
                from_snapshot=True, note=note)


def _is_fresh(root: str) -> bool:
    hit = snap.read_table(SNAP_KIND, root, "closes", day=snap.today_et())
    if hit is None or "fetched_at" not in hit[1]:
        return False
    age = now_utc() - datetime.fromisoformat(hit[1]["fetched_at"])
    return age <= timedelta(minutes=FRESH_MINUTES)


def _tapes(roots: Iterable[str]) -> tuple[dict[str, Tape], list[dict]]:
    """Tapes for `roots`; not_verified lines for roots with no data at all."""
    roots = [_root(r).code for r in roots]
    live: dict[str, Tape] = {}
    note = None
    if not snap.is_offline() and not all(_is_fresh(r) for r in roots):
        try:
            live = _record_all()
        except DownloadError as e:
            note = f"live yfinance read failed ({e}); served the latest snapshot"
    tapes, unverified_lines = {}, []
    for r in roots:
        tape = live.get(r) or _read_tape(r, note)
        if tape is None:
            unverified_lines.append(unverified(f"{r} futures closes", "no yfinance data and no snapshot",
                          ROOTS[r].settlements_url))
        else:
            tapes[r] = tape
    return tapes, unverified_lines


def _root(code: str) -> Root:
    try:
        return ROOTS[code.upper()]
    except KeyError:
        raise ValueError(f"unknown root {code!r}; expected one of {sorted(ROOTS)}") from None


def _prov(tapes: Iterable[Tape], source_url: str, **extra) -> dict:
    tapes = list(tapes)
    return provenance(
        source=SOURCE, source_url=source_url,
        asof=max(t.fetched_at for t in tapes), session="daily_close",
        quality="snapshot" if any(t.from_snapshot for t in tapes) else "last_only",
        **extra)


def _tape_warnings(tapes: Iterable[Tape]) -> list[str]:
    return sorted({t.note for t in tapes if t.note})


def _change_fields(changes: dict[int, dict], item: str) -> tuple[dict, list[dict]]:
    fields, unverified_lines = {}, []
    for n, c in changes.items():
        fields[f"change_{n}"] = c["change"]
        fields[f"change_{n}_from_date"] = c["from_date"]
        if c["change"] is None:
            unverified_lines.append(unverified(f"{item} {n}-session change", c["reason"]))
    return fields, unverified_lines


# ---------- public API ----------

def curve(root: str, months: int = MAX_MONTHS) -> dict:
    """Month-by-month curve for `root` (CL, BZ, HO, RB, NG), M1..M`months`.

    data: {root, name, unit, curve_date, months: [{position, contract, symbol,
    delivery_month, settle, settle_date, unit, expiry, expiry_source,
    days_to_expiry, first_notice}], spreads: {"M1-M2"|"M1-M6"|"M1-M12":
    {value, legs}}, shape: {label, definition, ...}}.
    M_k is the k-th calendar delivery month from the first priced unexpired
    contract; an unresolved month keeps its slot with settle None.
    """
    spec = _root(root)
    if not 1 <= months <= MAX_MONTHS:
        raise ValueError(f"months must be 1..{MAX_MONTHS}")
    tapes, unverified_lines = _tapes([spec.code])
    tape = tapes.get(spec.code)
    built = build_curve(tape, snap.asof_date(), months) if tape else None
    if built is None:
        return unsourced(f"{spec.code} curve", "no priced unexpired contract",
                      spec.settlements_url, source=SOURCE)
    unverified_lines += built.not_verified
    spreads = {}
    for k in (2, 6, 12):
        if k > len(built.rows):
            continue
        a, b = built.rows[0], built.rows[k - 1]
        value = (a["settle"] - b["settle"]
                 if a["settle"] is not None and b["settle"] is not None else None)
        spreads[f"M1-M{k}"] = {"value": value, "legs": [a["contract"], b["contract"]]}
        if value is None:
            unverified_lines.append(unverified(f"{spec.code} M1-M{k}", "a leg has no settle on the curve date"))
    if not tape.from_snapshot:
        snap.write_table(SNAP_KIND, spec.code, "curve", pd.DataFrame(built.rows),
                         meta={"fetched_at": tape.fetched_at, "source": SOURCE,
                               "curve_date": built.curve_date})
    data = {"root": spec.code, "name": spec.name, "unit": spec.unit,
            "curve_date": built.curve_date, "months": built.rows, "spreads": spreads,
            "shape": curve_shape([r["settle"] for r in built.rows])}
    prov = _prov([tape], YAHOO_QUOTE_URL.format(sym=built.window[0]),
                 official_settlement_url=spec.settlements_url,
                 contract_specs_url=spec.specs_url)
    return envelope(data, prov=prov, warnings=_tape_warnings([tape]) + built.warnings,
                    not_verified=unverified_lines)


def prompt_spread(root: str) -> dict:
    """M1-M2 for `root`, its 5- and 21-session change, and health (CL, BZ only).

    data: {root, unit, front, second, spread, spread_date, health, thresholds,
    thresholds_note, change_5, change_5_from_date, change_21,
    change_21_from_date, m1_expiry, m1_days_to_expiry, annualize_note}.
    Both ends of each change use the same two contracts.
    """
    spec = _root(root)
    tapes, unverified_lines = _tapes([spec.code])
    tape = tapes.get(spec.code)
    built = build_curve(tape, snap.asof_date(), 2) if tape else None
    if built is None:
        return unsourced(f"{spec.code} prompt spread", "no priced unexpired contract",
                      spec.settlements_url, source=SOURCE)
    m1, m2 = built.window
    wide = tape.wide()
    if m2 not in wide.columns:
        return unsourced(f"{spec.code} prompt spread", f"{m2} has no yfinance data",
                      YAHOO_QUOTE_URL.format(sym=m2), source=SOURCE)
    series = combine(wide, {m1: 1.0, m2: -1.0})
    if series.empty:
        return unsourced(f"{spec.code} prompt spread", f"no session with closes for both "
                      f"{contract_code(m1)} and {contract_code(m2)}", spec.settlements_url,
                      source=SOURCE)
    spread = float(series.iloc[-1])
    changes, change_nvs = _change_fields(session_changes(series, list(wide.index)),
                                         f"{spec.code} prompt spread")
    warnings = _tape_warnings([tape])
    m1_row = built.rows[0]
    if m1_row["days_to_expiry"] is not None and m1_row["days_to_expiry"] <= NEAR_EXPIRY_DAYS:
        warnings.append(f"M1 {m1_row['contract']} expires {m1_row['expiry']}: the prompt "
                        f"spread is set by an expiring contract")
    if spec.code in THRESHOLD_ROOTS:
        thresholds = config.get("prompt_spread_thresholds")
        health = health_label(spread, thresholds)
        thresholds_note = "user thresholds in $/bbl from config prompt_spread_thresholds"
    else:
        thresholds, health = None, None
        thresholds_note = (f"not applied: prompt_spread_thresholds are the user's crude "
                           f"thresholds in $/bbl; {spec.code} is quoted in {spec.unit}")
    data = {"root": spec.code, "unit": spec.unit,
            "front": contract_code(m1), "second": contract_code(m2),
            "spread": spread, "spread_date": series.index[-1],
            "definition": "M1 close - M2 close, same two contracts at every date",
            "health": health, "thresholds": thresholds, "thresholds_note": thresholds_note,
            **changes,
            "m1_expiry": m1_row["expiry"], "m1_days_to_expiry": m1_row["days_to_expiry"],
            "annualize_note": ANNUALIZE_NOTE}
    prov = _prov([tape], YAHOO_QUOTE_URL.format(sym=m1),
                 official_settlement_url=spec.settlements_url)
    return envelope(data, prov=prov, warnings=warnings,
                    not_verified=unverified_lines + [n for n in built.not_verified
                                        if n["item"].startswith((m1, m2))] + change_nvs)


def _matching_month(tapes: dict[str, Tape], roots: Iterable[str], asof: date) -> str | None:
    """Earliest delivery month (e.g. 'X26') priced and unexpired for every root."""
    roots = list(roots)
    wides = {r: tapes[r].wide() for r in roots}
    for sym in tapes[roots[0]].requested:
        suffix = sym[2:]
        legs = {r: f"{r}{suffix}" for r in roots}
        if all(legs[r] in wides[r].columns and not _expired(tapes[r], legs[r], asof)
               for r in roots):
            return suffix
    return None


def _expired(tape: Tape, sym: str, asof: date) -> bool:
    exp = tape.expiries.get(sym)
    return exp is not None and date.fromisoformat(exp) < asof


def _crack_series(c: Crack, tapes: dict[str, Tape], asof: date
                  ) -> tuple[pd.Series, pd.DataFrame, dict[str, float]] | tuple[None, str, None]:
    """(daily crack series, wide closes, legs) on the matching month, or (None, reason, None)."""
    suffix = _matching_month(tapes, c.weights, asof)
    if suffix is None:
        return None, "no delivery month is priced for every leg", None
    legs = {f"{r}{suffix}": w for r, w in c.weights.items()}
    wide = to_wide(pd.concat([tapes[r].closes for r in c.weights]))
    series = combine(wide, legs)
    if series.empty:
        return None, f"no session with a close for every {suffix} leg", None
    return series, wide, legs


def _crack(c: Crack, tapes: dict[str, Tape], asof: date) -> tuple[dict | None, list[dict]]:
    series, wide, legs = _crack_series(c, tapes, asof)
    if series is None:
        return None, [unverified(c.name, wide)]
    day = series.index[-1]
    changes, unverified_lines = _change_fields(session_changes(series, list(wide.index)), c.name)
    leg_rows = [{"root": sym[:2], "contract": contract_code(sym), "symbol": sym,
                 "close": num(wide.at[day, sym]), "expiry": tapes[sym[:2]].expiries.get(sym)}
                for sym in legs]
    return {"name": c.name, "formula": c.formula, "unit": "$/bbl",
            "value": float(series.iloc[-1]), "value_date": day,
            "delivery_month": delivery_month(next(iter(legs))),
            "month_basis": "matching delivery month for every leg (nearest month "
                           "priced and unexpired for all legs)",
            "legs": leg_rows, **changes}, unverified_lines


def cracks() -> dict:
    """ULSD, RBOB, 3-2-1 cracks and Brent-WTI, each on one matching delivery month.

    data: {"cracks": [{name, formula, unit, value, value_date, delivery_month,
    month_basis, legs: [{root, contract, symbol, close, expiry}], change_5,
    change_5_from_date, change_21, change_21_from_date}]}.
    A crack with no month common to all its legs is not_verified, never mixed.
    """
    tapes, unverified_lines = _tapes(["CL", "BZ", "HO", "RB"])
    asof = snap.asof_date()
    out = []
    for c in CRACKS:
        if not all(r in tapes for r in c.weights):
            unverified_lines.append(unverified(c.name, "a leg's futures closes are unavailable"))
            continue
        row, row_nvs = _crack(c, tapes, asof)
        unverified_lines += row_nvs
        if row:
            out.append(row)
    if not tapes:
        return unsourced("cracks", "no futures closes", ROOTS["CL"].settlements_url, source=SOURCE)
    prov = _prov(tapes.values(), YAHOO_QUOTE_URL.format(sym="CL=F"),
                 official_settlement_urls={r: ROOTS[r].settlements_url for r in tapes})
    return envelope({"cracks": out}, prov=prov, warnings=_tape_warnings(tapes.values()),
                    not_verified=unverified_lines)


def crack_history() -> dict:
    """Daily history of each crack on its matching delivery month (the chart series).

    data: {"cracks": [{name, formula, unit, delivery_month, points: [{date, value}]}]}.
    The legs are the same contracts on every date, as in `cracks()`.
    """
    tapes, unverified_lines = _tapes(["CL", "BZ", "HO", "RB"])
    if not tapes:
        return unsourced("crack history", "no futures closes", ROOTS["CL"].settlements_url,
                      source=SOURCE)
    out = []
    for c in CRACKS:
        if not all(r in tapes for r in c.weights):
            unverified_lines.append(unverified(c.name, "a leg's futures closes are unavailable"))
            continue
        series, reason, legs = _crack_series(c, tapes, snap.asof_date())
        if series is None:
            unverified_lines.append(unverified(c.name, reason))
            continue
        out.append({"name": c.name, "formula": c.formula, "unit": "$/bbl",
                    "delivery_month": delivery_month(next(iter(legs))),
                    "points": [{"date": d, "value": float(v)} for d, v in series.items()]})
    prov = _prov(tapes.values(), YAHOO_QUOTE_URL.format(sym="CL=F"))
    return envelope({"cracks": out}, prov=prov, warnings=_tape_warnings(tapes.values()),
                    not_verified=unverified_lines)


def curve_overlay(root: str, sessions_back: tuple[int, ...] = CHANGE_SESSIONS,
                  months: int = 12) -> dict:
    """Today's curve and the same contracts n sessions earlier (the overlay chart).

    data: {root, unit, contracts: [...], curves: [{label, date, settles: [...]}]}.
    Earlier curves reuse today's contracts, so the overlay shows how those
    contracts moved, not a rolled continuous curve.
    """
    spec = _root(root)
    tapes, unverified_lines = _tapes([spec.code])
    tape = tapes.get(spec.code)
    built = build_curve(tape, snap.asof_date(), months) if tape else None
    if built is None:
        return unsourced(f"{spec.code} curve overlay", "no priced unexpired contract",
                      spec.settlements_url, source=SOURCE)
    wide = tape.wide()
    sessions = list(wide.index)
    pos = sessions.index(built.curve_date)
    syms = [r["symbol"] for r in built.rows]
    curves = [{"label": "latest", "date": built.curve_date,
               "settles": [r["settle"] for r in built.rows]}]
    for n in sessions_back:
        if pos - n < 0:
            unverified_lines.append(unverified(f"{spec.code} curve {n} sessions back",
                          f"history holds {pos} earlier sessions"))
            continue
        day = sessions[pos - n]
        curves.append({"label": f"{n} sessions earlier", "date": day,
                       "settles": [num(wide.at[day, s]) if s in wide.columns else None
                                   for s in syms]})
    data = {"root": spec.code, "name": spec.name, "unit": spec.unit,
            "contracts": [r["contract"] for r in built.rows], "curves": curves}
    return envelope(data, prov=_prov([tape], spec.settlements_url),
                    warnings=_tape_warnings([tape]) + built.warnings,
                    not_verified=unverified_lines + built.not_verified)


def contract_events(roots: Iterable[str]) -> list[dict]:
    """Expiry events for each root's listed months, in the calendar event shape.

    First-notice events are absent: yfinance does not publish them, and
    `curve()` lists them in not_verified with the CME spec URL.
    """
    specs = [_root(r) for r in roots]
    tapes, _ = _tapes(s.code for s in specs)
    events = []
    for spec in specs:
        tape = tapes.get(spec.code)
        built = build_curve(tape, snap.asof_date(), MAX_MONTHS) if tape else None
        for row in built.rows if built else []:
            if row["expiry"] is None:
                continue
            events.append(event(
                row["expiry"], f"{row['contract']} last trade (expiry)",
                source_url=YAHOO_QUOTE_URL.format(sym=row["symbol"]), verified=True,
                fetched_at=tape.fetched_at,
                notes=f"{EXPIRY_SOURCE}; {spec.name}; contract specs {spec.specs_url}"))
    return sorted(events, key=lambda e: (e["date"], e["event"]))
