"""EIA Weekly Petroleum Status Report (WPSR) series by PADD, and STEO balances.

What: weekly crude (commercial, excl. SPR), distillate, total gasoline and
kerosene-type jet stocks for the US and PADDs 1-5; SPR crude stocks;
refinery utilization (US and PADDs); US crude and total-product imports and
exports; days of supply. Each weekly value comes with its five-year
seasonal band, its position in that band, and "lowest since" dates. The
monthly Short-Term Energy Outlook (STEO) world balance and price forecasts
come from the same API.

Why: stock levels against the seasonal band are how a WPSR print is read; a
record-low regional stock is a sizing input only if the week it was last
this low is shown with it.

Source: EIA Open Data API v2, https://api.eia.gov/v2/ . The key is read
from EIA_API_KEY; without it DEMO_KEY is used, which is heavily rate limited
(x-ratelimit-limit: 10 observed 2026-09-21). Register a free key at
https://www.eia.gov/opendata/register.php . Request URLs carry no dates
(`sort`/`length`/`offset` only), so offline replay of snapshots works.

Release timing: the WPSR is released Wednesdays 10:30 ET; holiday-shifted
dates come from `feeds.calendar`, not from here. Weeks end on Friday; the
`week_ending` below is EIA's `period`.

Units are EIA's: stocks in thousand barrels (MBBL), flows in thousand
barrels per day (MBBL/D), utilization in percent, days of supply in days.

Definitions (all on the weekly observations, keyed by ISO week number):

  five_year_band   For the latest week ending d with ISO (year Y, week W):
                   the values at ISO week W in each ISO year Y-1 .. Y-5 (for
                   W = 53, a year without week 53 contributes its week 52).
                   min / max / mean of those values. The band is reported
                   only when all five years have a value; otherwise None and
                   a not_verified line.
  percentile       Percent of the five band values that are <= the latest
                   value (0, 20, ..., 100).
  band_position    (value - min) / (max - min) * 100, not clamped: < 0 is
                   below the five-year low, > 100 above the five-year high.
  lowest_since     all_weeks: the most recent earlier week whose value is
                   <= the latest value ("lowest since" that week). same_week:
                   the same search restricted to the latest ISO week (with
                   the 52/53 rule above). week_ending None means the latest
                   value is the lowest in the fetched history, which starts
                   at `history_start`.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Iterable
from datetime import date

from ..storage import snapshots as snap
from . import http
from .envelope import envelope, num, provenance, unsourced, unverified

API = "https://api.eia.gov/v2"
KEY_ENV = "EIA_API_KEY"
DEMO_KEY = "DEMO_KEY"
REGISTER_URL = "https://www.eia.gov/opendata/register.php"
SOURCE = "EIA Open Data API v2"
PAGE_ROWS = 5000                 # API maximum rows per JSON response
BAND_YEARS = 5
SUMMARY_WEEKS = (BAND_YEARS + 1) * 53   # enough weekly rows per series for the band

STOCKS = "petroleum/stoc/wstk"
UTILIZATION = "petroleum/pnp/wiup"
SUPPLY = "petroleum/sum/sndw"

# (series, region) -> (EIA series id, API route). Every id was listed by the
# route's /facet/series/ endpoint on 2026-09-21 with the description quoted
# in the comment; ids that did not appear there are not included.
REGISTRY: dict[tuple[str, str], tuple[str, str]] = {
    # "... Ending Stocks excluding SPR of Crude Oil (Thousand Barrels)"
    ("crude_stocks", "US"): ("WCESTUS1", STOCKS),
    **{("crude_stocks", f"PADD{n}"): (f"WCESTP{n}1", STOCKS) for n in range(1, 6)},
    # "... Ending Stocks of Distillate Fuel Oil (Thousand Barrels)"
    ("distillate_stocks", "US"): ("WDISTUS1", STOCKS),
    **{("distillate_stocks", f"PADD{n}"): (f"WDISTP{n}1", STOCKS) for n in range(1, 6)},
    # "... Ending Stocks of Total Gasoline (Thousand Barrels)"
    ("gasoline_stocks", "US"): ("WGTSTUS1", STOCKS),
    **{("gasoline_stocks", f"PADD{n}"): (f"WGTSTP{n}1", STOCKS) for n in range(1, 6)},
    # "... Ending Stocks of Kerosene-Type Jet Fuel (Thousand Barrels)"
    ("jet_stocks", "US"): ("WKJSTUS1", STOCKS),
    **{("jet_stocks", f"PADD{n}"): (f"WKJSTP{n}1", STOCKS) for n in range(1, 6)},
    # "U.S. Ending Stocks of Crude Oil in SPR (Thousand Barrels)"
    ("spr_stocks", "US"): ("WCSSTUS1", STOCKS),
    # "U.S. Percent Utilization of Refinery Operable Capacity"
    ("refinery_utilization", "US"): ("WPULEUS3", UTILIZATION),
    # "<PADD> Percent Utilization of Refinery Operable Capacity"
    **{("refinery_utilization", f"PADD{n}"): (f"W_NA_YUP_R{n}0_PER", UTILIZATION)
       for n in range(1, 6)},
    # "U.S. Imports / Exports of Crude Oil | Total Petroleum Products (Thousand Barrels per Day)"
    ("crude_imports", "US"): ("WCRIMUS2", SUPPLY),
    ("crude_exports", "US"): ("WCREXUS2", SUPPLY),
    ("product_imports", "US"): ("WRPIMUS2", SUPPLY),
    ("product_exports", "US"): ("WRPEXUS2", SUPPLY),
    # "U.S. Days of Supply of ... (Number of Days)"
    ("crude_days_of_supply", "US"): ("W_EPC0_VSD_NUS_DAYS", SUPPLY),
    ("gasoline_days_of_supply", "US"): ("W_EPM0_VSD_NUS_DAYS", SUPPLY),
    ("distillate_days_of_supply", "US"): ("W_EPD0_VSD_NUS_DAYS", SUPPLY),
    ("jet_days_of_supply", "US"): ("W_EPJK_VSD_NUS_DAYS", SUPPLY),
}
SERIES = tuple(dict.fromkeys(s for s, _ in REGISTRY))

# STEO series ids from /v2/steo/facet/seriesId/ on 2026-09-21.
STEO_SERIES = {
    "PAPR_WORLD": "world_liquids_production",
    "PATC_WORLD": "world_liquids_consumption",
    "T3_STCHANGE_WORLD": "world_net_inventory_withdrawals",
    "PASC_OECD_T3": "oecd_commercial_inventory",
    "WTIPUUS": "wti_price",
    "BREPUUS": "brent_price",
}
STEO_MONTHS = 50


# ---------- key and URLs ----------

def _key() -> tuple[str, list[str]]:
    key = os.environ.get(KEY_ENV, "").strip()
    if key:
        return key, []
    return DEMO_KEY, [f"{KEY_ENV} is not set; using DEMO_KEY, which is rate limited. "
                      f"Register a free key at {REGISTER_URL}"]


def series_url(series_id: str, key: str) -> str:
    """Full weekly history of one series (sorted newest first by the API)."""
    return f"{API}/seriesid/PET.{series_id}.W?api_key={key}"


def route_url(route: str, series_ids: Iterable[str], key: str, offset: int = 0) -> str:
    """Newest-first weekly rows for several series of one route, one page."""
    facets = "".join(f"&facets[series][]={s}" for s in series_ids)
    return (f"{API}/{route}/data/?frequency=weekly&data[0]=value{facets}"
            f"&sort[0][column]=period&sort[0][direction]=desc"
            f"&offset={offset}&length={PAGE_ROWS}&api_key={key}")


def steo_url(key: str) -> str:
    facets = "".join(f"&facets[seriesId][]={s}" for s in STEO_SERIES)
    return (f"{API}/steo/data/?frequency=monthly&data[0]=value{facets}"
            f"&sort[0][column]=period&sort[0][direction]=desc"
            f"&length={len(STEO_SERIES) * STEO_MONTHS}&api_key={key}")


def normalize_region(region: str) -> str:
    """'PADD 1', 'padd1', '1' -> 'PADD1'; 'us' -> 'US'."""
    r = re.sub(r"\s+", "", str(region)).upper()
    if r in ("US", "USA", "TOTAL"):
        return "US"
    m = re.fullmatch(r"(?:PADD)?([1-5])", r)
    return f"PADD{m.group(1)}" if m else r


# ---------- response parsing (pure) ----------

def parse_rows(obj: dict) -> list[dict]:
    """API response -> rows with `period`, `series`, numeric `value`, `units`, description.

    Raises ValueError when the API returned an error body instead of data.
    """
    if "error" in obj or "response" not in obj:
        raise ValueError(str(obj.get("error") or "no response block"))
    out = []
    for r in obj["response"].get("data") or []:
        out.append({"period": r.get("period"), "series": r.get("series") or r.get("seriesId"),
                    "value": num(r.get("value")), "units": r.get("units") or r.get("unit"),
                    "description": r.get("series-description") or r.get("seriesDescription")})
    return out


def observations(rows: Iterable[dict], series_id: str) -> list[tuple[date, float]]:
    """Oldest-first (week_ending, value) for one series; missing values dropped."""
    obs = {date.fromisoformat(r["period"]): r["value"] for r in rows
           if r["series"] == series_id and r["value"] is not None}
    return sorted(obs.items())


# ---------- derived statistics (pure) ----------

def _week(d: date) -> tuple[int, int]:
    y, w, _ = d.isocalendar()
    return y, w


def _same_week(d: date, iso_week: int) -> bool:
    """True if `d` falls in `iso_week` (week 53 maps to 52 in 52-week years)."""
    y, w = _week(d)
    if w == iso_week:
        return True
    return iso_week == 53 and w == 52 and date(y, 12, 28).isocalendar()[1] == 52


def five_year_band(obs: list[tuple[date, float]]) -> dict | None:
    """Band of the latest week's ISO week over the prior five ISO years (see module doc)."""
    if not obs:
        return None
    y, w = _week(obs[-1][0])
    picks: dict[int, tuple[date, float]] = {}
    for d, v in obs[:-1]:
        yr = _week(d)[0]
        if y - BAND_YEARS <= yr < y and _same_week(d, w):
            picks[yr] = (d, v)
    if len(picks) < BAND_YEARS:
        return None
    chosen = sorted(picks.values())
    vals = [v for _, v in chosen]
    return {"min": min(vals), "max": max(vals), "mean": sum(vals) / len(vals), "n": len(vals),
            "weeks": [d.isoformat() for d, _ in chosen], "values": vals}


def band_stats(value: float, band: dict | None) -> dict:
    """percentile, band_position and difference from the band mean (see module doc)."""
    if band is None:
        return {"percentile": None, "band_position": None, "vs_mean": None, "vs_mean_pct": None}
    width = band["max"] - band["min"]
    return {
        "percentile": 100 * sum(1 for v in band["values"] if v <= value) / band["n"],
        "band_position": (value - band["min"]) / width * 100 if width else None,
        "vs_mean": value - band["mean"],
        "vs_mean_pct": (value / band["mean"] - 1) * 100 if band["mean"] else None,
    }


def lowest_since(obs: list[tuple[date, float]]) -> dict:
    """Most recent earlier week with value <= latest, over all weeks and the same ISO week."""
    if not obs:
        return {"all_weeks": None, "same_week": None}
    last_d, last_v = obs[-1]
    iso_week = _week(last_d)[1]
    history_start = obs[0][0].isoformat()

    def find(pred) -> dict:
        for d, v in reversed(obs[:-1]):
            if pred(d) and v <= last_v:
                return {"week_ending": d.isoformat(), "value": v,
                        "years": round((last_d - d).days / 365.25, 1)}
        return {"week_ending": None, "value": None, "years": None,
                "note": f"lowest in fetched history (starts {history_start})"}

    return {"all_weeks": find(lambda d: True),
            "same_week": find(lambda d: _same_week(d, iso_week))}


def analyze(obs: list[tuple[date, float]]) -> dict:
    """Latest value, week-on-week change, five-year band and position in it."""
    last_d, last_v = obs[-1]
    prior = obs[-2][1] if len(obs) > 1 else None
    band = five_year_band(obs)
    stats = band_stats(last_v, band)
    return {"week_ending": last_d.isoformat(), "value": last_v, "prior_week_value": prior,
            "change": last_v - prior if prior is not None else None,
            "five_year_band": band, **stats, "history_start": obs[0][0].isoformat()}


# ---------- fetching ----------

def _fetch(url: str, item: str) -> tuple[dict, http.Fetched] | dict:
    """(response JSON, Fetched), or a not_verified line when the read fails."""
    public = http.public_url(url)
    try:
        got = http.get(url)
        obj = got.json()
        parse_rows(obj)
    except (http.FetchError, snap.OfflineMiss) as e:
        return unverified(item, f"fetch failed: {e}", public)
    except ValueError as e:  # undecodable body or an EIA error document
        return unverified(item, f"EIA API error: {e}", public)
    return obj, got


def _quality(fetched: Iterable[http.Fetched]) -> tuple[str, list[str]]:
    fetched = list(fetched)
    notes = [f.note for f in fetched if f.note]
    return ("snapshot" if any(f.from_snapshot for f in fetched) else "live"), notes


def _unavailable(line: dict, warnings: list[str]) -> dict:
    return envelope(None, prov=provenance(source=SOURCE, source_url=line["url"],
                                          quality="unavailable"),
                    warnings=warnings, not_verified=[line])


def _band_line(series: str, region: str, url: str) -> dict:
    return unverified(f"{series} {region} five-year band",
              f"fewer than {BAND_YEARS} prior years have a value in this ISO week", url)


# ---------- public ----------

def _load(series: str, region: str) -> tuple[dict, list, list[str]] | dict:
    """Registry lookup + full-history fetch shared by weekly_series() and seasonal_history().

    Returns (meta, observations, warnings) or a finished not_verified envelope.
    """
    reg = normalize_region(region)
    entry = REGISTRY.get((series, reg))
    if entry is None:
        regions = sorted(r for s, r in REGISTRY if s == series)
        reason = (f"no verified EIA series for ({series!r}, {reg!r}); "
                  + (f"regions for this series: {regions}" if regions
                     else f"series must be one of {list(SERIES)}"))
        return unsourced(f"{series} {reg}", reason, None, source=SOURCE)
    sid = entry[0]
    key, warnings = _key()
    res = _fetch(series_url(sid, key), f"{series} {reg} ({sid})")
    if isinstance(res, dict):
        return _unavailable(res, warnings)
    obj, got = res
    rows = parse_rows(obj)
    obs = observations(rows, sid)
    if not obs:
        return _unavailable(unverified(f"{series} {reg} ({sid})", "no values returned", got.url), warnings)
    total = num(obj["response"].get("total"))
    if total is not None and total > len(rows):
        warnings.append(f"history truncated: {len(rows)} of {int(total)} rows returned")
    quality, notes = _quality([got])
    head = next(r for r in rows if r["series"] == sid)
    meta = {"series": series, "region": reg, "series_id": sid,
            "description": head["description"], "unit": head["units"], "got": got,
            "quality": quality}
    return meta, obs, warnings + notes


def _prov(meta: dict, **extra) -> dict:
    got = meta["got"]
    return provenance(source=SOURCE, source_url=got.url, asof=got.fetched_at,
                      quality=meta["quality"], **extra)


def weekly_series(series: str, region: str = "US") -> dict:
    """One WPSR series with its five-year band, band percentile and lowest-since weeks.

    series: one of SERIES; region: "US" or "PADD1".."PADD5" ("PADD 1", "1" accepted).
    data: {"series", "region", "series_id", "description", "unit", "week_ending",
           "value", "prior_week_value", "change", "five_year_band": {min, max, mean,
           n, weeks, values} | None, "percentile", "band_position", "vs_mean",
           "vs_mean_pct", "history_start", "lowest_since": {"all_weeks", "same_week"},
           "url"}
    """
    loaded = _load(series, region)
    if isinstance(loaded, dict):
        return loaded
    meta, obs, warnings = loaded
    url = meta["got"].url
    data = {k: meta[k] for k in ("series", "region", "series_id", "description", "unit")}
    data |= {**analyze(obs), "lowest_since": lowest_since(obs), "url": url}
    unverified_lines = [] if data["five_year_band"] else [_band_line(series, data["region"], url)]
    return envelope(data, prov=_prov(meta, week_ending=data["week_ending"]),
                    warnings=warnings, not_verified=unverified_lines)


def seasonal_by_year(obs: list[tuple[date, float]]) -> tuple[list[dict], list[dict]]:
    """(years, band) for charting: the latest ISO year and the BAND_YEARS before it.

    years: [{"year", "points": [{"week", "week_ending", "value"}]}], oldest first.
    band: per ISO week, min/max/mean over the prior BAND_YEARS years that have
    that week (weeks with fewer than BAND_YEARS values are left out) (pure).
    """
    last_year = _week(obs[-1][0])[0]
    first_year = last_year - BAND_YEARS
    by_year: dict[int, list[dict]] = {}
    for d, v in obs:
        y, w = _week(d)
        if y >= first_year:
            by_year.setdefault(y, []).append({"week": w, "week_ending": d.isoformat(),
                                              "value": v})
    by_week: dict[int, list[float]] = {}
    for y, points in by_year.items():
        if y < last_year:
            for p in points:
                by_week.setdefault(p["week"], []).append(p["value"])
    band = [{"week": w, "min": min(vs), "max": max(vs), "mean": sum(vs) / len(vs)}
            for w, vs in sorted(by_week.items()) if len(vs) == BAND_YEARS]
    years = [{"year": y, "points": by_year[y]} for y in sorted(by_year)]
    return years, band


def seasonal_history(series: str, region: str = "US") -> dict:
    """Current ISO year and the five prior years by ISO week, plus the band, for charting.

    data: {"series", "region", "unit", "description",
           "years": [{"year", "points": [{"week", "week_ending", "value"}]}],
           "band": [{"week", "min", "max", "mean"}]}
    """
    loaded = _load(series, region)
    if isinstance(loaded, dict):
        return loaded
    meta, obs, warnings = loaded
    years, band = seasonal_by_year(obs)
    data = {k: meta[k] for k in ("series", "region", "unit", "description")}
    data |= {"years": years, "band": band}
    return envelope(data, prov=_prov(meta, week_ending=obs[-1][0].isoformat()),
                    warnings=warnings)


def _route_rows(route: str, ids: list[str], key: str) -> tuple[list[dict], list, list[dict]]:
    """All pages needed for the band, newest first: (rows, fetched, not_verified)."""
    pages = math.ceil(len(ids) * SUMMARY_WEEKS / PAGE_ROWS)
    rows, fetched, unverified_lines = [], [], []
    for page in range(pages):
        res = _fetch(route_url(route, ids, key, offset=page * PAGE_ROWS),
                     f"{route} page {page + 1} of {pages}")
        if isinstance(res, dict):
            unverified_lines.append(res)
            continue
        rows += parse_rows(res[0])
        fetched.append(res[1])
    return rows, fetched, unverified_lines


def wpsr_summary() -> dict:
    """Every registry series in one call: latest value, change and five-year band.

    Fetches ~six years per series (paged bulk requests per API route), which
    covers the band but not full history; for "lowest since" call
    weekly_series(series, region).
    data: {"week_ending": latest week in the table, "rows": [{"series", "region",
           "series_id", "unit", "week_ending", "value", "prior_week_value", "change",
           "five_year_band", "percentile", "band_position", "vs_mean", "vs_mean_pct",
           "history_start"}]}
    """
    key, warnings = _key()
    by_route: dict[str, list[tuple[str, str, str]]] = {}
    for (series, region), (sid, route) in REGISTRY.items():
        by_route.setdefault(route, []).append((series, region, sid))
    out, fetched, unverified_lines = [], [], []
    for route, entries in by_route.items():
        rows, got, route_nvs = _route_rows(route, [sid for _, _, sid in entries], key)
        fetched += got
        unverified_lines += route_nvs
        url = got[0].url if got else None
        for series, region, sid in entries:
            obs = observations(rows, sid)
            if not obs:
                unverified_lines.append(unverified(f"{series} {region} ({sid})", "no values returned", url))
                continue
            unit = next(r["units"] for r in rows if r["series"] == sid)
            row = {"series": series, "region": region, "series_id": sid, "unit": unit,
                   **analyze(obs)}
            if row["five_year_band"] is None:
                unverified_lines.append(_band_line(series, region, url))
            out.append(row)
    if not out:
        return envelope(None, prov=provenance(source=SOURCE, quality="unavailable"),
                        warnings=warnings, not_verified=unverified_lines)
    quality, notes = _quality(fetched)
    prov = provenance(source=SOURCE, source_url=f"{API}/petroleum/", quality=quality,
                      asof=min(f.fetched_at for f in fetched))
    data = {"week_ending": max(r["week_ending"] for r in out), "rows": out}
    return envelope(data, prov=prov, warnings=warnings + notes, not_verified=unverified_lines)


def parse_steo(obj: dict, current_month: str) -> dict:
    """STEO response -> {name: {"series_id", "description", "unit", "months": [...]}} (pure).

    `after_current_month` marks months later than `current_month` ("YYYY-MM");
    STEO mixes history, estimates and forecasts, and the API does not label
    which is which.
    """
    out: dict[str, dict] = {}
    for r in sorted(parse_rows(obj), key=lambda r: r["period"]):
        name = STEO_SERIES.get(r["series"])
        if name is None:
            continue
        s = out.setdefault(name, {"series_id": r["series"], "description": r["description"],
                                  "unit": r["units"], "months": []})
        s["months"].append({"period": r["period"], "value": r["value"],
                            "after_current_month": r["period"] > current_month})
    return out


def implied_stock_change(series: dict) -> list[dict]:
    """World liquids production minus consumption by month (+ = implied build) (pure)."""
    prod = {m["period"]: m["value"] for m in series.get("world_liquids_production", {})
            .get("months", [])}
    out = []
    for m in series.get("world_liquids_consumption", {}).get("months", []):
        p = prod.get(m["period"])
        if p is not None and m["value"] is not None:
            out.append({"period": m["period"], "value": p - m["value"],
                        "after_current_month": m["after_current_month"]})
    return out


def steo() -> dict:
    """Monthly STEO world balance and price path (latest STEO vintage).

    data: {"series": {name: {"series_id", "description", "unit", "months":
           [{"period", "value", "after_current_month"}]}},
           "implied_stock_change": {"definition", "unit", "months": [...]}}
    Names: world_liquids_production, world_liquids_consumption (million b/d),
    world_net_inventory_withdrawals (million b/d, + = draw),
    oecd_commercial_inventory (million barrels, end of period),
    wti_price, brent_price ($/b).
    """
    key, warnings = _key()
    res = _fetch(steo_url(key), "STEO forward balances")
    if isinstance(res, dict):
        return _unavailable(res, warnings)
    obj, got = res
    series = parse_steo(obj, snap.today_et().strftime("%Y-%m"))
    unverified_lines = [unverified(f"STEO {sid}", "no values returned", got.url)
           for sid, name in STEO_SERIES.items() if name not in series]
    unit = series.get("world_liquids_production", {}).get("unit")
    data = {"series": series,
            "implied_stock_change": {
                "definition": "world_liquids_production - world_liquids_consumption; "
                              "+ = implied stock build",
                "unit": unit, "months": implied_stock_change(series)}}
    quality, notes = _quality([got])
    prov = provenance(source=SOURCE, source_url=got.url, asof=got.fetched_at, quality=quality)
    return envelope(data, prov=prov, warnings=warnings + notes, not_verified=unverified_lines)
