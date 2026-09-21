"""Prediction-market reads from Polymarket's public Gamma API (item 10).

What: for a configured list of markets, the current YES probability, its
24-hour and 7-day change, volume, the resolution criteria text and the
resolution date. A probability is never returned without the market id,
question and resolution text it belongs to.

Why: event probabilities (Hormuz traffic, ceasefires, price thresholds)
are inputs to scenario weights; what the market resolves on matters as
much as the number.

Source (verified live 2026-09-21): https://gamma-api.polymarket.com
  /markets/{id}         one market object (422 for an unknown id)
  /markets?slug={slug}  a list with zero or one market object
  /public-search?q=     {"events": [{..., "markets": [...]}], "pagination"}
Fields used from a market object, as observed on 2026-09-21:
  outcomes, outcomePrices  JSON-encoded string lists; the YES probability is
                           the "Yes" entry of outcomePrices, the price the
                           Polymarket UI displays. It matched the bid/ask
                           midpoint on two-sided books, but on a book with no
                           bid it can be a level nobody has traded, so bid,
                           ask and last trade are returned beside it.
  bestBid, bestAsk, lastTradePrice   floats, null when absent
  oneDayPriceChange, oneWeekPriceChange   price-point changes, null on new
                           or thin markets
  volumeNum (lifetime USDC), volume24hr, volume1wk
  description (resolution criteria), resolutionSource, endDate, active, closed

Units: probabilities and changes are fractions (0.195 = 19.5%); volumes USDC.

History: every live read is written to the snapshot store
(snapshots/polymarket/{market id}/{date}/read.json, one per ET day, the
day's latest read). When Gamma omits a change, it is computed from our own
read of exactly one or seven days earlier, and `source` says which.
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import date, timedelta

from ..storage import snapshots as snap
from . import config, http
from .envelope import envelope, iso, num, provenance, unsourced, unverified

GAMMA = "https://gamma-api.polymarket.com"
SOURCE = "Polymarket Gamma API"
SNAP_KIND = "polymarket"
SNAP_NAME = "read"
# change name -> (Gamma field, days back for our own reference read)
CHANGES = {"24h": ("oneDayPriceChange", 1), "7d": ("oneWeekPriceChange", 7)}


# ---------- URLs ----------

def market_url(slug_or_id: str) -> str:
    """Numeric input is a market id; anything else is a slug."""
    key = str(slug_or_id).strip()
    if key.isdigit():
        return f"{GAMMA}/markets/{key}"
    return f"{GAMMA}/markets?{urllib.parse.urlencode({'slug': key})}"


def search_url(query: str, *, active_only: bool = True) -> str:
    params = {"q": query}
    if active_only:
        params["events_status"] = "active"
    return f"{GAMMA}/public-search?{urllib.parse.urlencode(params)}"


def page_url(slug: str) -> str:
    """Human page; verified 2026-09-21 to redirect to the market's event page."""
    return f"https://polymarket.com/market/{slug}"


# ---------- pure parsers ----------

def _json_list(raw) -> list:
    if isinstance(raw, list):
        return raw
    return json.loads(raw) if raw else []


def parse_market(obj: dict) -> dict:
    """One Gamma market object -> the fields this feed reports.

    `probability` is the "Yes" outcome price, or None when the market's
    outcomes are not Yes/No (then only `outcomes` carries prices).
    """
    names = _json_list(obj.get("outcomes"))
    prices = [num(p) for p in _json_list(obj.get("outcomePrices"))]
    outcomes = dict(zip(names, prices, strict=False))
    return {
        "id": str(obj["id"]),
        "slug": obj.get("slug"),
        "question": obj.get("question"),
        "probability": outcomes.get("Yes"),
        "outcomes": outcomes,
        "best_bid": num(obj.get("bestBid")),
        "best_ask": num(obj.get("bestAsk")),
        "last_trade_price": num(obj.get("lastTradePrice")),
        "gamma_change": {name: num(obj.get(field)) for name, (field, _) in CHANGES.items()},
        "volume": num(obj.get("volumeNum", obj.get("volume"))),
        "volume_24h": num(obj.get("volume24hr")),
        "volume_7d": num(obj.get("volume1wk")),
        "resolution_text": obj.get("description") or None,
        "resolution_source": obj.get("resolutionSource") or None,
        "end_date": obj.get("endDate"),
        "active": obj.get("active"),
        "closed": obj.get("closed"),
        "updated_at": obj.get("updatedAt"),
        "url": page_url(obj["slug"]) if obj.get("slug") else None,
    }


def parse_market_body(body) -> dict:
    """A /markets/{id} object or a /markets?slug= list -> parse_market()."""
    if isinstance(body, list):
        if not body:
            raise LookupError("no market with that slug")
        body = body[0]
    return parse_market(body)


def parse_search(body: dict, *, active_only: bool = True) -> list[dict]:
    """/public-search body -> one row per market, with its event."""
    rows = []
    for event in body.get("events") or []:
        for m in event.get("markets") or []:
            if active_only and m.get("closed"):
                continue
            rows.append({
                "id": str(m["id"]), "slug": m.get("slug"), "question": m.get("question"),
                "end_date": m.get("endDate"), "active": m.get("active"),
                "closed": m.get("closed"),
                "event_slug": event.get("slug"), "event_title": event.get("title"),
            })
    return rows


def change(m: dict, name: str, reference: dict | None) -> dict | None:
    """The `name` change and where it came from, or None if neither source has it.

    Gamma's own figure wins; otherwise our reference read (taken `days`
    earlier) is differenced against the current probability.
    """
    gamma = m["gamma_change"][name]
    if gamma is not None:
        return {"value": gamma, "source": f"gamma:{CHANGES[name][0]}"}
    prob = m["probability"]
    if prob is None or reference is None or reference.get("probability") is None:
        return None
    return {"value": prob - reference["probability"], "source": "own_snapshot",
            "reference_read_at": reference["read_at"],
            "reference_probability": reference["probability"]}


def alert(probability: float | None, alert_above: float | None) -> bool | None:
    if probability is None or alert_above is None:
        return None
    return probability > alert_above


# ---------- snapshot history ----------

def _reference_read(market_id: str, today: date, days: int) -> dict | None:
    hit = snap.read_json(SNAP_KIND, market_id, SNAP_NAME, day=today - timedelta(days=days))
    return hit[0] if hit else None


def _record_read(m: dict, read_at: str) -> None:
    doc = {k: m[k] for k in ("id", "slug", "question", "probability", "outcomes",
                                  "best_bid", "best_ask", "last_trade_price",
                                  "resolution_text", "end_date", "closed")}
    snap.write_json(SNAP_KIND, m["id"], SNAP_NAME, {"read_at": read_at, **doc})


# ---------- public ----------

def market(slug_or_id: str) -> dict:
    """One market read.

    data: {id, slug, question, probability, outcomes, best_bid, best_ask,
    last_trade_price, change_24h, change_7d, volume, volume_24h, volume_7d,
    resolution_text, resolution_source, end_date, active, closed,
    updated_at, url}. Each change is {value, source[, reference_read_at,
    reference_probability]} or None with a not_verified line.
    """
    url = market_url(slug_or_id)
    item = f"polymarket {slug_or_id}"
    try:
        got = http.get(url)
        m = parse_market_body(got.json())
    except (http.FetchError, snap.OfflineMiss, LookupError, ValueError) as e:
        return unsourced(item, str(e), url, source=SOURCE)

    if not got.from_snapshot:
        _record_read(m, got.fetched_at)
    today = snap.asof_date()
    unverified_lines, warnings = [], [got.note] if got.from_snapshot and got.note else []
    if m["probability"] is None:
        unverified_lines.append(unverified(f"{item} probability", f"outcomes are {list(m['outcomes'])}, "
                      "not Yes/No; see outcomes", m["url"]))
    for name, (_, days) in CHANGES.items():
        m[f"change_{name}"] = change(m, name, _reference_read(m["id"], today, days))
        if m[f"change_{name}"] is None:
            unverified_lines.append(unverified(f"{item} change_{name}", f"Gamma omits it and no own read "
                          f"from {today - timedelta(days=days)}", url))
    del m["gamma_change"]
    if m["closed"]:
        warnings.append(f"market {m['id']} is closed")
    if m["best_bid"] is None or m["best_ask"] is None:
        warnings.append(f"market {m['id']} has a one-sided or empty book; the displayed "
                        "probability is not a two-sided midpoint")
    prov = provenance(source=SOURCE, source_url=got.url, asof=got.fetched_at,
                      quality="snapshot" if got.from_snapshot else "live")
    return envelope(m, prov=prov, warnings=warnings, not_verified=unverified_lines)


def watchlist_markets() -> dict:
    """market() for every entry of config key `polymarket`.

    Entry: {"slug" or "id", "label", "alert_above"?}. data: {"markets":
    [market data + label, alert_above, alert]}; `alert` is True when the
    probability is above `alert_above`, None when either is missing.
    """
    entries = config.get("polymarket") or []
    rows, warnings, unverified_lines, from_snapshot = [], [], [], False
    for entry in entries:
        key = entry.get("id") or entry.get("slug")
        if not key:
            unverified_lines.append(unverified(f"polymarket entry {entry.get('label')!r}",
                          "config entry has neither slug nor id"))
            continue
        env = market(str(key))
        warnings += env["warnings"]
        unverified_lines += env["not_verified"]
        if env["data"] is None:
            continue
        from_snapshot |= env["provenance"]["quality"] == "snapshot"
        threshold = num(entry.get("alert_above"))
        rows.append({**env["data"], "label": entry.get("label"), "alert_above": threshold,
                     "alert": alert(env["data"]["probability"], threshold)})
    if not entries:
        unverified_lines.append(unverified("polymarket watchlist", "no markets configured (config key "
                      "'polymarket')"))
    prov = provenance(source=SOURCE, source_url=GAMMA, asof=iso(),
                      quality="snapshot" if from_snapshot or snap.is_offline() else "live")
    return envelope({"markets": rows}, prov=prov, warnings=warnings, not_verified=unverified_lines)


def search(query: str, *, active_only: bool = True) -> dict:
    """Find markets to put in the watchlist.

    data: {"query", "results": [{id, slug, question, end_date, active,
    closed, event_slug, event_title}]}. Closed markets are dropped when
    `active_only`.
    """
    url = search_url(query, active_only=active_only)
    try:
        got = http.get(url)
        results = parse_search(got.json(), active_only=active_only)
    except (http.FetchError, snap.OfflineMiss, ValueError) as e:
        return unsourced(f"polymarket search {query!r}", str(e), url, source=SOURCE)
    prov = provenance(source=SOURCE, source_url=got.url, asof=got.fetched_at,
                      quality="snapshot" if got.from_snapshot else "live")
    warnings = [got.note] if got.from_snapshot and got.note else []
    return envelope({"query": query, "results": results}, prov=prov, warnings=warnings)
