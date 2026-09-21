"""Item 1: option chains with quote quality, parity spot, usability, and walls.

Ingest (`snapshot`) stores the provider's raw quotes per expiry; every
derived field is computed on read (`chain_report`), so a live read and an
offline replay of the same snapshot produce identical output.

Per strike (derived):
  mid                  (bid + ask) / 2 from a two-sided quote only. A zero or
                       missing bid or ask is None, never 0 (after-hours zeros
                       are not prices). Last is shown, never used for pricing.
  iv_mid/bid/ask_pct   Black-Scholes IV solved from mid, bid, ask (percent)
  last_outside_bidask  Last print lies outside the current bid/ask
  spread_pct           (ask - bid) / mid, percent
  vol_oi               volume / open interest (None when OI is 0)

Per expiry (derived):
  spot_check   feed spot vs CBOE delayed spot vs parity-implied spot; the
               FEED_SUSPECT flag when any pair disagrees by more than 1%.
               Parity spot is the median over the three strikes nearest the
               feed spot of S = (C - P + K e^-rT) e^qT (European parity; the
               near-the-money strikes minimize American early-exercise bias).
  usability    over strikes within 80-120% of spot: median spread_pct and the
               share of strikes with OI >= 500; verdict usable / thin /
               unusable against config["usability"] thresholds.
  walls        top three strikes by OI per side, with percent of side OI.

Sources: yfinance `Ticker.option_chain` (quotes), yfinance `info` (spot),
CBOE delayed quotes JSON (cross-check spot):
https://cdn.cboe.com/api/global/delayed_quotes/options/{SYMBOL}.json
"""

from __future__ import annotations

import math
from datetime import date, datetime

import numpy as np
import pandas as pd

from ..pricing import implied_vol, year_fraction
from ..storage import snapshots as snap
from . import config, http
from .envelope import ET, envelope, num, provenance, unsourced, unverified
from .fundamentals import quote_summary, risk_free_rate, spot_quote

SOURCE = "yfinance Ticker.option_chain"
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json"
FEED_SUSPECT_PCT = 1.0
PARITY_STRIKES = 3
WALLS_PER_SIDE = 3
LIQUID_OI = 500
NEAR_MONEY_BAND = (0.8, 1.2)
LISTING = "_listed"           # per-day document: the provider's listed expiries
RAW_COLUMNS = ["type", "contract", "strike", "bid", "ask", "last",
               "volume", "oi", "provider_iv", "last_trade"]


def _yahoo_url(symbol: str) -> str:
    return f"https://finance.yahoo.com/quote/{symbol}/options"


def _cboe_symbol(symbol: str) -> str:
    return "_" + symbol[1:] if symbol.startswith("^") else symbol


# ---------- ingest ----------

def _raw_side(df: pd.DataFrame, side: str) -> pd.DataFrame:
    out = pd.DataFrame({
        "type": side,
        "contract": df["contractSymbol"],
        "strike": df["strike"].astype(float),
        "bid": df["bid"], "ask": df["ask"], "last": df["lastPrice"],
        "volume": df["volume"], "oi": df["openInterest"],
        "provider_iv": df["impliedVolatility"],
        "last_trade": pd.to_datetime(df["lastTradeDate"], utc=True).dt.strftime(
            "%Y-%m-%dT%H:%M:%S%z"),
    })
    return out[RAW_COLUMNS]


def _cboe_spot(symbol: str) -> dict:
    """CBOE delayed spot, or a not_verified line."""
    url = CBOE_URL.format(symbol=_cboe_symbol(symbol))
    try:
        got = http.get(url)
        payload = got.json()
    except (http.FetchError, snap.OfflineMiss, ValueError) as e:
        return unverified("CBOE cross-check spot", str(e), url)
    d = payload.get("data") or {}
    return {"spot": num(d.get("current_price")), "last_trade_time": d.get("last_trade_time"),
            "cboe_timestamp": payload.get("timestamp"), "fetched_at": got.fetched_at,
            "url": got.url}


def record_chains(symbol: str, expirations: list[str] | None = None) -> dict:
    """Fetch and store raw chains (default: every listed expiry) plus context."""
    import yfinance as yf

    symbol = symbol.upper()
    if snap.is_offline():
        return unsourced(f"{symbol} chain snapshot", "offline mode: snapshots are read-only",
                      source=SOURCE)
    ticker = yf.Ticker(symbol)
    listed = list(ticker.options or ())
    if not listed:
        return unsourced(f"{symbol} chain", "provider lists no expirations",
                      _yahoo_url(symbol), source=SOURCE)
    wanted = [e for e in (expirations or listed) if e in listed]

    snap.write_json("chains", symbol, LISTING, listed)
    quote_env = spot_quote(symbol)
    context = {
        "fetched_at": quote_env["provenance"]["asof"],
        "quote": quote_env,
        "rate": risk_free_rate(),
        "trailing_dividend_yield": num(quote_summary(symbol).value.get("trailingAnnualDividendYield")),
        "cboe": _cboe_spot(symbol),
    }
    written = []
    for exp in wanted:
        raw = ticker.option_chain(exp)
        frame = pd.concat([_raw_side(raw.calls, "call"), _raw_side(raw.puts, "put")])
        snap.write_table("chains", symbol, exp, frame, meta=context)
        written.append(exp)
    return envelope({"symbol": symbol, "expirations_written": written,
                     "listed_expirations": listed},
                    prov=provenance(source=SOURCE, source_url=_yahoo_url(symbol)))


# ---------- pure analytics ----------

def _price(v) -> float | None:
    """A quote price, or None. Zero is not a price."""
    f = num(v)
    return f if f is not None and f > 0 else None


def _iv_pct(price, spot, strike, T, r, q, side) -> float | None:
    if price is None or T <= 0:
        return None
    iv = implied_vol(price, spot, strike, T, r, side, q)
    return round(iv * 100, 2) if iv is not None else None


def derive_quote_fields(raw: pd.DataFrame, spot: float, T: float,
                 r: float | None, q: float) -> pd.DataFrame:
    """Per-strike derived fields. IVs are None when r is unavailable."""
    rows = []
    for rec in raw.to_dict("records"):
        bid, ask, last = _price(rec["bid"]), _price(rec["ask"]), _price(rec["last"])
        mid = round((bid + ask) / 2, 4) if bid is not None and ask is not None else None
        oi = num(rec["oi"])
        volume = num(rec["volume"])
        solve = r is not None
        rows.append({
            "type": rec["type"], "strike": float(rec["strike"]),
            "bid": bid, "ask": ask, "mid": mid, "last": last,
            "last_outside_bidask": (last is not None and bid is not None and ask is not None
                                    and not bid <= last <= ask),
            "spread_pct": round((ask - bid) / mid * 100, 2) if mid else None,
            "volume": volume, "oi": oi,
            "vol_oi": round(volume / oi, 3) if volume is not None and oi else None,
            "provider_iv_pct": round(num(rec["provider_iv"]) * 100, 2)
            if num(rec["provider_iv"]) else None,
            "iv_mid_pct": _iv_pct(mid, spot, rec["strike"], T, r, q, rec["type"]) if solve else None,
            "iv_bid_pct": _iv_pct(bid, spot, rec["strike"], T, r, q, rec["type"]) if solve else None,
            "iv_ask_pct": _iv_pct(ask, spot, rec["strike"], T, r, q, rec["type"]) if solve else None,
        })
    return pd.DataFrame(rows)


def parity_spot(rows: pd.DataFrame, feed_spot: float, T: float,
                r: float, q: float) -> dict | None:
    """Median parity-implied spot over the strikes nearest the feed spot."""
    calls = rows[(rows["type"] == "call") & rows["mid"].notna()].set_index("strike")["mid"]
    puts = rows[(rows["type"] == "put") & rows["mid"].notna()].set_index("strike")["mid"]
    both = sorted(set(calls.index) & set(puts.index), key=lambda k: abs(k - feed_spot))
    picked = both[:PARITY_STRIKES]
    if len(picked) < PARITY_STRIKES:
        return None
    implied = [(calls[k] - puts[k] + k * math.exp(-r * T)) * math.exp(q * T) for k in picked]
    return {"spot": round(float(np.median(implied)), 4),
            "strikes": [{"strike": k, "implied_spot": round(s, 4)}
                        for k, s in zip(picked, implied, strict=True)]}


def _pct_diff(a: float | None, b: float | None) -> float | None:
    return round(abs(a - b) / b * 100, 3) if a is not None and b else None


def spot_check(feed: float, cboe: float | None, parity: float | None) -> dict:
    """Pairwise disagreement between the three spots, and FEED_SUSPECT."""
    pairs = {"feed_vs_cboe_pct": _pct_diff(feed, cboe),
             "feed_vs_parity_pct": _pct_diff(feed, parity),
             "cboe_vs_parity_pct": _pct_diff(cboe, parity)}
    breaches = [k for k, v in pairs.items() if v is not None and v > FEED_SUSPECT_PCT]
    return {"feed_spot": feed, "cboe_spot": cboe, "parity_spot": parity, **pairs,
            "threshold_pct": FEED_SUSPECT_PCT,
            "flag": "FEED_SUSPECT" if breaches else None, "breaches": breaches}


def chain_usability(rows: pd.DataFrame, spot: float, thresholds: dict) -> dict:
    """Median spread and OI depth near the money, with a verdict."""
    lo, hi = NEAR_MONEY_BAND
    band = rows[(rows["strike"] >= lo * spot) & (rows["strike"] <= hi * spot)]
    spreads = band["spread_pct"].dropna()
    two_sided = int(band["mid"].notna().sum())
    median_spread = round(float(spreads.median()), 2) if not spreads.empty else None
    share_liquid = (round(float((band["oi"].fillna(0) >= LIQUID_OI).mean()), 3)
                    if len(band) else None)

    passes_spread = median_spread is not None and median_spread <= thresholds["max_median_spread_pct"]
    passes_oi = share_liquid is not None and share_liquid >= thresholds["min_share_oi_500"]
    if two_sided == 0:
        verdict, reason = "unusable", "no two-sided quotes near the money (dead chain or after-hours zeros)"
    elif passes_spread and passes_oi:
        verdict, reason = "usable", "spread and OI depth both pass"
    elif passes_spread or passes_oi:
        verdict = "thin"
        reason = "OI depth fails" if passes_spread else "median spread fails"
    else:
        verdict, reason = "unusable", "median spread and OI depth both fail"
    return {"verdict": verdict, "reason": reason,
            "median_spread_pct": median_spread, "share_strikes_oi_ge_500": share_liquid,
            "two_sided_quotes": two_sided, "strikes_in_band": int(len(band)),
            "band": f"{int(lo * 100)}-{int(hi * 100)}% of spot",
            "thresholds": thresholds}


def oi_walls(rows: pd.DataFrame) -> dict:
    """Top strikes by open interest per side, with percent of side OI."""
    out = {}
    for side in ("call", "put"):
        s = rows[rows["type"] == side]
        total = float(s["oi"].fillna(0).sum())
        top = s.dropna(subset=["oi"]).nlargest(WALLS_PER_SIDE, "oi")
        out[side] = [{"strike": r.strike, "oi": int(r.oi),
                      "pct_of_side": round(r.oi / total * 100, 1) if total else None}
                     for r in top.itertuples()]
        out[f"{side}_total_oi"] = int(total)
    return out


def atm_row_pair(rows: list[dict], spot: float) -> tuple[dict, dict] | None:
    """(call row, put row) at the listed strike nearest spot, or None."""
    calls = {r["strike"]: r for r in rows if r["type"] == "call"}
    puts = {r["strike"]: r for r in rows if r["type"] == "put"}
    common = set(calls) & set(puts)
    if not common:
        return None
    k = min(common, key=lambda s: abs(s - spot))
    return calls[k], puts[k]


def atm_iv_pct(rows: list[dict], spot: float) -> float | None:
    """Mean of call and put mid-IV at the strike nearest spot (either if one is missing)."""
    pair = atm_row_pair(rows, spot)
    ivs = [r["iv_mid_pct"] for r in pair or () if r["iv_mid_pct"] is not None]
    return round(sum(ivs) / len(ivs), 2) if ivs else None


# ---------- public ----------

def _quality_label(session: str | None, rows: pd.DataFrame, from_snapshot: bool) -> str:
    if rows["mid"].notna().sum() == 0:
        return "last_only"
    if from_snapshot:
        return "snapshot"
    return "delayed" if session == "regular" else "stale"


def _nearest_listed(expiration: str, listed: list[str]) -> dict:
    before = [e for e in listed if e < expiration]
    after = [e for e in listed if e > expiration]
    return {"listed_before": before[-1] if before else None,
            "listed_after": after[0] if after else None}


def chain_report(symbol: str, expiration: str, *, include_rows: bool = True) -> dict:
    """The quality report for one expiry: spot check, usability, walls, rows.

    Online: refreshes this expiry's snapshot first. Offline: replays the
    latest snapshot on or before OPTIONSLAB_ASOF.
    """
    symbol = symbol.upper()
    if not snap.is_offline():
        record_chains(symbol, [expiration])

    listed = listed_expirations(symbol)
    if listed is None:
        return unsourced(f"{symbol} chain", "no chain snapshot", _yahoo_url(symbol), source=SOURCE)
    if expiration not in listed:
        return envelope(None, prov=provenance(source=SOURCE, source_url=_yahoo_url(symbol),
                                              quality="unavailable"),
                        not_verified=[unverified(f"{symbol} {expiration} chain",
                                         "expiration is not listed", _yahoo_url(symbol),
                                         **_nearest_listed(expiration, listed))])
    table = snap.read_table("chains", symbol, expiration)
    if table is None:
        return unsourced(f"{symbol} {expiration} chain", "listed but never snapshotted",
                      _yahoo_url(symbol), source=SOURCE)
    raw, context, day = table
    return _report(symbol, expiration, raw, context, day, include_rows)


def _report(symbol: str, expiration: str, raw: pd.DataFrame, context: dict,
            day: date, include_rows: bool) -> dict:
    warnings, not_verified = [], []
    q_env, r_env = context["quote"], context["rate"]
    spot = (q_env.get("data") or {}).get("spot")
    if spot is None:
        return unsourced(f"{symbol} spot", "no feed spot in snapshot", _yahoo_url(symbol), source=SOURCE)
    r = (r_env.get("data") or {}).get("r")
    if r is None:
        not_verified.append(unverified("risk-free rate", "unavailable; IVs and parity spot not computed",
                               r_env["provenance"].get("source_url")))
    q = context.get("trailing_dividend_yield") or 0.0
    warnings.append(f"q = trailing dividend yield {q:.4f} (yfinance); see dividend_policy "
                    "for variable-dividend names")

    fetched = datetime.fromisoformat(context["fetched_at"])
    T = year_fraction(expiration, now=fetched.astimezone(ET).replace(tzinfo=None))
    rows = derive_quote_fields(raw, spot, T, r, q)

    parity = parity_spot(rows, spot, T, r, q) if r is not None else None
    if r is not None and parity is None:
        not_verified.append(unverified("parity spot", f"fewer than {PARITY_STRIKES} strikes with "
                               "two-sided call and put quotes"))
    cboe = context["cboe"]
    if cboe.get("status") == "not_verified":
        not_verified.append(cboe)
        cboe_spot = None
    else:
        cboe_spot = cboe["spot"]
    check = spot_check(spot, cboe_spot, parity["spot"] if parity else None)
    if check["flag"]:
        warnings.append(f"FEED_SUSPECT: {', '.join(check['breaches'])} above "
                        f"{FEED_SUSPECT_PCT}% — do not size on the feed spot")

    thresholds = {**config.DEFAULTS["usability"], **config.get("usability")}
    session = q_env["provenance"].get("session")
    quality = _quality_label(session, rows, snap.is_offline())
    if quality == "last_only":
        warnings.append("no two-sided quotes: Last prints only; nothing here is a price")

    data = {
        "symbol": symbol, "expiration": expiration, "snapshot_date": day.isoformat(),
        "T_years": round(T, 5), "r": r, "q": q,
        "spot_check": check, "parity": parity, "cboe": cboe if cboe_spot is not None else None,
        "atm_iv_pct": atm_iv_pct(rows.to_dict("records"), spot),
        "usability": chain_usability(rows, spot, thresholds),
        "walls": oi_walls(rows),
        "rows": rows.to_dict("records") if include_rows else None,
    }
    prov = provenance(source=f"{SOURCE}; spot {q_env['provenance']['source']}",
                      source_url=_yahoo_url(symbol), asof=context["fetched_at"],
                      session=session, quality=quality, snapshot_date=day.isoformat())
    return envelope(data, prov=prov, warnings=warnings, not_verified=not_verified)


def listed_expirations(symbol: str) -> list[str] | None:
    """Expiries the provider listed at the latest snapshot (None if never fetched)."""
    hit = snap.read_json("chains", symbol.upper(), LISTING)
    return hit[0] if hit else None
