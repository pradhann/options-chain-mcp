"""USCF commodity funds (USO, BNO, UGA, UNG): holdings, roll rule, roll window, roll yield.

What: for each fund, the contract months it holds (when the holdings can be
sourced), the roll rule as the prospectus states it, this month's roll
window as USCF publishes it, and the expected roll yield from the live
futures curve: (sold - bought) / bought on the portion rolled, for the roll
period only, NOT annualized.

Sources, each read on 2026-09-21:

  Fund page     https://www.uscfinvestments.com/{uso,bno,uga,ung}
                Static HTML carries the benchmark sentence (quoted below as
                `page_sentence`); every `roll_rule()` re-reads it and the rule is
                not_verified if that sentence is gone.
  Prospectus    https://secure.alpsinc.com/MarketingAPI/api/v1/Content/uscfinvestments/
                united-states-{oil,brent-oil,gasoline,natural-gas}-fund-pro-20260424.pdf
                (the USO file name came from the fund page's document API; the
                others were fetched and read the same day). The roll-rule
                sentences below are verbatim from those PDFs (typographic
                apostrophes normalized).
  Roll dates    https://secure.alpsinc.com/MarketingAPI/api/v1/Content/uscfinvestments/
                uscf-rolldates-commodities-{year}.csv, the "USCF Commodities
                Fund Roll/Rebalance Dates" CSV linked from each holdings page.
                Rows `FUND,Roll Begin,Roll End` with dates like `1-Sep-26`;
                UGA rows carry only a begin date (its roll is one day).
  Holdings      https://www.uscfinvestments.com/holdings/{fund}. The table is
                filled in the browser from
                https://secure.alpsinc.com/MarketingAPI/api/v1/holding/{FUND}/full,
                which answers HTTP 401 without the bearer token the page embeds.
                The static page has an empty table, so holdings are
                not_verified; this module does not replay that token.

Roll-yield pricing: the contracts sold and bought are the near and next
month at the start of the roll window (the prospectus rule), priced from
`futures.curve()` (yfinance daily close, a settlement proxy). BNO holds ICE
Brent; it is priced with NYMEX BZ (Brent Last Day Financial) of the same
delivery month and says so.
"""

from __future__ import annotations

import csv
import html
import io
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime

from ..storage import snapshots as snap
from . import futures, http
from .calendar import event
from .envelope import envelope, provenance, unsourced, unverified

READ_ON = "2026-09-21"
_ALPS_CONTENT = "https://secure.alpsinc.com/MarketingAPI/api/v1/Content/uscfinvestments/"
ROLL_DATES_URL = _ALPS_CONTENT + "uscf-rolldates-commodities-{year}.csv"
HOLDINGS_API_URL = "https://secure.alpsinc.com/MarketingAPI/api/v1/holding/{fund}/full"
PAGE_MAX_AGE_HOURS = 24
ROLL_YIELD_BASIS = ("(sold - bought) / bought on the portion rolled; for the roll period "
                    "only, not annualized; priced at the futures curve date, not at the "
                    "fund's execution prices")
SOURCE = "USCF prospectus + USCF roll-dates CSV + optionslab.feeds.futures curve"


@dataclass(frozen=True)
class RollRule:
    roll_days: int
    starts: str                 # when the window starts, per the prospectus
    sold: str
    bought: str
    evidence: tuple[str, ...]   # verbatim prospectus sentences


@dataclass(frozen=True)
class Fund:
    code: str
    name: str
    root: str                   # futures root used to price the roll
    page_url: str
    holdings_url: str
    prospectus_url: str
    prospectus_date: str
    page_sentence: str          # verbatim from page_url on READ_ON
    rule: RollRule
    price_note: str | None = None


_PAGE = "https://www.uscfinvestments.com/"
_NEAR_TO_NEXT = ("near month contract to expire", "next month contract to expire")
_TWO_WEEKS = "when the near month contract is within two weeks of expiration"

FUNDS: dict[str, Fund] = {
    "USO": Fund(
        "USO", "United States Oil Fund", "CL", _PAGE + "uso", _PAGE + "holdings/uso",
        _ALPS_CONTENT + "united-states-oil-fund-pro-20260424.pdf", "2026-04-24",
        "The Benchmark Oil Futures Contract is the futures contract for light, sweet crude "
        "oil delivered to Cushing, Oklahoma that is traded on the NYMEX that is the near "
        "month contract to expire and changes, over a five-day period, into the NYMEX "
        "futures contract that is the next month to expire.",
        RollRule(5, "the first business day of each month", *_NEAR_TO_NEXT, (
            "USO's Benchmark Oil Futures Contract is the near month contract to expire "
            "until the near month contract approaches expiration when, over a five-day "
            "period beginning on the first business day of each month, the Benchmark Oil "
            "Futures Contract transitions to the next month contract to expire and remains "
            "that contract until the next roll period.",
            "Typically, on each day during a five-day roll period, USO will seek to "
            "rebalance approximately 20% of the announced percentage of the notional value "
            "of its nearest month instrument and other specified instruments (which could "
            "be 100% of such notional value of such interests) and reinvest the proceeds in "
            "the remaining current portfolio holdings as well as further-dated contracts and "
            "any new specified portfolio holdings.",
        ))),
    "BNO": Fund(
        "BNO", "United States Brent Oil Fund", "BZ", _PAGE + "bno", _PAGE + "holdings/bno",
        _ALPS_CONTENT + "united-states-brent-oil-fund-pro-20260424.pdf", "2026-04-24",
        "BNO's Benchmark Futures Contract is the futures contract on Brent crude oil as "
        "traded on the ICE Futures Exchange that is the near month contract to expire. If "
        "the near month contract is within two weeks of expiration, the Benchmark will be "
        "the next month contract to expire.",
        RollRule(4, _TWO_WEEKS, *_NEAR_TO_NEXT, (
            "BNO's Benchmark Futures Contract is such that every month it begins by using "
            "the near month contract to expire until the near month contract is within two "
            "weeks of expiration, when, over a four-day period, it transitions to the next "
            "month contract to expire as its benchmark contract and keeps that contract as "
            "its benchmark until it becomes the near month contract and close to expiration.",
        )),
        price_note="BNO holds ICE Brent futures; priced with NYMEX BZ (Brent Last Day "
                   "Financial) of the same delivery month as a proxy"),
    "UGA": Fund(
        "UGA", "United States Gasoline Fund", "RB", _PAGE + "uga", _PAGE + "holdings/uga",
        _ALPS_CONTENT + "united-states-gasoline-fund-pro-20260424.pdf", "2026-04-24",
        "UGA's Benchmark Futures Contract is the futures contract on gasoline as traded on "
        "the NYMEX that is the near month contract to expire. If the near month contract is "
        "within two weeks of expiration, the Benchmark will be the next month contract to "
        "expire.",
        RollRule(1, _TWO_WEEKS, *_NEAR_TO_NEXT, (
            "UGA's Benchmark Futures Contract is such that every month it begins by using "
            "the near month contract to expire until the near month contract is within two "
            "weeks of expiration, when, over a one-day period, it transitions to the next "
            "month contract to expire as its benchmark contract and keeps that contract as "
            "its benchmark until it becomes the near month contract and close to expiration.",
        ))),
    "UNG": Fund(
        "UNG", "United States Natural Gas Fund", "NG", _PAGE + "ung", _PAGE + "holdings/ung",
        _ALPS_CONTENT + "united-states-natural-gas-fund-pro-20260424.pdf", "2026-04-24",
        "UNG's Benchmark Futures Contract is the futures contract on natural gas as traded "
        "on the NYMEX that is the near month contract to expire. If the near month contract "
        "is within two weeks of expiration, the Benchmark will be the next month contract "
        "to expire.",
        RollRule(4, _TWO_WEEKS, *_NEAR_TO_NEXT, (
            "UNG's Benchmark Futures Contract is such that every month it begins by using "
            "the near month contract to expire until the near month contract is within two "
            "weeks of expiration, when, over a four-day period, it transitions to the next "
            "month contract to expire as its benchmark contract and keeps that contract as "
            "its benchmark until it becomes the near month contract and close to expiration.",
        ))),
}


@dataclass(frozen=True)
class Window:
    fund: str
    begin: date
    end: date | None            # None when the CSV gives only a begin date

    @property
    def last_day(self) -> date:
        return self.end or self.begin

    def status(self, today: date) -> str:
        if today < self.begin:
            return "upcoming"
        return "in_progress" if today <= self.last_day else "completed"

    def as_dict(self, today: date) -> dict:
        return {"begin": self.begin.isoformat(),
                "end": self.end.isoformat() if self.end else None,
                "status": self.status(today)}


# ---------- parsing (pure) ----------

def page_text(body: str) -> str:
    """Visible text of an HTML page, whitespace collapsed, apostrophes normalized."""
    body = re.sub(r"<(script|style)\b.*?</\1>", " ", body, flags=re.S | re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", body)).replace("’", "'")
    return " ".join(text.split())


def holdings_rows_present(body: str) -> bool:
    """True if the static holdings table body has any row."""
    m = re.search(r'<table id="holdings-table"[^>]*>.*?<tbody>(.*?)</tbody>', body, re.S)
    return bool(m and "<tr" in m.group(1))


def _csv_date(s: str) -> date | None:
    s = s.strip()
    return datetime.strptime(s, "%d-%b-%y").date() if s else None


def parse_roll_dates(text: str) -> list[Window]:
    """Rows `FUND,Roll Begin,Roll End` of the USCF roll-dates CSV."""
    out = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2 or not re.fullmatch(r"[A-Z]{2,5}", row[0].strip()):
            continue
        try:
            begin = _csv_date(row[1])
            end = _csv_date(row[2]) if len(row) > 2 else None
        except ValueError:
            continue
        if begin:
            out.append(Window(row[0].strip(), begin, end))
    return out


def roll_target(windows: list[Window], today: date) -> tuple[Window | None, Window | None]:
    """(this month's window, the window whose roll is current or next)."""
    this_month = next((w for w in windows
                       if (w.begin.year, w.begin.month) == (today.year, today.month)), None)
    ahead = sorted((w for w in windows if w.last_day >= today), key=lambda w: w.begin)
    return this_month, (ahead[0] if ahead else None)


def roll_legs(rows: list[dict], begin: date) -> tuple[dict, dict] | None:
    """(sold, bought) curve rows: the near month at `begin` and the month after it.

    `rows` are futures.curve() months (unexpired, delivery order). Valid while
    the roll has not ended: the contract rolled out of expires after its roll.
    """
    for i, row in enumerate(rows[:-1]):
        if row["expiry"] and date.fromisoformat(row["expiry"]) >= begin:
            return row, rows[i + 1]
    return None


def roll_yield(sold: float | None, bought: float | None) -> float | None:
    if sold is None or not bought:
        return None
    return (sold - bought) / bought


# ---------- fetching ----------

@dataclass
class _Reads:
    """Fetch results, not_verified lines and snapshot notes across one call."""

    not_verified: list[dict]
    warnings: list[str]
    from_snapshot: bool = False

    def get(self, url: str, item: str) -> http.Fetched | None:
        try:
            got = http.get(url, max_age_hours=PAGE_MAX_AGE_HOURS)
        except (http.FetchError, snap.OfflineMiss) as e:
            self.not_verified.append(unverified(item, f"fetch failed: {e}", url))
            return None
        self.from_snapshot = self.from_snapshot or got.from_snapshot
        if got.note:
            self.warnings.append(got.note)
        return got


def _fund(code: str) -> Fund:
    try:
        return FUNDS[code.upper()]
    except KeyError:
        raise ValueError(f"unknown fund {code!r}; expected one of {sorted(FUNDS)}") from None


def _windows(fund: Fund, year: int, reads: _Reads) -> tuple[list[Window], http.Fetched | None]:
    url = ROLL_DATES_URL.format(year=year)
    got = reads.get(url, f"USCF roll dates {year}")
    if got is None:
        return [], None
    windows = [w for w in parse_roll_dates(got.text()) if w.fund == fund.code]
    if not windows:
        reads.not_verified.append(unverified(f"{fund.code} roll dates {year}",
                                     "no rows for this fund in the roll-dates CSV", url))
    return windows, got


def _rule(fund: Fund, reads: _Reads) -> dict | None:
    """The encoded rule, re-checked against the fund page's benchmark sentence."""
    got = reads.get(fund.page_url, f"{fund.code} fund page")
    if got is None:
        page_check = "unavailable"
        reads.warnings.append(f"{fund.code} fund page unavailable; rule rests on the "
                              f"prospectus read on {READ_ON}")
    elif fund.page_sentence in page_text(got.text()):
        page_check = "matches"
    else:
        reads.not_verified.append(unverified(
            f"{fund.code} roll rule",
            f"the benchmark sentence read on {READ_ON} is no longer on the fund page; "
            "re-read the prospectus", fund.page_url))
        return None
    r = fund.rule
    return {"roll_days": r.roll_days, "window_starts": r.starts, "sells": r.sold,
            "buys": r.bought, "prospectus_url": fund.prospectus_url,
            "prospectus_date": fund.prospectus_date, "read_on": READ_ON,
            "evidence": list(r.evidence), "page_url": fund.page_url,
            "page_sentence": fund.page_sentence, "page_check": page_check}


def _expected_roll(fund: Fund, target: Window, today: date,
                   reads: _Reads) -> dict | None:
    env = futures.curve(fund.root)
    reads.from_snapshot = reads.from_snapshot or env["provenance"]["quality"] == "snapshot"
    if env["data"] is None:
        reads.not_verified.append(unverified(f"{fund.code} roll yield",
                                     f"{fund.root} futures curve unavailable",
                                     futures.ROOTS[fund.root].settlements_url))
        return None
    legs = roll_legs(env["data"]["months"], target.begin)
    if legs is None:
        reads.not_verified.append(unverified(f"{fund.code} roll yield",
                                     f"no {fund.root} contract expiring on/after "
                                     f"{target.begin} with a following month on the curve"))
        return None
    sold, bought = legs
    value = roll_yield(sold["settle"], bought["settle"])
    if value is None:
        reads.not_verified.append(unverified(f"{fund.code} roll yield",
                                     f"no settle for {sold['contract']} or {bought['contract']}"
                                     f" on {env['data']['curve_date']}",
                                     futures.ROOTS[fund.root].settlements_url))
    leg = ("contract", "delivery_month", "settle", "expiry")
    return {"window": target.as_dict(today),
            "sold": {k: sold[k] for k in leg}, "bought": {k: bought[k] for k in leg},
            "price_date": env["data"]["curve_date"], "price_source": futures.SOURCE,
            "price_note": fund.price_note,
            "roll_yield": value, "roll_yield_pct": None if value is None else value * 100,
            "basis": ROLL_YIELD_BASIS, "portion_rolled": None}


# ---------- public API ----------

def fund_holdings(fund: str) -> dict:
    """Contract months held and their proportions, from the USCF holdings page.

    Currently always not_verified: the page's table is rendered client-side
    from an API that needs a bearer token (see module docstring).
    data: None.
    """
    f = _fund(fund)
    reads = _Reads([], [])
    got = reads.get(f.holdings_url, f"{f.code} holdings")
    if got is None:
        return unsourced(f"{f.code} holdings", reads.not_verified[0]["reason"], f.holdings_url,
                      source="USCF holdings page")
    reason = ("the static page has an empty holdings table; rows are loaded in the browser "
              f"from {HOLDINGS_API_URL.format(fund=f.code)}, which returns HTTP 401 without "
              "the page's bearer token")
    if holdings_rows_present(got.text()):
        reason = "the holdings page now serves rows in static HTML; parser not implemented"
    return unsourced(f"{f.code} holdings", reason, f.holdings_url, source="USCF holdings page")


def roll_rule(fund: str) -> dict:
    """Roll rule with evidence, this month's window, and the expected roll yield.

    data: {fund, name, rule: {roll_days, window_starts, sells, buys,
    prospectus_url, prospectus_date, read_on, evidence, page_url,
    page_sentence, page_check} | None, this_month_window: {begin, end,
    status} | None, expected_roll: {window, sold, bought, price_date,
    price_source, price_note, roll_yield, roll_yield_pct, basis,
    portion_rolled} | None}.
    expected_roll is this month's roll while it is upcoming or in progress,
    otherwise the next published window.
    """
    f = _fund(fund)
    today = snap.asof_date()
    reads = _Reads([], [])
    rule = _rule(f, reads)
    windows, got = _windows(f, today.year, reads)
    this_month, target = roll_target(windows, today)
    if got is not None and target is None:
        more, _ = _windows(f, today.year + 1, reads)
        target = roll_target(more, today)[1]
    if got is not None and this_month is None:
        reads.not_verified.append(unverified(f"{f.code} roll window {today:%Y-%m}",
                                     "no row for this month in the roll-dates CSV", got.url))
    expected = None
    if rule is not None and target is not None:
        expected = _expected_roll(f, target, today, reads)
    elif got is not None:
        reads.not_verified.append(unverified(f"{f.code} next roll window",
                                     "no published window on or after today", got.url))
    reads.not_verified.append(unverified(
        f"{f.code} portion rolled", "fund holdings are not verified (see fund_holdings())",
        f.holdings_url))
    data = {"fund": f.code, "name": f.name, "rule": rule,
            "this_month_window": this_month.as_dict(today) if this_month else None,
            "expected_roll": expected}
    prov = provenance(source=SOURCE, source_url=ROLL_DATES_URL.format(year=today.year),
                      quality="snapshot" if reads.from_snapshot else "live",
                      prospectus_url=f.prospectus_url)
    return envelope(data, prov=prov, warnings=reads.warnings, not_verified=reads.not_verified)


def roll_events(funds: Iterable[str]) -> list[dict]:
    """Published roll-window begin/end dates from this month on, as calendar events.

    Funds whose roll-dates CSV cannot be read contribute no events.
    """
    today = snap.asof_date()
    first = today.replace(day=1)
    events = []
    for code in funds:
        f = _fund(code)
        reads = _Reads([], [])
        windows, got = _windows(f, today.year, reads)
        for w in windows:
            if w.begin < first:
                continue
            span = f"{w.begin} to {w.end}" if w.end else f"{w.begin}, one day"
            notes = (f"{f.rule.roll_days}-day roll, {f.rule.sold} -> {f.rule.bought} "
                     f"(prospectus {f.prospectus_date})")
            events.append(event(w.begin, f"{f.code} roll window begins ({span})",
                                source_url=got.url, verified=True,
                                fetched_at=got.fetched_at, notes=notes))
            if w.end and w.end != w.begin:
                events.append(event(w.end, f"{f.code} roll window ends ({span})",
                                    source_url=got.url, verified=True,
                                    fetched_at=got.fetched_at, notes=notes))
    return sorted(events, key=lambda e: (e["date"], e["event"]))
