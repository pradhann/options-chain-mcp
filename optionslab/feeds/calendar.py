"""Dated events the trader sizes against, each traceable to the page it came from.

Every event, from every producer (including the futures / ETF-roll events a
caller merges in through `events_between(extra_events=...)`), has one shape:

    {"date": "YYYY-MM-DD", "event": str, "source_url": str,
     "verified": bool, "fetched_at": ISO timestamp, "notes": str | None}

A date the tool cannot read from a source is not emitted; the envelope
carries a `not_verified` line with the URL instead. Nothing is inferred from
"usually" rules. Sources and what was checked live on 2026-09-21:

  EIA WPSR   https://www.eia.gov/petroleum/supply/weekly/schedule.php
             The page states the standard release ("10:30 a.m. eastern time
             on Wednesdays") and tabulates holiday exceptions by data week.
             Release dates are produced only for the weeks that table spans
             (first to last listed week ending); later weeks are not emitted
             because the page has not yet said whether they are shifted.
  EIA STEO   https://www.eia.gov/outlooks/steo/release_schedule.php
             Table: Issue | Release date (MM/DD/YYYY) | Notes.
  CFTC COT   https://www.cftc.gov/MarketReports/CommitmentsofTraders/ReleaseSchedule/index.htm
             "YYYY Release Schedule" tables: month, then day cells; "*" marks
             a holiday-delayed release. The page calls the schedule tentative.
  FOMC       https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
             One panel per year; the event date is the last day of the meeting.
  OPEC/OPEC+ https://www.opec.org/ (press-releases.html returns 403 to
             scripts; the home page and the pr-detail pages it links return
             200). Meeting dates are read from sentences such as "The next
             meeting will be held on 4 October 2026." in those releases.
  Earnings   yfinance `Ticker.calendar` / `Ticker.info` (each read is stored
             with storage.snapshots.write_json and replayed offline) and
             Nasdaq https://api.nasdaq.com/api/analyst/{SYM}/earnings-date
             (needs browser-like Accept/Origin/Referer headers), plus an
             optional company IR page from config key `ir_calendar_urls`.
             verified=True only when two sources give the same single date
             and at least one of them does not flag it as an estimate.
  Ex-div     yfinance `info.exDividendDate` / `lastDividendValue`; second
             source Nasdaq https://api.nasdaq.com/api/quote/{SYM}/dividends
             ?assetclass=stocks, which serves Nasdaq-listed symbols only.

Dates are calendar dates in the source's own terms (US Eastern for all of
the above); yfinance epoch fields are converted as UTC midnight, which is how
they match Nasdaq's ex-dividend dates.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from typing import Any

from ..storage import snapshots as snap
from . import config, http
from .envelope import envelope, iso, provenance, unverified

WPSR_URL = "https://www.eia.gov/petroleum/supply/weekly/schedule.php"
STEO_URL = "https://www.eia.gov/outlooks/steo/release_schedule.php"
COT_URL = "https://www.cftc.gov/MarketReports/CommitmentsofTraders/ReleaseSchedule/index.htm"
FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
OPEC_HOME_URL = "https://www.opec.org/"
NASDAQ_EARNINGS_URL = "https://api.nasdaq.com/api/analyst/{sym}/earnings-date"
NASDAQ_DIVIDENDS_URL = "https://api.nasdaq.com/api/quote/{sym}/dividends?assetclass=stocks"
YAHOO_QUOTE_URL = "https://finance.yahoo.com/quote/{sym}/"

# Without these Nasdaq's API times out (verified 2026-09-21).
NASDAQ_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}
OPEC_MAX_RELEASES = 6
EVENT_KEYS = ("date", "event", "source_url", "verified", "fetched_at", "notes")

_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_MONTH_NAMES = ("January|February|March|April|May|June|July|August|September|"
                "October|November|December")
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


# ---------- event shape ----------

def event(day: date | str, name: str, *, source_url: str, verified: bool,
          fetched_at: str, notes: str | None = None) -> dict:
    """One calendar event in the shared shape."""
    return {"date": day if isinstance(day, str) else day.isoformat(), "event": name,
            "source_url": source_url, "verified": verified, "fetched_at": fetched_at,
            "notes": notes}


@dataclass
class _Collector:
    """Events, not_verified lines and snapshot warnings gathered across fetches."""

    events: list[dict] = field(default_factory=list)
    not_verified: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    from_snapshot: bool = False

    def fetch(self, url: str, item: str, headers: dict | None = None) -> http.Fetched | None:
        try:
            got = http.get(url, headers=headers)
        except (http.FetchError, snap.OfflineMiss) as e:
            self.not_verified.append(unverified(item, f"fetch failed: {e}", url))
            return None
        self.note_snapshot(got.from_snapshot, got.note)
        return got

    def note_snapshot(self, from_snapshot: bool, note: str | None = None) -> None:
        self.from_snapshot = self.from_snapshot or from_snapshot
        if note:
            self.warnings.append(note)

    def add(self, rows: Iterable[dict], got: http.Fetched) -> None:
        self.events.extend(
            event(r["date"], r["event"], source_url=got.url, verified=True,
                  fetched_at=got.fetched_at, notes=r.get("notes"))
            for r in rows)

    def envelope(self, data: Any, source: str, source_url: str | None = None) -> dict:
        prov = provenance(source=source, source_url=source_url,
                          quality="snapshot" if self.from_snapshot else "live")
        return envelope(data, prov=prov, warnings=self.warnings,
                        not_verified=self.not_verified)


# ---------- HTML helpers (pure) ----------

def _squash(s: str) -> str:
    return " ".join(s.split())


def _strip_tags(s: str) -> str:
    return _squash(html.unescape(re.sub(r"<[^>]+>", " ", s)))


class _Blocks(HTMLParser):
    """Document-order stream of ("heading", text) and ("row", [cell texts])."""

    _HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[tuple[str, Any]] = []
        self._head: list[str] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self._HEADINGS:
            self._head = []
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in self._HEADINGS and self._head is not None:
            self.items.append(("heading", _squash("".join(self._head))))
            self._head = None
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(_squash("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.items.append(("row", self._row))
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)
        if self._head is not None:
            self._head.append(data)


def _blocks(text: str) -> list[tuple[str, Any]]:
    p = _Blocks()
    p.feed(text)
    p.close()
    return p.items


def _long_date(s: str) -> date | None:
    """'September 4, 2026' -> date."""
    try:
        return datetime.strptime(_squash(s), "%B %d, %Y").date()
    except ValueError:
        return None


# ---------- official schedule parsers (pure: page text in, rows out) ----------

def parse_wpsr_schedule(text: str) -> tuple[list[dict], date | None]:
    """WPSR release dates for every week the holiday table spans.

    Returns (rows, last_week_ending). Each row is {"date", "event", "notes"}.
    The standard weekday and time come from the page's own sentence; a week
    with no listed exception is released on that weekday after the week
    ending. Nothing is produced if the standard-rule sentence is missing.
    """
    rule = re.search(r"standard release time and day of the week will be at\s+"
                     r"([\d:]+\s*[ap]\.m\.)\s+eastern time on\s+(\w+?)s\b",
                     _strip_tags(text), re.I)
    exceptions = {}
    for kind, cells in _blocks(text):
        if kind != "row" or len(cells) < 5:
            continue
        week, alt = _long_date(cells[0]), _long_date(cells[1])
        if week and alt:
            exceptions[week] = (alt, f"{cells[3]} ET {cells[2]}; holiday: {cells[4]}")
    if rule is None or not exceptions:
        return [], None
    std_time, weekday = rule.group(1), _WEEKDAYS.index(rule.group(2).lower())
    first, last = min(exceptions), max(exceptions)
    rows = []
    week = first
    while week <= last:
        if week in exceptions:
            release, notes = exceptions[week]
        else:
            release = week + timedelta(days=(weekday - week.weekday() - 1) % 7 + 1)
            notes = f"{std_time} ET (standard release; no exception listed for this week)"
        rows.append({"date": release, "event": f"EIA WPSR release (week ending {week})",
                     "notes": notes})
        week += timedelta(days=7)
    return rows, last


def parse_steo_schedule(text: str) -> list[dict]:
    """STEO release dates from the Issue | Release date | Notes table."""
    rows = []
    for kind, cells in _blocks(text):
        if kind != "row" or len(cells) < 2:
            continue
        m = re.match(r"(\d{2})/(\d{2})/(\d{4})\s*(.*)", cells[1])
        if not m:
            continue
        mm, dd, yyyy, extra = m.groups()
        notes = "; ".join(x for x in (extra, *cells[2:]) if x) or None
        rows.append({"date": date(int(yyyy), int(mm), int(dd)),
                     "event": f"EIA STEO release ({cells[0]} issue)", "notes": notes})
    return rows


def parse_cot_schedule(text: str) -> list[dict]:
    """COT release dates from each "YYYY Release Schedule" table."""
    time_m = re.search(r"released at\s+([\d:]+\s*[ap]\.m\.)\s+Eastern", _strip_tags(text), re.I)
    base_note = f"{time_m.group(1)} ET; " if time_m else ""
    base_note += "schedule marked tentative by CFTC"
    rows, year = [], None
    for kind, item in _blocks(text):
        if kind == "heading":
            m = re.match(r"(\d{4}) Release Schedule", item)
            year = int(m.group(1)) if m else None
            continue
        month = _MONTHS.get(item[0][:3].lower()) if year and item else None
        if month is None:
            continue
        for cell in item[1:]:
            m = re.fullmatch(r"(\d{1,2})(\*?)", cell)
            if not m:
                continue
            notes = base_note + ("; delayed by a federal holiday (* on page)" if m.group(2) else "")
            rows.append({"date": date(year, month, int(m.group(1))),
                         "event": "CFTC Commitments of Traders release", "notes": notes})
    return rows


def parse_fomc_calendar(text: str) -> list[dict]:
    """FOMC meetings; the event date is the meeting's last day."""
    heads = list(re.finditer(r"<h4><a[^>]*>(\d{4}) FOMC Meetings</a></h4>", text))
    rows = []
    for i, head in enumerate(heads):
        year = int(head.group(1))
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        for m in re.finditer(r'fomc-meeting__month[^>]*><strong>([^<]+)</strong></div>\s*'
                             r'<div class="fomc-meeting__date[^>]*>([^<]+)</div>',
                             text[head.end():end]):
            row = _fomc_row(year, m.group(1), html.unescape(m.group(2)))
            if row:
                rows.append(row)
    return rows


def _fomc_row(year: int, month_txt: str, days_txt: str) -> dict | None:
    months = [_MONTHS.get(p.strip()[:3].lower()) for p in month_txt.split("/")]
    days = re.match(r"\s*(\d{1,2})(?:-(\d{1,2}))?(\*?)\s*(.*)", days_txt)
    if not days or None in months:
        return None
    first, last, star, extra = days.groups()
    notes = [f"meeting {_squash(month_txt)} {first}" + (f"-{last}" if last else "")]
    if star:
        notes.append("Summary of Economic Projections (* on page)")
    if extra.strip():
        notes.append(extra.strip().strip("()"))
    return {"date": date(year, months[-1], int(last or first)), "event": "FOMC decision",
            "notes": "; ".join(notes)}


def parse_opec_home_links(text: str) -> list[str]:
    """Absolute URLs of the press releases linked from opec.org, page order."""
    out: list[str] = []
    for href in re.findall(r'href="\.?/?(pr-detail/[^"]+\.html)"', text):
        url = OPEC_HOME_URL + href
        if url not in out:
            out.append(url)
    return out


def parse_opec_release(text: str) -> list[dict]:
    """Meeting dates announced in an OPEC press release ("next meeting ... D Month YYYY")."""
    head = re.search(r'id="articleHeadline"[^>]*>(.*?)</', text, re.S)
    body = re.search(r'id="articleContent"[^>]*>(.*?)</section>', text, re.S)
    if not body:
        return []
    headline = _strip_tags(head.group(1)) if head else None
    rows = []
    for sentence in re.split(r"(?<=\.)\s+", _strip_tags(body.group(1))):
        if not re.search(r"\bnext meeting\b", sentence, re.I):
            continue
        m = re.search(rf"\b(\d{{1,2}} (?:{_MONTH_NAMES}) \d{{4}})\b", sentence)
        if not m:
            continue
        jmmc = re.search(r"\bJMMC\b(?:\s*\((\d+\w*)\))?", sentence)
        name = "OPEC+ JMMC meeting" if jmmc else "OPEC+ meeting"
        if jmmc and jmmc.group(1):
            name += f" ({jmmc.group(1)})"
        notes = f'"{sentence}"' + (f" (release: {headline})" if headline else "")
        rows.append({"date": datetime.strptime(m.group(1), "%d %B %Y").date(),
                     "event": name, "notes": notes})
    return rows


# ---------- macro calendar ----------

def _macro(col: _Collector) -> None:
    got = col.fetch(WPSR_URL, "EIA WPSR release schedule")
    if got:
        rows, last_week = parse_wpsr_schedule(got.text())
        col.add(rows, got)
        if last_week:
            col.not_verified.append(unverified(
                f"EIA WPSR releases for weeks ending after {last_week}",
                "schedule page lists holiday exceptions only through this week", WPSR_URL))
        else:
            col.not_verified.append(unverified("EIA WPSR release schedule",
                                       "standard rule or holiday table not found", WPSR_URL))
    for url, item, parser in ((STEO_URL, "EIA STEO release schedule", parse_steo_schedule),
                              (COT_URL, "CFTC COT release schedule", parse_cot_schedule),
                              (FOMC_URL, "FOMC meeting calendar", parse_fomc_calendar)):
        got = col.fetch(url, item)
        if got:
            rows = parser(got.text())
            col.add(rows, got)
            if not rows:
                col.not_verified.append(unverified(item, "page fetched but no dates parsed", url))
    _opec(col)


def _opec(col: _Collector) -> None:
    item = "OPEC / OPEC+ meeting dates"
    home = col.fetch(OPEC_HOME_URL, item)
    if home is None:
        return
    found = 0
    for url in parse_opec_home_links(home.text())[:OPEC_MAX_RELEASES]:
        got = col.fetch(url, f"OPEC press release {url}")
        if got:
            rows = parse_opec_release(got.text())
            found += len(rows)
            col.add(rows, got)
    if not found:
        col.not_verified.append(unverified(item, "no meeting date stated in the press releases "
                                         "linked from the OPEC home page", OPEC_HOME_URL))


def macro_calendar() -> dict:
    """Upcoming WPSR, STEO, COT, FOMC and OPEC/OPEC+ dates (today ET onward).

    data: {"events": [event, ...] sorted by date}.
    """
    col = _Collector()
    _macro(col)
    today = snap.today_et().isoformat()
    upcoming = sorted((e for e in col.events if e["date"] >= today),
                      key=lambda e: (e["date"], e["event"]))
    return col.envelope({"events": upcoming}, "EIA, CFTC, Federal Reserve, OPEC schedule pages")


# ---------- yfinance (wrapped, snapshotted) ----------

def _epoch_date(v: Any) -> date | None:
    if isinstance(v, (int, float)) and v > 0:
        return datetime.fromtimestamp(v, UTC).date()
    return None


def yfinance_fields(calendar: Any, info: Any) -> dict:
    """The yfinance fields this module uses, JSON-safe (pure)."""
    cal = calendar if isinstance(calendar, dict) else {}
    info = info if isinstance(info, dict) else {}
    ex = _epoch_date(info.get("exDividendDate"))
    same_event = ex is not None and _epoch_date(info.get("lastDividendDate")) == ex
    return {
        "earnings_dates": [d.isoformat() for d in cal.get("Earnings Date") or []
                           if isinstance(d, date)],
        "earnings_is_estimate": info.get("isEarningsDateEstimate"),
        "ex_dividend_date": ex.isoformat() if ex else None,
        "last_dividend_value": info.get("lastDividendValue") if same_event else None,
    }


def _yfinance(sym: str, col: _Collector) -> dict | None:
    """Live yfinance read written to snapshots; offline or on error, the latest snapshot."""
    failure = None
    if not snap.is_offline():
        try:
            import yfinance as yf

            t = yf.Ticker(sym)
            payload = yfinance_fields(t.calendar, t.info) | {"fetched_at": iso()}
            snap.write_json("yfinance", sym, "calendar", payload)
            return payload
        except Exception as e:  # yfinance raises many unrelated types on network/parse failure
            failure = f"yfinance read for {sym} failed ({e})"
    hit = snap.read_json("yfinance", sym, "calendar")
    if hit is None:
        col.not_verified.append(unverified(f"yfinance calendar {sym}", failure or "offline and no snapshot",
                                   YAHOO_QUOTE_URL.format(sym=sym)))
        return None
    col.note_snapshot(True, f"{failure}; served snapshot of {hit[1]}" if failure else None)
    return hit[0]


# ---------- earnings ----------

def parse_nasdaq_earnings(obj: Any) -> dict:
    """Nasdaq earnings-date JSON -> {"date", "timing", "is_estimate", "text"} (pure)."""
    text = _squash(((obj or {}).get("data") or {}).get("reportText") or "")
    m = re.search(r"report earnings on\s+(\d{2})/(\d{2})/(\d{4})\s*(before market open|"
                  r"after market close)?", text)
    return {
        "date": date(int(m.group(3)), int(m.group(1)), int(m.group(2))).isoformat() if m else None,
        "timing": m.group(4) if m else None,
        "is_estimate": "derived from an algorithm" in text,
        "text": text or None,
    }


def date_in_text(d: date, text: str) -> bool:
    """True when `d` appears in `text` in a common written form (pure)."""
    month, short = d.strftime("%B"), d.strftime("%b")
    forms = (rf"{month}\s+{d.day},?\s+{d.year}", rf"{short}\.?\s+{d.day},?\s+{d.year}",
             rf"{d.day}\s+{month}\s+{d.year}", rf"0?{d.month}/0?{d.day}/{d.year}",
             d.isoformat())
    return any(re.search(rf"\b{f}\b", text, re.I) for f in forms)


def _earnings_sources(sym: str, col: _Collector) -> list[dict]:
    """Each source's read: {"source", "url", "dates", "is_estimate", "detail"}."""
    out = []
    yf = _yfinance(sym, col)
    if yf is not None:
        out.append({"source": "yfinance", "url": YAHOO_QUOTE_URL.format(sym=sym),
                    "dates": yf["earnings_dates"],
                    "is_estimate": bool(yf["earnings_is_estimate"])
                    or len(yf["earnings_dates"]) > 1,
                    "detail": "window" if len(yf["earnings_dates"]) > 1 else None})
    url = NASDAQ_EARNINGS_URL.format(sym=sym)
    got = col.fetch(url, f"Nasdaq earnings date {sym}", headers=NASDAQ_HEADERS)
    if got:
        nas = parse_nasdaq_earnings(got.json())
        out.append({"source": "nasdaq", "url": got.url,
                    "dates": [nas["date"]] if nas["date"] else [],
                    "is_estimate": nas["is_estimate"], "detail": nas["timing"]})
    ir_url = (config.get("ir_calendar_urls") or {}).get(sym)
    if ir_url:
        got = col.fetch(ir_url, f"IR calendar {sym}")
        if got:
            out.append({"source": "ir_calendar", "url": got.url, "dates": [],
                        "is_estimate": False, "detail": None, "text": _strip_tags(got.text())})
    return out


def _agreeing(d: date, sources: list[dict]) -> list[dict]:
    """Sources that give exactly `d` (a yfinance window never counts as agreement)."""
    iso_d = d.isoformat()
    return [s for s in sources
            if (s["source"] == "ir_calendar" and date_in_text(d, s["text"]))
            or s["dates"] == [iso_d]]


def next_earnings_event(sym: str, sources: list[dict], today: date,
                        fetched_at: str) -> dict | None:
    """The earliest future candidate date as an event; verified per the module rule (pure)."""
    candidates = sorted({date.fromisoformat(x) for s in sources for x in s["dates"]
                         if date.fromisoformat(x) >= today})
    if not candidates:
        return None
    d = candidates[0]
    agree = _agreeing(d, sources)
    verified = len(agree) >= 2 and any(not s["is_estimate"] for s in agree)
    parts = []
    for s in sources:
        shown = ", ".join(s["dates"]) or ("date found on page" if s in agree else "no date")
        flags = [x for x in (s["detail"], "estimate" if s["is_estimate"] else None) if x]
        parts.append(f"{s['source']}: {shown}" + (f" ({'; '.join(flags)})" if flags else ""))
    if len(agree) >= 2 and not verified:
        parts.append("sources agree but every agreeing source flags the date as an estimate")
    elif len(agree) < 2:
        parts.append("sources do not agree; earliest candidate shown")
    return event(d, f"{sym} earnings", source_url=agree[0]["url"] if agree else sources[0]["url"],
                 verified=verified, fetched_at=fetched_at, notes="; ".join(parts))


def next_earnings(ticker: str) -> dict:
    """Next earnings date for `ticker`, cross-checked across sources.

    data: {"ticker", "next_earnings": event | None,
           "sources": [{"source", "url", "dates", "is_estimate", "detail"}]}
    """
    sym = ticker.upper()
    col = _Collector()
    sources = _earnings_sources(sym, col)
    ev = next_earnings_event(sym, sources, snap.today_et(), iso())
    if ev is None:
        col.not_verified.append(unverified(f"{sym} next earnings", "no source gives a future date",
                                   NASDAQ_EARNINGS_URL.format(sym=sym)))
    shown = [{k: v for k, v in s.items() if k != "text"} for s in sources]
    return col.envelope({"ticker": sym, "next_earnings": ev, "sources": shown},
                        "yfinance + Nasdaq (+ IR page if configured)")


# ---------- ex-dividend ----------

def parse_nasdaq_dividends(obj: Any) -> dict:
    """Nasdaq dividends JSON -> {"rows": [{"ex_date", "amount", "declaration_date",
    "payment_date"}], "message"} (pure)."""
    data = (obj or {}).get("data") or {}
    rows = []
    for r in ((data.get("dividends") or {}).get("rows") or []):
        ex = _mdy(r.get("exOrEffDate"))
        if ex is None:
            continue
        amount = re.sub(r"[^\d.]", "", r.get("amount") or "")
        rows.append({"ex_date": ex, "amount": float(amount) if amount else None,
                     "declaration_date": _mdy(r.get("declarationDate")),
                     "payment_date": _mdy(r.get("paymentDate"))})
    return {"rows": rows, "message": (obj or {}).get("message")}


def _mdy(s: Any) -> str | None:
    try:
        return datetime.strptime(str(s), "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def ex_dividend_event(sym: str, yf: dict | None, nasdaq: dict | None, *,
                      yf_fetched_at: str | None, nasdaq_url: str) -> dict | None:
    """Latest declared ex-dividend date as an event (pure). verified needs Nasdaq agreement."""
    ex = (yf or {}).get("ex_dividend_date")
    if not ex:
        return None
    amount = yf.get("last_dividend_value")
    notes = [f"yfinance info.exDividendDate {ex}",
             f"amount {amount} per share (yfinance info.lastDividendValue)" if amount is not None
             else "amount: yfinance lastDividendDate differs from exDividendDate; not shown"]
    match = None
    if nasdaq is None:
        notes.append("second source: Nasdaq dividend history not fetched")
    elif not nasdaq["rows"]:
        notes.append(f"second source unavailable: Nasdaq says {nasdaq['message'] or 'no rows'}")
    else:
        match = next((r for r in nasdaq["rows"] if r["ex_date"] == ex), None)
        if match is None:
            notes.append(f"Nasdaq dividend history has no ex-date {ex} "
                         f"(latest {nasdaq['rows'][0]['ex_date']})")
        elif amount is not None and match["amount"] is not None and match["amount"] != amount:
            notes.append(f"Nasdaq agrees on the date but gives amount {match['amount']}")
            match = None
        else:
            notes.append(f"Nasdaq dividend history agrees (amount {match['amount']}, "
                         f"declared {match['declaration_date']}, paid {match['payment_date']})")
    return event(ex, f"{sym} ex-dividend", source_url=nasdaq_url if match
                 else YAHOO_QUOTE_URL.format(sym=sym), verified=match is not None,
                 fetched_at=yf_fetched_at or iso(), notes="; ".join(notes))


def _ex_dividend(sym: str, col: _Collector) -> dict | None:
    yf = _yfinance(sym, col)
    url = NASDAQ_DIVIDENDS_URL.format(sym=sym)
    got = col.fetch(url, f"Nasdaq dividend history {sym}", headers=NASDAQ_HEADERS)
    nasdaq = parse_nasdaq_dividends(got.json()) if got else None
    return ex_dividend_event(sym, yf, nasdaq, yf_fetched_at=yf and yf.get("fetched_at"),
                             nasdaq_url=url)


def ex_dividend(ticker: str) -> dict:
    """Latest declared ex-dividend date for `ticker` with amount and a second-source check.

    data: {"ticker", "ex_dividend": event | None, "upcoming": bool | None}
    """
    sym = ticker.upper()
    col = _Collector()
    ev = _ex_dividend(sym, col)
    if ev is None:
        col.not_verified.append(unverified(f"{sym} ex-dividend date", "no declared ex-dividend date",
                                   YAHOO_QUOTE_URL.format(sym=sym)))
    upcoming = ev["date"] >= snap.today_et().isoformat() if ev else None
    return col.envelope({"ticker": sym, "ex_dividend": ev, "upcoming": upcoming},
                        "yfinance + Nasdaq dividend history")


# ---------- merge ----------

def _as_date(d: date | str) -> str:
    return d if isinstance(d, str) else d.isoformat()


def events_between(start: date | str, end: date | str, *, tickers: Iterable[str] = (),
                   extra_events: Iterable[dict] = ()) -> dict:
    """Every event dated in [start, end], sorted by date.

    Merges the macro calendar, each ticker's next earnings and latest declared
    ex-dividend, and `extra_events` (same shape; e.g. futures expiries, first
    notice days, ETF roll windows). An extra event missing a key is reported
    in not_verified, not emitted.

    data: {"start", "end", "events": [event, ...]}
    """
    lo, hi = _as_date(start), _as_date(end)
    col = _Collector()
    _macro(col)
    for t in tickers:
        sym = t.upper()
        sources = _earnings_sources(sym, col)
        ev = next_earnings_event(sym, sources, snap.today_et(), iso())
        col.events.extend(e for e in (ev, _ex_dividend(sym, col)) if e)
    for e in extra_events:
        missing = [k for k in EVENT_KEYS if k not in e]
        if missing:
            col.not_verified.append(unverified(str(e.get("event")), f"extra event missing {missing}",
                                       e.get("source_url")))
        else:
            col.events.append({k: e[k] for k in EVENT_KEYS})
    inside = sorted((e for e in col.events if lo <= _as_date(e["date"]) <= hi),
                    key=lambda e: (_as_date(e["date"]), e["event"]))
    return col.envelope({"start": lo, "end": hi, "events": inside}, "optionslab calendar")
