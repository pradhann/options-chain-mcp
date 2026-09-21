"""Managed-money positioning from the Commitments of Traders reports (item 7).

What: weekly managed-money gross long, gross short, net (long - short) and
total open interest, all in contracts, for NYMEX CL, HO, RB, NG (CFTC
Disaggregated, futures and options combined) and ICE Brent (ICE Futures
Europe COT, futures and options combined). Derived: net as a percent of
open interest, the one- and three-year percentile rank of net and of
net % OI, days/weeks since the report date, and a `stale` flag.

Why: positioning is a crowding gauge for energy options; a percentile is
only meaningful next to the report date it describes, so every read says
which Tuesday it is, when it was released, and how old it is.

Sources (all verified live 2026-09-21):
  * CFTC Socrata dataset kh3c-gbw2, metadata name "Disaggregated - Combined"
    ("a report of futures and options combined positions"):
    https://publicreporting.cftc.gov/resource/kh3c-gbw2.json
    Contract codes were discovered by listing market_and_exchange_names:
      067651 WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE
      022651 NY HARBOR ULSD - NEW YORK MERCANTILE EXCHANGE
      111659 GASOLINE RBOB - NEW YORK MERCANTILE EXCHANGE
      023651 NAT GAS NYME - NEW YORK MERCANTILE EXCHANGE
  * CFTC release schedule (tentative, 3:30 p.m. ET):
    https://www.cftc.gov/MarketReports/CommitmentsofTraders/ReleaseSchedule/index.htm
    A report's release date is the first scheduled release after its report
    date; it is read from the page, never computed from the weekday.
  * ICE yearly history files https://www.ice.com/publicdocs/futures/COTHist{YYYY}.csv
    (2023-2026 verified), row "ICE Brent Crude Futures and Options - ICE
    Futures Europe" (FutOnly_or_Combined = "Combined"). ICE publishes no
    machine-readable release schedule, so Brent's release date is
    not_verified with the ICE report page https://www.ice.com/report/122.

"Today" is snapshots.asof_date() so offline replays are reproducible.
"""

from __future__ import annotations

import csv
import io
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html import unescape

from ..storage import snapshots as snap
from . import config, http
from .envelope import envelope, iso, provenance, unsourced, unverified

CFTC_DATASET_URL = "https://publicreporting.cftc.gov/resource/kh3c-gbw2.json"
CFTC_SCHEDULE_URL = ("https://www.cftc.gov/MarketReports/CommitmentsofTraders/"
                     "ReleaseSchedule/index.htm")
ICE_HIST_URL = "https://www.ice.com/publicdocs/futures/COTHist{year}.csv"
ICE_REPORT_PAGE = "https://www.ice.com/report/122"

CFTC_SOURCE = "CFTC Disaggregated COT, futures and options combined"
ICE_SOURCE = "ICE Futures Europe COT, futures and options combined"

# root -> (CFTC contract market code, market_and_exchange_names seen 2026-09-21)
CFTC_MARKETS: dict[str, tuple[str, str]] = {
    "CL": ("067651", "WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE"),
    "HO": ("022651", "NY HARBOR ULSD - NEW YORK MERCANTILE EXCHANGE"),
    "RB": ("111659", "GASOLINE RBOB - NEW YORK MERCANTILE EXCHANGE"),
    "NG": ("023651", "NAT GAS NYME - NEW YORK MERCANTILE EXCHANGE"),
}
ICE_BRENT_MARKET = "ICE Brent Crude Futures and Options - ICE Futures Europe"
ROOTS = ("CL", "HO", "RB", "NG", "BZ")

# ~3.6 years of weekly prints: enough for a three-year percentile window.
CFTC_WEEKS = 190
ICE_YEARS_BACK = 3
PERCENTILE_WINDOWS = {"1y": 1, "3y": 3}
# A window counts as covered when history starts within this of its start.
WINDOW_COVERAGE_SLACK = timedelta(days=14)

_CFTC_FIELDS = ("report_date_as_yyyy_mm_dd", "market_and_exchange_names",
                "cftc_contract_market_code", "futonly_or_combined", "contract_units",
                "open_interest_all", "m_money_positions_long_all",
                "m_money_positions_short_all")


@dataclass(frozen=True)
class Print:
    """One weekly COT observation, in contracts."""
    report_date: date
    open_interest: int
    mm_long: int
    mm_short: int

    @property
    def net(self) -> int:
        return self.mm_long - self.mm_short

    @property
    def net_pct_oi(self) -> float | None:
        return 100.0 * self.net / self.open_interest if self.open_interest else None


@dataclass(frozen=True)
class Series:
    """A market's weekly prints (oldest first) plus the labels it came with."""
    market: str
    report_type: str
    units: str | None
    prints: list[Print]


# ---------- URLs ----------

def cftc_url(code: str) -> str:
    """Date-independent query: the latest CFTC_WEEKS prints for one contract."""
    query = {
        "$select": ",".join(_CFTC_FIELDS),
        "$where": f"cftc_contract_market_code='{code}'",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": str(CFTC_WEEKS),
    }
    qs = urllib.parse.urlencode(query, safe="$(),'=:*")
    return f"{CFTC_DATASET_URL}?{qs}"


def ice_url(year: int) -> str:
    return ICE_HIST_URL.format(year=year)


# ---------- pure parsers ----------

def parse_cftc(rows: list[dict]) -> Series:
    """Socrata JSON rows for one contract -> Series (oldest first)."""
    if not rows:
        raise ValueError("CFTC query returned no rows")
    prints = {}
    for r in rows:
        day = date.fromisoformat(r["report_date_as_yyyy_mm_dd"][:10])
        prints[day] = Print(day, int(r["open_interest_all"]),
                            int(r["m_money_positions_long_all"]),
                            int(r["m_money_positions_short_all"]))
    latest = max(rows, key=lambda r: r["report_date_as_yyyy_mm_dd"])
    return Series(market=latest["market_and_exchange_names"],
                  report_type=latest.get("futonly_or_combined") or "",
                  units=latest.get("contract_units"),
                  prints=[prints[d] for d in sorted(prints)])


def parse_ice_csv(text: str, market: str) -> Series:
    """One ICE COTHist{YYYY}.csv -> the Series for `market` (oldest first)."""
    reader = csv.DictReader(io.StringIO(text.removeprefix("\ufeff")))
    rows = [r for r in reader if r["Market_and_Exchange_Names"].strip() == market]
    if not rows:
        raise ValueError(f"no rows for {market!r}")
    prints = {}
    for r in rows:
        day = datetime.strptime(r["As_of_Date_In_Form_YYMMDD"].strip(), "%y%m%d").date()
        prints[day] = Print(day, int(r["Open_Interest_All"]),
                            int(r["M_Money_Positions_Long_All"]),
                            int(r["M_Money_Positions_Short_All"]))
    last = rows[-1]
    return Series(market=market, report_type=last.get("FutOnly_or_Combined", "").strip(),
                  units=last.get("Contract_Units"),
                  prints=[prints[d] for d in sorted(prints)])


def merge_series(parts: list[Series]) -> Series:
    """Concatenate yearly Series; a later part wins on a duplicate date."""
    by_day = {p.report_date: p for s in parts for p in s.prints}
    last = parts[-1]
    return Series(last.market, last.report_type, last.units,
                  [by_day[d] for d in sorted(by_day)])


_SCHEDULE_HEADING = re.compile(r"(\d{4})\s+Release Schedule")
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")


def parse_release_schedule(html: str) -> list[date]:
    """CFTC release-schedule page -> scheduled release dates, sorted.

    Each "{YYYY} Release Schedule" heading is followed by a table whose rows
    are a month name then day numbers ("05*" marks a holiday-delayed date).
    """
    heads = list(_SCHEDULE_HEADING.finditer(html))
    out = []
    for i, head in enumerate(heads):
        year = int(head.group(1))
        end = heads[i + 1].start() if i + 1 < len(heads) else len(html)
        for row in _ROW.findall(html[head.end():end]):
            cells = [unescape(_TAG.sub("", c)).strip() for c in _CELL.findall(row)]
            if not cells:
                continue
            try:
                month = datetime.strptime(cells[0], "%B").month
            except ValueError:
                continue
            for cell in cells[1:]:
                digits = cell.rstrip("*").strip()
                if digits.isdigit():
                    out.append(date(year, month, int(digits)))
    return sorted(out)


def release_date_for(report_date: date, schedule: list[date]) -> date | None:
    """The first scheduled release after `report_date`.

    None unless the schedule also lists a release on or before the report
    date: otherwise the page does not cover that week and the first listed
    date could belong to a later report.
    """
    if not schedule or schedule[0] > report_date:
        return None
    return next((d for d in schedule if d > report_date), None)


def percentile_rank(values: list[tuple[date, float]], years: int) -> float | None:
    """Percent of observations in the trailing window <= the latest one.

    The window is the `years` before the latest date. None when the history
    does not reach back to (within WINDOW_COVERAGE_SLACK of) the window start.
    """
    if not values:
        return None
    latest_day, latest = values[-1]
    start = latest_day - timedelta(days=365 * years)
    if values[0][0] > start + WINDOW_COVERAGE_SLACK:
        return None
    window = [v for d, v in values if d > start]
    return 100.0 * sum(v <= latest for v in window) / len(window)


def summarise(series: Series, today: date, stale_days: int) -> dict:
    """Latest print, derived fields, and percentiles for one Series."""
    last = series.prints[-1]
    age = (today - last.report_date).days
    history = {
        "net": [(p.report_date, float(p.net)) for p in series.prints],
        "net_pct_oi": [(p.report_date, p.net_pct_oi) for p in series.prints
                       if p.net_pct_oi is not None],
    }
    return {
        "market": series.market,
        "report_type": series.report_type,
        "units": series.units,
        "report_date": last.report_date,
        "days_since_report": age,
        "weeks_since_print": round(age / 7, 1),
        "stale": age > stale_days,
        "stale_days_threshold": stale_days,
        "managed_money": {"long": last.mm_long, "short": last.mm_short, "net": last.net},
        "open_interest": last.open_interest,
        "net_pct_oi": last.net_pct_oi,
        "percentiles": {name: {w: percentile_rank(vals, yrs)
                               for w, yrs in PERCENTILE_WINDOWS.items()}
                        for name, vals in history.items()},
        "history_start": series.prints[0].report_date,
        "history_weeks": len(series.prints),
    }


# ---------- fetch ----------

@dataclass
class _Read:
    """A root's Series plus where it came from and what could not be sourced."""
    series: Series
    latest: http.Fetched          # the fetch that supplied the latest print
    source: str
    url: str
    release_date: date | None = None
    release_source: str | None = None
    warnings: list[str] = field(default_factory=list)
    not_verified: list[dict] = field(default_factory=list)


def _read_cftc(root: str) -> _Read:
    code, expected_name = CFTC_MARKETS[root]
    url = cftc_url(code)
    got = http.get(url)
    series = parse_cftc(got.json())
    read = _Read(series, got, CFTC_SOURCE, url, warnings=_notes([got]))
    if series.market != expected_name:
        read.warnings.append(f"CFTC market name for {code} is now {series.market!r} "
                             f"(was {expected_name!r})")
    report_date = series.prints[-1].report_date
    try:
        schedule = http.get(CFTC_SCHEDULE_URL, max_age_hours=24)
    except (http.FetchError, snap.OfflineMiss) as e:
        read.not_verified.append(unverified(f"{root} release_date", f"release schedule unavailable: {e}",
                                    CFTC_SCHEDULE_URL))
        return read
    read.warnings += _notes([schedule])
    read.release_date = release_date_for(report_date, parse_release_schedule(schedule.text()))
    if read.release_date is None:
        read.not_verified.append(unverified(f"{root} release_date", "release schedule page does not cover "
                                    f"report date {report_date}", CFTC_SCHEDULE_URL))
    else:
        read.release_source = CFTC_SCHEDULE_URL
    return read


def _read_ice(today: date) -> _Read:
    """Brent from the ICE yearly files. Closed years are cached for 30 days."""
    parts, fetched, missing = [], [], []
    for year in range(today.year - ICE_YEARS_BACK, today.year + 1):
        url = ice_url(year)
        try:
            got = http.get(url, max_age_hours=None if year == today.year else 24 * 30)
            parts.append(parse_ice_csv(got.text(), ICE_BRENT_MARKET))
        except (http.FetchError, snap.OfflineMiss, ValueError) as e:
            missing.append(unverified(f"BZ COT {year}", str(e), url))
            continue
        fetched.append(got)
    if not parts:
        raise http.FetchError("; ".join(m["reason"] for m in missing))
    series = merge_series(parts)
    if series.report_type != "Combined":
        missing.append(unverified("BZ report type", f"ICE row labelled {series.report_type!r}, "
                          "expected 'Combined'", ICE_REPORT_PAGE))
    missing.append(unverified("BZ release_date", "ICE publishes no machine-readable COT release "
                      "schedule; not computed", ICE_REPORT_PAGE))
    return _Read(series, fetched[-1], ICE_SOURCE, fetched[-1].url,
                 warnings=_notes(fetched), not_verified=missing)


def _notes(fetched: list[http.Fetched]) -> list[str]:
    return [f.note for f in fetched if f.from_snapshot and f.note]


def _percentile_gaps(root: str, data: dict, url: str) -> list[dict]:
    return [unverified(f"{root} {name} {window} percentile",
               f"history starts {data['history_start']}, too short for a {window} window",
               url)
            for name, windows in data["percentiles"].items()
            for window, value in windows.items() if value is None]


# ---------- public ----------

def managed_money(root: str) -> dict:
    """Managed-money positioning for one root (CL, HO, RB, NG, BZ).

    data: {root, market, report_type, units, report_date, release_date,
    release_date_source, days_since_report, weeks_since_print, stale,
    stale_days_threshold, managed_money: {long, short, net}, open_interest,
    net_pct_oi, percentiles: {net: {1y, 3y}, net_pct_oi: {1y, 3y}},
    history_start, history_weeks}. Positions are contracts; net_pct_oi is
    percent; percentiles are 0-100 ranks within the trailing window.
    """
    root = root.upper()
    today = snap.asof_date()
    item = f"{root} managed money"
    if root not in ROOTS:
        return unsourced(item, f"unknown root {root!r}; supported: {', '.join(ROOTS)}",
                      source="optionslab.cot")
    try:
        read = _read_ice(today) if root == "BZ" else _read_cftc(root)
    except (http.FetchError, snap.OfflineMiss, ValueError, KeyError) as e:
        if root == "BZ":
            return unsourced(item, f"ICE COT unavailable: {e}", ICE_REPORT_PAGE, source=ICE_SOURCE)
        return unsourced(item, str(e), cftc_url(CFTC_MARKETS[root][0]), source=CFTC_SOURCE)

    stale_days = int(config.get("cot_stale_days"))
    data = {"root": root, **summarise(read.series, today, stale_days),
            "release_date": read.release_date, "release_date_source": read.release_source}
    warnings = list(read.warnings)
    if data["stale"]:
        warnings.append(f"{root} COT print {data['report_date']} is "
                        f"{data['days_since_report']} days old (> {stale_days})")
    if read.latest.from_snapshot:
        quality = "snapshot"
    else:
        quality = "stale" if data["stale"] else "live"
    prov = provenance(source=read.source, source_url=read.url,
                      asof=data["report_date"].isoformat(), quality=quality,
                      fetched_at=read.latest.fetched_at)
    return envelope(data, prov=prov, warnings=warnings,
                    not_verified=read.not_verified + _percentile_gaps(root, data, read.url))


def net_history(root: str) -> dict:
    """Weekly managed-money history for one root (the chart series).

    data: {root, market, report_type, prints: [{report_date, mm_long, mm_short,
    net, open_interest, net_pct_oi}]}, oldest first.
    """
    root = root.upper()
    item = f"{root} managed-money history"
    if root not in ROOTS:
        return unsourced(item, f"unknown root {root!r}; supported: {', '.join(ROOTS)}",
                      source="optionslab.cot")
    try:
        read = _read_ice(snap.asof_date()) if root == "BZ" else _read_cftc(root)
    except (http.FetchError, snap.OfflineMiss, ValueError, KeyError) as e:
        url = ICE_REPORT_PAGE if root == "BZ" else cftc_url(CFTC_MARKETS[root][0])
        return unsourced(item, str(e), url, source=ICE_SOURCE if root == "BZ" else CFTC_SOURCE)
    prints = [{"report_date": p.report_date.isoformat(), "mm_long": p.mm_long,
               "mm_short": p.mm_short, "net": p.net, "open_interest": p.open_interest,
               "net_pct_oi": p.net_pct_oi} for p in read.series.prints]
    prov = provenance(source=read.source, source_url=read.url,
                      asof=read.series.prints[-1].report_date.isoformat(),
                      quality="snapshot" if read.latest.from_snapshot else "live",
                      fetched_at=read.latest.fetched_at)
    return envelope({"root": root, "market": read.series.market,
                     "report_type": read.series.report_type, "prints": prints},
                    prov=prov, warnings=read.warnings)


def positioning_summary(roots: tuple[str, ...] = ROOTS) -> dict:
    """managed_money() for each root, one compact row per root.

    data: {"rows": [{root, report_date, release_date, stale, mm_long,
    mm_short, mm_net, open_interest, net_pct_oi, net_pctile_1y,
    net_pctile_3y, net_pct_oi_pctile_1y, net_pct_oi_pctile_3y}]}.
    A root that failed contributes its not_verified lines and no row.
    """
    rows, warnings, unverified_lines, qualities = [], [], [], set()
    for root in roots:
        env = managed_money(root)
        warnings += env["warnings"]
        unverified_lines += env["not_verified"]
        d = env["data"]
        if d is None:
            continue
        qualities.add(env["provenance"]["quality"])
        mm, pct = d["managed_money"], d["percentiles"]
        rows.append({
            "root": d["root"], "report_date": d["report_date"],
            "release_date": d["release_date"], "stale": d["stale"],
            "mm_long": mm["long"], "mm_short": mm["short"], "mm_net": mm["net"],
            "open_interest": d["open_interest"], "net_pct_oi": d["net_pct_oi"],
            "net_pctile_1y": pct["net"]["1y"], "net_pctile_3y": pct["net"]["3y"],
            "net_pct_oi_pctile_1y": pct["net_pct_oi"]["1y"],
            "net_pct_oi_pctile_3y": pct["net_pct_oi"]["3y"],
        })
    quality = next((q for q in ("snapshot", "stale") if q in qualities), "live")
    prov = provenance(source=f"{CFTC_SOURCE}; {ICE_SOURCE}", source_url=CFTC_DATASET_URL,
                      asof=iso(), quality=quality if rows else "unavailable")
    return envelope({"rows": rows} if rows else None, prov=prov, warnings=warnings,
                    not_verified=unverified_lines)
