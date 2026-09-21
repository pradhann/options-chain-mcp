"""Capital-structure sweep from SEC EDGAR (item 8).

What: for a US-listed ticker, the filings that describe its capital
structure and the words in them that matter for an options position:

  * `recent_filings(ticker)` periodic reports, 8-K/6-K current reports with
                             their item numbers, 424B/SUPPL prospectuses.
  * `insider_trades(ticker)` Form 4 transactions (code, shares, price, dates,
                             owner) parsed from each filing's XML.
  * `share_count_trend(ticker)` dei:EntityCommonStockSharesOutstanding.
  * `structural_sweep(ticker)` one line per keyword plus insider, ATM/424B and
                             share-count lines, each verified | not_verified.
  * `add_manual_entry(...)`  a finding the user read in a document EDGAR does
                             not carry (SEDAR+, SEDI), stored with its reference.

Why: dilution (converts, warrants, ATMs), hedging (collars, swaps, fixed-
price, hedged production) and capital return (variable dividends,
buybacks) change what a stock can do; each claim here points to the filing
it came from so it can be re-read.

Sources (all probed live 2026-09-21):
  * Ticker -> CIK: https://www.sec.gov/files/company_tickers.json
    (class shares use "-", e.g. BRK-B).
  * Submissions: https://data.sec.gov/submissions/CIK##########.json
    (`filings.recent` columns: form, filingDate, reportDate, accessionNumber,
    items "5.02,7.01,9.01", primaryDocument; older pages in `filings.files`).
  * Full-text search: https://efts.sec.gov/LATEST/search-index with
    q (quoted phrases, "a" OR "b" is a should-match), ciks (10-digit),
    forms (comma list, matched on root form), dateRange=custom, startdt,
    enddt, from (page offset; 100 hits per page, ordered by score, not date).
    Phrases are NOT stemmed ("warrant" 3 hits vs "warrants" 7 for VLO), so
    each keyword carries its explicit variants. Hyphens tokenise like spaces
    ("at-the-market" and "at the market" both 210 hits for MSTR).
  * XBRL: https://data.sec.gov/api/xbrl/companyconcept/CIK##########/dei/
    EntityCommonStockSharesOutstanding.json — the companyfacts API narrowed
    to one concept (same fact rows: end, val, accn, form, filed). Filers that
    tag shares per class with dimensions (MSTR) have no undimensioned fact
    and get a 404; that is reported not_verified, not substituted.
  * Documents: https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{file}.
    Form 4 XML is the primaryDocument without its "xslF345X06/" prefix.
  * SEC requires a "Name email" User-Agent (http.py reads OPTIONSLAB_SEC_UA).
    www.sec.gov answered 403 to the default UA on 2026-09-21; results carry a
    warning whenever the variable is unset.

Issuer regime, read from the submissions JSON rather than a list:
  * foreign private issuer: the latest periodic report is a 20-F or 40-F
    (or, with none on file, the issuer furnishes 6-Ks).
  * Canadian: the latest periodic report is a 40-F (MJDS), or the
    incorporation / business address description names Canada
    (e.g. "Alberta, Canada").

Filing set searched by the sweep (all from submissions, so it is listed):
  * periodic: the last 8 of 10-K/10-Q (domestic) or the last 2 of 20-F/40-F
    (annual-only foreign filers: two annual reports span the same ~2 years),
    plus amendments filed since the oldest of them. That oldest filing date
    is the window start.
  * current: 8-K/8-K/A/6-K filed in the last 180 days.
  * prospectus: 424B* and SUPPL filed since the window start.
Each keyword runs two search-index queries (periodic + prospectus forms over
the window; current forms over 180 days) and keeps only hits whose accession
is in the set, so an out-of-window 8-K never counts. Hits are document-level
(primary document or exhibit). EFTS intermittently answers HTTP 500 to a cold
query that succeeds a second later, so pages are retried twice with a delay.

"Verified" means, per line:
  * keyword: the full-text search over the filing set completed (every result
    page read). A hit is verified with up to 2 excerpts (400 characters each
    side) from each of the 3 most recent hit documents, each with its URL.
    No hit is a verified absence for domestic and non-Canadian foreign
    filers, and the searched filings are listed so it can be audited. For a
    Canadian issuer no hit is not_verified with the SEDAR+ URL: EDGAR absence
    is not evidence when the primary filings are on SEDAR+. EDGAR hits in
    40-F/6-K are still reported as evidence.
  * insider: every Form 4 in the last 90 days was fetched and parsed. Foreign
    private issuers are exempt from Section 16, so their line is not_verified
    (Canadian: with the SEDI URL); any Form 4 rows found are still shown.
  * ATM/424B: the prospectus list from submissions, flagged where the
    at-the-market search hit. Canadian with none on EDGAR -> not_verified.
  * share count: at least one dei:EntityCommonStockSharesOutstanding fact.
  * a manual entry with a document_ref upgrades a not_verified line; one
    without a document_ref stays not_verified.

SEDAR+ and SEDI have no API and block scripted clients (HTTP 403 to curl on
2026-09-21). In a real browser the search pages below load, but they open a
session-bound search form: query-string prefill of an issuer is not
supported, so the line gives the page plus the exact EDGAR issuer name to type.

Cache ages (http.py snapshots every body): tickers 24 h, submissions,
search and XBRL 12 h, filing documents 7 days (immutable once filed).
"Today" is snapshots.asof_date() so offline replays are reproducible.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
from dataclasses import dataclass, field
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

from ..storage import snapshots as snap
from . import http
from .envelope import envelope, iso, provenance, unsourced, unverified

SOURCE = "SEC EDGAR"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"
SHARES_URL = ("https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/dei/"
              "EntityCommonStockSharesOutstanding.json")
EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{accession}-index.htm"
BROWSE_URL = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
              "&CIK={cik:010d}&type={form}&dateb=&owner=include&count=40")
SEDAR_SEARCH_URL = ("https://www.sedarplus.ca/csa-party/service/create.html"
                    "?targetAppCode=csa-party&service=searchDocuments&_locale=en")
SEDI_ISSUER_URL = "https://www.sedi.ca/sedi/SVTItdSelectIssuer?locale=en_CA"

TICKERS_MAX_AGE_H = 24.0
INDEX_MAX_AGE_H = 12.0
DOCUMENT_MAX_AGE_H = 24.0 * 7

DOMESTIC_PERIODIC = ("10-K", "10-Q")
FOREIGN_PERIODIC = ("20-F", "40-F")
DOMESTIC_PERIODIC_COUNT = 8
FOREIGN_PERIODIC_COUNT = 2
CURRENT_FORMS = frozenset({"8-K", "8-K/A", "6-K", "6-K/A"})
CURRENT_DAYS = 180
TRACKED_8K_ITEMS = ("1.05", "2.02", "5.02", "7.01")
INSIDER_FORMS = frozenset({"4", "4/A"})
INSIDER_DAYS = 90
INSIDER_MAX_FILINGS = 100

# keyword -> phrase variants searched (EFTS does not stem).
KEYWORDS: dict[str, tuple[str, ...]] = {
    "convertible": ("convertible",),
    "conversion price": ("conversion price",),
    "warrant": ("warrant", "warrants"),
    "at-the-market": ("at-the-market",),
    "collar": ("collar", "collars"),
    "swap": ("swap", "swaps"),
    "fixed-price": ("fixed-price",),
    "hedged production": ("hedged production",),
    "variable dividend": ("variable dividend", "variable dividends"),
    "share repurchase": ("share repurchase", "share repurchases"),
}
INSIDER_ITEM = "insider_form4"
ATM_ITEM = "atm_424b"
SHARES_ITEM = "share_count"
SWEEP_ITEMS = (*KEYWORDS, INSIDER_ITEM, ATM_ITEM, SHARES_ITEM)

EXCERPT_WIDTH = 400
EXCERPTS_PER_DOC = 2
DOCS_PER_KEYWORD = 3
EFTS_PAGE_SIZE = 100
EFTS_MAX_PAGES = 5
EFTS_RETRY_DELAYS = (1.0, 3.0)

MANUAL_DIR = "manual"
MANUAL_FILE = "sweep.json"

UA_WARNING = ("OPTIONSLAB_SEC_UA is unset: SEC asks for a 'Name email' User-Agent "
              "and www.sec.gov refuses the default one (HTTP 403).")


# ---------- fetch bookkeeping ----------

@dataclass
class _Reads:
    """Every body a call used, so provenance can say live vs snapshot."""
    fetched: dict[str, http.Fetched] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def get(self, url: str, max_age_hours: float) -> http.Fetched:
        """One read per URL per call; later uses reuse the body in memory."""
        if url in self.fetched:
            return self.fetched[url]
        got = http.get(url, max_age_hours=max_age_hours)
        self.fetched[url] = got
        if got.note:
            self.warnings.append(got.note)
        return got

    def prov(self, source_url: str | None) -> dict:
        snapshot = any(f.from_snapshot for f in self.fetched.values())
        asof = min((f.fetched_at for f in self.fetched.values()), default=iso())
        return provenance(source=SOURCE, source_url=source_url, asof=asof,
                          quality="snapshot" if snapshot else "live")

    def all_warnings(self) -> list[str]:
        out = [] if os.environ.get("OPTIONSLAB_SEC_UA") else [UA_WARNING]
        return out + list(dict.fromkeys(self.warnings))


_FETCH_ERRORS = (http.FetchError, snap.OfflineMiss)


def _failed(item: str, err: Exception, reads: _Reads) -> dict:
    """unsourced() plus the User-Agent warning: an unset UA is the usual cause of a 403."""
    out = unsourced(item, str(err), TICKERS_URL, source=SOURCE)
    out["warnings"] = reads.all_warnings()
    return out


# ---------- issuer ----------

@dataclass(frozen=True)
class Issuer:
    ticker: str
    cik: int
    name: str
    foreign_private: bool
    canadian: bool
    regime_evidence: tuple[str, ...]
    rows: tuple[dict, ...]           # every submissions row, newest first
    submissions_url: str

    def summary(self) -> dict:
        return {"ticker": self.ticker, "cik": self.cik, "name": self.name,
                "foreign_private_issuer": self.foreign_private,
                "canadian": self.canadian, "regime_evidence": list(self.regime_evidence)}


class IssuerNotFound(LookupError):
    """Ticker absent from SEC's company_tickers.json."""


def cik_for(tickers_json: dict, ticker: str) -> int:
    """CIK for `ticker` from company_tickers.json (pure)."""
    want = ticker.upper().replace(".", "-")
    for row in tickers_json.values():
        if row.get("ticker") == want:
            return int(row["cik_str"])
    raise IssuerNotFound(f"{ticker} is not in {TICKERS_URL}")


def submission_rows(block: dict) -> list[dict]:
    """Column-oriented `filings.recent` (or an older page) -> row dicts."""
    forms = block.get("form", [])
    return [{
        "form": forms[i],
        "filing_date": block["filingDate"][i],
        "report_date": block.get("reportDate", [None] * len(forms))[i] or None,
        "accession": block["accessionNumber"][i],
        "items": [s for s in (block.get("items", [""] * len(forms))[i] or "").split(",") if s],
        "primary_document": block.get("primaryDocument", [""] * len(forms))[i] or None,
    } for i in range(len(forms))]


def classify_issuer(submissions: dict, rows: list[dict]) -> tuple[bool, bool, list[str]]:
    """(foreign_private, canadian, evidence) from submissions metadata (pure).

    The regime follows the latest periodic report (20-F/40-F vs 10-K/10-Q), so
    an issuer that once filed 20-Fs and now files 10-Ks is domestic; with no
    periodic report on file, furnishing 6-Ks marks a foreign private issuer.
    """
    evidence = []
    latest = next((r for r in rows if r["form"] in DOMESTIC_PERIODIC + FOREIGN_PERIODIC), None)
    if latest is not None:
        fpi = latest["form"] in FOREIGN_PERIODIC
        evidence.append(f"latest periodic report: {latest['form']} ({latest['filing_date']})")
    else:
        fpi = any(r["form"] == "6-K" for r in rows)
        if fpi:
            evidence.append("furnishes 6-K, no periodic report on file")
    places = {
        "incorporation": submissions.get("stateOfIncorporationDescription") or "",
        "business address": (submissions.get("addresses", {}).get("business") or {})
        .get("stateOrCountryDescription") or "",
    }
    in_canada = [f"{k}: {v}" for k, v in places.items() if "canada" in v.lower()]
    evidence.extend(in_canada)
    canadian = (latest is not None and latest["form"] == "40-F") or bool(in_canada)
    return fpi, canadian, evidence


def _issuer(ticker: str, reads: _Reads) -> Issuer:
    cik = cik_for(reads.get(TICKERS_URL, TICKERS_MAX_AGE_H).json(), ticker)
    url = SUBMISSIONS_URL.format(cik=cik)
    subs = reads.get(url, INDEX_MAX_AGE_H).json()
    rows = submission_rows(subs["filings"]["recent"])
    periodic = DOMESTIC_PERIODIC + FOREIGN_PERIODIC
    older = subs["filings"].get("files") or []
    if older and sum(r["form"] in periodic for r in rows) < DOMESTIC_PERIODIC_COUNT:
        page = reads.get(SUBMISSIONS_PAGE_URL.format(name=older[0]["name"]), INDEX_MAX_AGE_H)
        rows += submission_rows(page.json())
    rows.sort(key=lambda r: r["filing_date"], reverse=True)
    fpi, canadian, evidence = classify_issuer(subs, rows)
    return Issuer(ticker=ticker.upper(), cik=cik, name=subs.get("name") or "",
                  foreign_private=fpi, canadian=canadian, regime_evidence=tuple(evidence),
                  rows=tuple(rows), submissions_url=url)


# ---------- filing set ----------

def _acc_path(accession: str) -> str:
    return accession.replace("-", "")


def document_url(cik: int, accession: str, document: str) -> str:
    return ARCHIVE_URL.format(cik=cik, acc=_acc_path(accession), doc=document)


def _filing(cik: int, row: dict) -> dict:
    doc = row["primary_document"]
    return {"form": row["form"], "filing_date": row["filing_date"],
            "report_date": row["report_date"], "accession": row["accession"],
            "items": row["items"],
            "url": document_url(cik, row["accession"], doc) if doc else None,
            "index_url": INDEX_URL.format(cik=cik, acc=_acc_path(row["accession"]),
                                          accession=row["accession"])}


def _base_form(form: str) -> str:
    return form.removesuffix("/A")


def filing_set(rows: list[dict] | tuple[dict, ...], *, foreign_private: bool,
               today: date) -> dict:
    """Periodic / current / prospectus rows plus the window start (pure)."""
    forms, count = ((FOREIGN_PERIODIC, FOREIGN_PERIODIC_COUNT) if foreign_private
                    else (DOMESTIC_PERIODIC, DOMESTIC_PERIODIC_COUNT))
    originals = [r for r in rows if r["form"] in forms][:count]
    start = originals[-1]["filing_date"] if originals else None
    periodic = [r for r in rows if _base_form(r["form"]) in forms
                and (r in originals or (start and r["filing_date"] >= start
                                        and r["form"].endswith("/A")))]
    current_from = (today - timedelta(days=CURRENT_DAYS)).isoformat()
    current = [r for r in rows if r["form"] in CURRENT_FORMS and r["filing_date"] >= current_from]
    prospectus = [r for r in rows if (r["form"].startswith("424B") or r["form"] == "SUPPL")
                  and start and r["filing_date"] >= start]
    return {"window_start": start, "current_from": current_from,
            "periodic": periodic, "current": current, "prospectus": prospectus}


def _filings_data(issuer: Issuer, today: date) -> dict:
    fs = filing_set(issuer.rows, foreign_private=issuer.foreign_private, today=today)
    current = [_filing(issuer.cik, r) for r in fs["current"]]
    return {
        **issuer.summary(),
        "window_start": fs["window_start"],
        "current_from": fs["current_from"],
        "periodic": [_filing(issuer.cik, r) for r in fs["periodic"]],
        "current": current,
        "current_by_item": {item: [f["accession"] for f in current if item in f["items"]]
                            for item in TRACKED_8K_ITEMS},
        "prospectus": [_filing(issuer.cik, r) for r in fs["prospectus"]],
    }


def recent_filings(ticker: str) -> dict:
    """Periodic, 8-K/6-K (with items) and 424B/SUPPL filings with URLs.

    data: {ticker, cik, name, foreign_private_issuer, canadian, regime_evidence,
    window_start, current_from, periodic[], current[], current_by_item{item:
    [accession]}, prospectus[]}; each filing row is {form, filing_date,
    report_date, accession, items, url, index_url}.
    """
    reads = _Reads()
    try:
        issuer = _issuer(ticker, reads)
    except (*_FETCH_ERRORS, IssuerNotFound) as e:
        return _failed("filings", e, reads)
    data = _filings_data(issuer, snap.asof_date())
    unverified_lines = [] if data["periodic"] else [unverified("periodic", "no periodic report in EDGAR submissions",
                                          issuer.submissions_url)]
    return envelope(data, prov=reads.prov(issuer.submissions_url),
                    warnings=reads.all_warnings(), not_verified=unverified_lines)


# ---------- Form 4 ----------

def _text(node: ElementTree.Element | None, path: str) -> str | None:
    """Text of `path/value` (Form 4 wraps most fields in <value>)."""
    if node is None:
        return None
    el = node.find(f"{path}/value")
    if el is None:
        el = node.find(path)
    text = (el.text or "").strip() if el is not None else ""
    return text or None


def _number(node: ElementTree.Element, path: str) -> float | None:
    raw = _text(node, path)
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None


def parse_form4(xml: bytes) -> list[dict]:
    """Transaction rows from a Form 4 ownershipDocument (pure).

    `price` is None when the form gives only a footnote; a reported 0 (e.g. an
    M exercise of stock units) is kept as the filer reported it.
    """
    root = ElementTree.fromstring(xml)
    owners = root.findall("reportingOwner")
    owner = "; ".join(_text(o, "reportingOwnerId/rptOwnerName") or "" for o in owners) or None
    rel = owners[0].find("reportingOwnerRelationship") if owners else None
    relationship = {tag: _text(rel, tag) in ("1", "true")
                    for tag in ("isDirector", "isOfficer", "isTenPercentOwner", "isOther")}
    relationship["officerTitle"] = _text(rel, "officerTitle")
    rows = []
    for table, tag in (("non_derivative", "nonDerivativeTable/nonDerivativeTransaction"),
                       ("derivative", "derivativeTable/derivativeTransaction")):
        for t in root.findall(tag):
            rows.append({
                "table": table,
                "security": _text(t, "securityTitle"),
                "code": _text(t, "transactionCoding/transactionCode"),
                "acquired_disposed": _text(
                    t, "transactionAmounts/transactionAcquiredDisposedCode"),
                "shares": _number(t, "transactionAmounts/transactionShares"),
                "price": _number(t, "transactionAmounts/transactionPricePerShare"),
                "transaction_date": _text(t, "transactionDate"),
                "shares_after": _number(
                    t, "postTransactionAmounts/sharesOwnedFollowingTransaction"),
                "direct_indirect": _text(t, "ownershipNature/directOrIndirectOwnership"),
                "owner": owner,
                "relationship": relationship,
            })
    return rows


def _form4_xml_url(cik: int, row: dict) -> str:
    doc = row["primary_document"] or ""
    raw = doc.split("/", 1)[1] if doc.startswith("xsl") and "/" in doc else doc
    return document_url(cik, row["accession"], raw)


def _insider(issuer: Issuer, reads: _Reads, days: int, today: date) -> dict:
    since = (today - timedelta(days=days)).isoformat()
    rows = [r for r in issuer.rows if r["form"] in INSIDER_FORMS and r["filing_date"] >= since]
    warnings, unverified_lines, out = [], [], []
    if len(rows) > INSIDER_MAX_FILINGS:
        warnings.append(f"{len(rows)} Form 4 filings since {since}; parsed the "
                        f"{INSIDER_MAX_FILINGS} most recent")
        rows = rows[:INSIDER_MAX_FILINGS]
    for r in rows:
        url = _form4_xml_url(issuer.cik, r)
        try:
            parsed = parse_form4(reads.get(url, DOCUMENT_MAX_AGE_H).body)
        except (*_FETCH_ERRORS, ElementTree.ParseError) as e:
            unverified_lines.append(unverified(f"form4 {r['accession']}", str(e), url))
            continue
        for t in parsed:
            out.append({**t, "form": r["form"], "filing_date": r["filing_date"],
                        "accession": r["accession"], "url": url,
                        "filing_url": _filing(issuer.cik, r)["url"]})
    by_code: dict[str, int] = {}
    for t in out:
        by_code[t["code"] or "?"] = by_code.get(t["code"] or "?", 0) + 1
    data = {**issuer.summary(), "since": since, "filings_parsed": len(rows) - len(unverified_lines),
            "transactions": out, "count_by_code": by_code}
    return {"data": data, "warnings": warnings, "not_verified": unverified_lines}


def insider_trades(ticker: str, days: int = INSIDER_DAYS) -> dict:
    """Form 4 transactions filed in the last `days` days.

    data: {ticker, cik, ..., since, filings_parsed, count_by_code{code: n},
    transactions[{table, security, code, acquired_disposed, shares, price,
    transaction_date, shares_after, direct_indirect, owner, relationship,
    form, filing_date, accession, url, filing_url}]}.
    """
    reads = _Reads()
    try:
        issuer = _issuer(ticker, reads)
    except (*_FETCH_ERRORS, IssuerNotFound) as e:
        return _failed(INSIDER_ITEM, e, reads)
    res = _insider(issuer, reads, days, snap.asof_date())
    unverified_lines = res["not_verified"]
    if issuer.foreign_private:
        unverified_lines = [*unverified_lines, _insider_nv(issuer)]
    return envelope(res["data"], prov=reads.prov(issuer.submissions_url),
                    warnings=reads.all_warnings() + res["warnings"], not_verified=unverified_lines)


def _insider_nv(issuer: Issuer) -> dict:
    if issuer.canadian:
        return unverified(INSIDER_ITEM,
                  "Canadian issuer: insider trades are filed on SEDI, which has no API "
                  "and no query-string prefill; open the issuer search and enter "
                  f"'{issuer.name}' (EDGAR name).", SEDI_ISSUER_URL)
    return unverified(INSIDER_ITEM,
              "Foreign private issuer: exempt from Section 16, so Form 4 absence is not "
              "evidence; insider dealings are reported under home-market rules.",
              BROWSE_URL.format(cik=issuer.cik, form="4"))


# ---------- share count ----------

def parse_share_facts(concept_json: dict) -> list[dict]:
    """dei:EntityCommonStockSharesOutstanding facts, one per (date, accession) (pure)."""
    seen, out = set(), []
    for f in sorted(concept_json.get("units", {}).get("shares", []),
                    key=lambda f: (f["end"], f["filed"])):
        key = (f["end"], f["accn"])
        if key in seen:
            continue
        seen.add(key)
        out.append({"date": f["end"], "value": f["val"], "form": f.get("form"),
                    "filed": f.get("filed"), "accession": f["accn"]})
    return out


NO_SHARE_FACT = ("no undimensioned dei:EntityCommonStockSharesOutstanding fact (HTTP 404); "
                 "filers that tag shares per class with dimensions have none - read the "
                 "cover page of the latest periodic report")


def _is_404(e: Exception) -> bool:
    cause = e.__cause__
    return isinstance(cause, urllib.error.HTTPError) and cause.code == 404


def _share_trend(issuer: Issuer, reads: _Reads) -> dict:
    url = SHARES_URL.format(cik=issuer.cik)
    try:
        facts = parse_share_facts(reads.get(url, INDEX_MAX_AGE_H).json())
    except (*_FETCH_ERRORS, ValueError) as e:
        reason = (NO_SHARE_FACT if _is_404(e) else f"XBRL read failed: {e}")
        return {"data": None, "not_verified": [unverified(SHARES_ITEM, reason, url)], "url": url}
    for f in facts:
        f["url"] = INDEX_URL.format(cik=issuer.cik, acc=_acc_path(f["accession"]),
                                    accession=f["accession"])
    for prev, cur in zip(facts, facts[1:], strict=False):
        cur["change_pct"] = (100.0 * (cur["value"] / prev["value"] - 1.0)
                             if prev["value"] else None)
    if facts:
        facts[0]["change_pct"] = None
    unverified_lines = [] if facts else [unverified(SHARES_ITEM, "concept has no share facts", url)]
    return {"data": {**issuer.summary(), "facts": facts}, "not_verified": unverified_lines, "url": url}


def share_count_trend(ticker: str) -> dict:
    """Cover-page shares outstanding over time.

    data: {ticker, cik, ..., facts[{date, value (shares), form, filed, accession,
    url, change_pct vs the previous fact}]}, oldest first.
    """
    reads = _Reads()
    try:
        issuer = _issuer(ticker, reads)
    except (*_FETCH_ERRORS, IssuerNotFound) as e:
        return _failed(SHARES_ITEM, e, reads)
    res = _share_trend(issuer, reads)
    return envelope(res["data"], prov=reads.prov(res["url"]),
                    warnings=reads.all_warnings(), not_verified=res["not_verified"])


# ---------- full-text search and excerpts ----------

def efts_url(cik: int, phrases: tuple[str, ...], forms: list[str], start: str, end: str,
             offset: int = 0) -> str:
    params = {"q": " OR ".join(f'"{p}"' for p in phrases), "ciks": f"{cik:010d}",
              "forms": ",".join(forms), "dateRange": "custom", "startdt": start, "enddt": end}
    if offset:
        params["from"] = str(offset)
    return f"{EFTS_URL}?{urllib.parse.urlencode(params)}"


def parse_efts(body: dict) -> tuple[int, list[dict]]:
    """(total hits, hit rows) from a search-index response (pure)."""
    hits = body["hits"]
    rows = []
    for h in hits["hits"]:
        src = h["_source"]
        accession, _, document = h["_id"].partition(":")
        rows.append({"accession": accession, "document": document, "form": src.get("form"),
                     "file_type": src.get("file_type"), "filing_date": src.get("file_date")})
    return int(hits["total"]["value"]), rows


class _TextExtractor(HTMLParser):
    _SKIP = frozenset({"script", "style", "head", "ix:header"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        self.parts.append(" ")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_text(body: bytes) -> str:
    """Visible text of an HTML/iXBRL filing document, whitespace collapsed (pure)."""
    p = _TextExtractor()
    p.feed(body.decode("utf-8", "replace"))
    p.close()
    return re.sub(r"\s+", " ", "".join(p.parts)).strip()


def _phrase_regex(phrases: tuple[str, ...]) -> re.Pattern:
    alts = sorted((r"[\s\-]+".join(map(re.escape, re.split(r"[\s\-]+", p))) for p in phrases),
                  key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(alts) + r")\b", re.IGNORECASE)


def excerpts(text: str, phrases: tuple[str, ...], *, width: int = EXCERPT_WIDTH,
             limit: int = EXCERPTS_PER_DOC) -> list[dict]:
    """Up to `limit` non-overlapping `width`-each-side excerpts around a phrase (pure)."""
    out, next_start = [], 0
    for m in _phrase_regex(phrases).finditer(text):
        if m.start() < next_start:
            continue
        lo, hi = max(0, m.start() - width), min(len(text), m.end() + width)
        out.append({"match": m.group(0), "offset": m.start(), "excerpt": text[lo:hi]})
        next_start = hi
        if len(out) == limit:
            break
    return out


def _efts_page(url: str, reads: _Reads) -> tuple[int, list[dict]]:
    """One search-index page, retried after EFTS_RETRY_DELAYS on a network error.

    EFTS answers HTTP 500 to some cold queries and 200 to the same URL a
    second later (seen repeatedly for FRO on 2026-09-21).
    """
    for delay in EFTS_RETRY_DELAYS:
        try:
            return parse_efts(reads.get(url, INDEX_MAX_AGE_H).json())
        except http.FetchError:
            time.sleep(delay)
    return parse_efts(reads.get(url, INDEX_MAX_AGE_H).json())


def search_groups(data: dict, today: date) -> list[dict]:
    """Search-index queries that together cover the filing set (pure).

    Current reports are searched only over their 180-day window and the rest
    over the periodic window, so older 8-K/6-K hits do not crowd out pages.
    """
    end = today.isoformat()
    groups = []
    for rows, start in ((data["periodic"] + data["prospectus"], data["window_start"]),
                        (data["current"], data["current_from"])):
        if rows and start:
            groups.append({"forms": sorted({r["form"] for r in rows}), "start": start,
                           "end": end})
    return groups


def _search(issuer: Issuer, phrases: tuple[str, ...], groups: list[dict],
            reads: _Reads) -> tuple[list[dict], bool, list[str]]:
    """(hit rows, every page read?, query URLs) over all search groups."""
    rows: list[dict] = []
    complete, urls = True, []
    for g in groups:
        got: list[dict] = []
        for page in range(EFTS_MAX_PAGES):
            url = efts_url(issuer.cik, phrases, g["forms"], g["start"], g["end"],
                           page * EFTS_PAGE_SIZE)
            if not page:
                urls.append(url)
            total, hits = _efts_page(url, reads)
            got += hits
            if len(got) >= total or not hits:
                break
        else:
            complete = False
        rows += got
    return rows, complete, urls


def _hit_evidence(issuer: Issuer, hit: dict, phrases: tuple[str, ...], reads: _Reads) -> dict:
    url = document_url(issuer.cik, hit["accession"], hit["document"])
    row = {**hit, "url": url}
    try:
        found = excerpts(html_text(reads.get(url, DOCUMENT_MAX_AGE_H).body), phrases)
    except _FETCH_ERRORS as e:
        return {**row, "excerpts": [], "error": str(e)}
    if not found:
        row["note"] = "search index hit; phrase not located in the rendered text"
    return {**row, "excerpts": found}


def _hit_filings(hits: list[dict]) -> list[dict]:
    """Distinct filings among document-level hits, newest first."""
    out: dict[str, dict] = {}
    for h in hits:
        out.setdefault(h["accession"], {k: h[k] for k in ("accession", "form", "filing_date")})
    return list(out.values())


def _keyword_line(issuer: Issuer, keyword: str, searched: dict[str, dict],
                  groups: list[dict], reads: _Reads) -> dict:
    phrases = KEYWORDS[keyword]
    try:
        hits, complete, urls = _search(issuer, phrases, groups, reads)
    except (*_FETCH_ERRORS, KeyError, ValueError) as e:
        return unverified(keyword, f"full-text search failed: {e}", EFTS_URL, phrases=list(phrases))
    base = {"item": keyword, "phrases": list(phrases), "search_urls": urls,
            "search_complete": complete}
    in_set = sorted((h for h in hits if h["accession"] in searched),
                    key=lambda h: (h["filing_date"], h["accession"]), reverse=True)
    if in_set:
        docs = in_set[:DOCS_PER_KEYWORD]
        return {**base, "status": "verified", "reason": None, "found": True,
                "url": document_url(issuer.cik, docs[0]["accession"], docs[0]["document"]),
                "hit_documents": len(in_set), "hit_filings": _hit_filings(in_set),
                "evidence": [_hit_evidence(issuer, h, phrases, reads) for h in docs]}
    if not complete:
        return {**base, **unverified(keyword, f"more than {EFTS_MAX_PAGES * EFTS_PAGE_SIZE} hits "
                             "per query, none in the filing set; absence not established",
                             urls[0])}
    if issuer.canadian:
        return {**base, **unverified(
            keyword, "no EDGAR hit, but a Canadian issuer's primary filings are on SEDAR+, "
            "which has no API and no query-string prefill; open the document search and "
            f"search issuer '{issuer.name}' (EDGAR name) for '{keyword}'.",
            SEDAR_SEARCH_URL), "found": False}
    return {**base, "status": "verified", "reason": None, "found": False, "url": urls[0],
            "hit_documents": 0, "hit_filings": [], "evidence": []}


def _atm_line(issuer: Issuer, data: dict, keyword_lines: dict[str, dict]) -> dict:
    atm = keyword_lines.get("at-the-market", {})
    atm_accessions = {h["accession"] for h in atm.get("hit_filings", [])}
    rows = [{**p, "at_the_market_hit": p["accession"] in atm_accessions}
            for p in data["prospectus"]]
    if issuer.canadian and not rows:
        return unverified(ATM_ITEM, "no prospectus supplement on EDGAR; Canadian shelf "
                  "supplements are filed on SEDAR+ (no API, no prefill): search issuer "
                  f"'{issuer.name}' (EDGAR name) for prospectus supplements.",
                  SEDAR_SEARCH_URL, prospectus=[])
    return {"item": ATM_ITEM, "status": "verified", "reason": None,
            "found": bool(rows),
            "url": BROWSE_URL.format(cik=issuer.cik, form="424B"),
            "prospectus": rows}


def _insider_line(issuer: Issuer, res: dict) -> dict:
    tx = res["data"]["transactions"]
    evidence = {"transactions": tx, "count_by_code": res["data"]["count_by_code"],
                "since": res["data"]["since"]}
    if issuer.foreign_private:
        line = _insider_nv(issuer)
        return {**line, **evidence, "found": bool(tx)}
    if res["not_verified"]:
        return unverified(INSIDER_ITEM, "some Form 4 filings could not be read",
                  BROWSE_URL.format(cik=issuer.cik, form="4"), found=bool(tx),
                  failures=res["not_verified"], **evidence)
    return {"item": INSIDER_ITEM, "status": "verified", "reason": None, "found": bool(tx),
            "url": BROWSE_URL.format(cik=issuer.cik, form="4"), **evidence}


def _shares_line(res: dict) -> dict:
    if res["data"] is None or not res["data"]["facts"]:
        return res["not_verified"][0]
    facts = res["data"]["facts"]
    return {"item": SHARES_ITEM, "status": "verified", "reason": None, "found": True,
            "url": facts[-1]["url"], "latest": facts[-1], "facts": facts}


# ---------- manual entries ----------

def _manual_path() -> Path:
    return snap.home() / MANUAL_DIR / MANUAL_FILE


def _load_manual() -> dict:
    path = _manual_path()
    return json.loads(path.read_text()) if path.exists() else {}


def add_manual_entry(ticker: str, item: str, finding: str, document_ref: str | None,
                     url: str | None = None) -> dict:
    """Record a finding the user read themselves, with the document it came from.

    `item` is one of SWEEP_ITEMS. Without a `document_ref` the entry is kept
    but stays not_verified. Stored in `.optionslab/manual/sweep.json`.
    """
    if item not in SWEEP_ITEMS:
        raise ValueError(f"item must be one of {SWEEP_ITEMS}, got {item!r}")
    ref = (document_ref or "").strip() or None
    entry = {"item": item, "finding": finding, "document_ref": ref, "url": url,
             "entered_at": iso(), "status": "verified" if ref else "not_verified"}
    store = _load_manual()
    store.setdefault(ticker.upper(), []).append(entry)
    path = _manual_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, indent=1) + "\n")
    return entry


def manual_entries(ticker: str) -> list[dict]:
    return list(_load_manual().get(ticker.upper(), []))


def _apply_manual(line: dict, entries: list[dict]) -> dict:
    mine = [e for e in entries if e["item"] == line["item"]]
    if not mine:
        return line
    line = {**line, "manual": mine}
    if line["status"] != "verified" and any(e["status"] == "verified" for e in mine):
        line.update(status="verified", verified_by="manual", edgar_reason=line["reason"],
                    reason=None)
    return line


# ---------- sweep ----------

def structural_sweep(ticker: str) -> dict:
    """The capital-structure table: one line per keyword plus insider, ATM, shares.

    data: {ticker, cik, name, foreign_private_issuer, canadian, regime_evidence,
    window_start, current_from, lines[{item, status, reason, url, found, ...}],
    filings_searched[{form, filing_date, accession, url, ...}]}.
    Keyword lines carry search_urls, hit_filings [{accession, form, filing_date}], evidence
    [{url, form, filing_date, excerpts[{match, offset, excerpt}]}].
    """
    reads = _Reads()
    try:
        issuer = _issuer(ticker, reads)
    except (*_FETCH_ERRORS, IssuerNotFound) as e:
        return _failed("sweep", e, reads)
    today = snap.asof_date()
    data = _filings_data(issuer, today)
    searched = {f["accession"]: f for f in
                (*data["periodic"], *data["current"], *data["prospectus"])}
    if not searched:
        return unsourced("sweep", "no periodic, current or prospectus filings to search",
                      issuer.submissions_url, source=SOURCE)
    fs = {k: data[k] for k in ("window_start", "current_from")}
    groups = search_groups(data, today)
    keyword_lines = {k: _keyword_line(issuer, k, searched, groups, reads) for k in KEYWORDS}
    insider = _insider(issuer, reads, INSIDER_DAYS, today)
    shares = _share_trend(issuer, reads)
    manual = manual_entries(issuer.ticker)
    lines = [_apply_manual(line, manual) for line in (
        *keyword_lines.values(), _insider_line(issuer, insider),
        _atm_line(issuer, data, keyword_lines), _shares_line(shares))]
    out = {**issuer.summary(), **fs, "lines": lines,
           "filings_searched": sorted(searched.values(), key=lambda f: f["filing_date"],
                                      reverse=True)}
    unverified_lines = [unverified(line["item"], line["reason"], line["url"])
           for line in lines if line["status"] != "verified"]
    warnings = reads.all_warnings() + insider["warnings"]
    if issuer.foreign_private and not issuer.canadian:
        warnings.append("foreign private issuer: EDGAR holds its 20-F and furnished 6-Ks; "
                        "home-market filings outside EDGAR are not searched")
    return envelope(out, prov=reads.prov(issuer.submissions_url), warnings=warnings,
                    not_verified=unverified_lines)
