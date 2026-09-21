"""EDGAR capital-structure sweep: offline replay of bodies captured 2026-09-21.

Fixtures are real SEC responses, trimmed: submissions keep 2024-06+ rows of
the forms the sweep reads, search-index bodies keep only the hit whose
document is included, and filing documents keep ~6 KB around each phrase.
`manifest.json` maps each original URL to its fixture file.
"""

from __future__ import annotations

import json
import urllib.error
from datetime import date
from pathlib import Path

import pytest

from optionslab.feeds import edgar, http

FIX = Path(__file__).parent / "fixtures" / "edgar"
DAY = date(2026, 9, 21)


@pytest.fixture
def edgar_home(offline_home, monkeypatch):
    monkeypatch.setenv("OPTIONSLAB_ASOF", DAY.isoformat())
    monkeypatch.setenv("OPTIONSLAB_SEC_UA", "optionslab-tests tests@example.com")
    for url, name in json.loads((FIX / "manifest.json").read_text()).items():
        http.record(url, (FIX / name).read_bytes(), day=DAY)
    return offline_home


def _line(result: dict, item: str) -> dict:
    return next(line for line in result["data"]["lines"] if line["item"] == item)


# ---------- pure parsers ----------

def test_parse_form4_reads_code_shares_price_and_owner():
    rows = edgar.parse_form4((FIX / "form4_wk-form4_1790022100.xml").read_bytes())
    stock = rows[0]
    assert stock["table"] == "non_derivative" and stock["code"] == "M"
    assert stock["shares"] == 924.0 and stock["price"] == 0.0  # reported 0, kept
    assert stock["transaction_date"] == "2026-09-18" and stock["owner"] == "Reymond Robert L"
    assert stock["relationship"]["isDirector"] is True
    assert rows[1]["table"] == "derivative"


def test_parse_form4_missing_price_is_none_not_zero():
    xml = b"""<ownershipDocument><reportingOwner><reportingOwnerId>
      <rptOwnerName>X</rptOwnerName></reportingOwnerId></reportingOwner>
      <nonDerivativeTable><nonDerivativeTransaction>
      <transactionCoding><transactionCode>G</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>10</value></transactionShares>
      <transactionPricePerShare><footnoteId id="F1"/></transactionPricePerShare>
      </transactionAmounts></nonDerivativeTransaction></nonDerivativeTable>
      </ownershipDocument>"""
    (row,) = edgar.parse_form4(xml)
    assert row["code"] == "G" and row["shares"] == 10.0 and row["price"] is None


def test_excerpts_take_width_each_side_and_match_hyphen_or_space():
    text = "a" * 1000 + " at the market offering " + "b" * 1000
    (ex,) = edgar.excerpts(text, ("at-the-market",), width=400, limit=2)
    assert ex["match"] == "at the market"
    assert len(ex["excerpt"]) == 400 + len("at the market") + 400


def test_excerpts_respect_plural_variants_and_limit():
    text = " ".join(["the warrants and one warrant"] * 50)
    assert len(edgar.excerpts(text, ("warrant", "warrants"), width=10, limit=2)) == 2
    assert edgar.excerpts("warranty claims", ("warrant",)) == []


def test_html_text_drops_hidden_ixbrl_header_and_entities():
    body = (b"<html><head><title>t</title></head><body><ix:header>hidden facts</ix:header>"
            b"<p>Share&nbsp;repurchase</p><p>program</p></body></html>")
    assert edgar.html_text(body) == "Share repurchase program"


def test_classify_issuer_from_submissions_not_a_list():
    subs = json.loads((FIX / "submissions_SU.json").read_text())
    rows = edgar.submission_rows(subs["filings"]["recent"])
    fpi, canadian, evidence = edgar.classify_issuer(subs, rows)
    assert fpi and canadian and "incorporation: Alberta, Canada" in evidence

    subs = json.loads((FIX / "submissions_VLO.json").read_text())
    rows = edgar.submission_rows(subs["filings"]["recent"])
    assert edgar.classify_issuer(subs, rows) == (
        False, False, ["latest periodic report: 10-Q (2026-07-30)"])


def test_filing_set_takes_last_eight_periodic_and_180_day_current():
    subs = json.loads((FIX / "submissions_VLO.json").read_text())
    rows = edgar.submission_rows(subs["filings"]["recent"])
    fs = edgar.filing_set(rows, foreign_private=False, today=DAY)
    assert len(fs["periodic"]) == 8 and fs["window_start"] == "2024-10-30"
    assert fs["current_from"] == "2026-03-25"
    assert all(r["filing_date"] >= "2026-03-25" for r in fs["current"])
    assert all(r["form"].startswith("424B") for r in fs["prospectus"])


def test_parse_share_facts_orders_and_dedupes():
    facts = edgar.parse_share_facts(json.loads((FIX / "shares_VLO.json").read_text()))
    assert facts[-1] == {"date": "2026-07-24", "value": 287927461, "form": "10-Q",
                         "filed": "2026-07-30", "accession": "0001628280-26-050937"}
    assert [f["date"] for f in facts] == sorted(f["date"] for f in facts)


# ---------- public API, offline ----------

def test_filings_lists_8k_items_and_prospectus(edgar_home):
    r = edgar.recent_filings("VLO")
    assert r["status"] == "ok" and r["provenance"]["quality"] == "snapshot"
    d = r["data"]
    assert d["cik"] == 1035002 and len(d["periodic"]) == 8
    assert set(d["current_by_item"]) == {"1.05", "2.02", "5.02", "7.01"}
    assert "0001628280-26-050822" in d["current_by_item"]["2.02"]
    assert d["periodic"][0]["url"].startswith("https://www.sec.gov/Archives/edgar/data/1035002/")
    assert [p["form"] for p in d["prospectus"]] == ["424B5"] * 4


def test_insider_form4_domestic_rows(edgar_home):
    r = edgar.insider_trades("VLO")
    assert r["status"] == "ok"
    tx = r["data"]["transactions"]
    assert len(tx) == 6 and r["data"]["count_by_code"]["M"] == 2
    assert all(t["filing_date"] >= "2026-06-23" and t["url"].endswith(".xml") for t in tx)


def test_share_count_trend(edgar_home):
    r = edgar.share_count_trend("VLO")
    facts = r["data"]["facts"]
    assert facts[-1]["value"] == 287927461 and facts[-1]["change_pct"] < 0
    assert facts[0]["change_pct"] is None


def test_share_count_404_is_not_verified_not_substituted(edgar_home, monkeypatch):
    real_get = http.get

    def fake_get(url, **kw):
        if "companyconcept" in url:
            err = urllib.error.HTTPError(url, 404, "Not Found", None, None)
            raise http.FetchError("404") from err
        return real_get(url, **kw)

    monkeypatch.setattr(edgar.http, "get", fake_get)
    r = edgar.share_count_trend("VLO")
    assert r["status"] == "not_verified" and r["data"] is None
    assert "HTTP 404" in r["not_verified"][0]["reason"]


def test_sweep_domestic_hits_and_verified_absence(edgar_home):
    r = edgar.structural_sweep("VLO")
    assert r["status"] == "ok" and r["warnings"] == []
    collar = _line(r, "collar")
    assert collar["status"] == "verified" and collar["found"]
    ev = collar["evidence"][0]
    assert ev["form"] == "10-K" and ev["url"].endswith("a12312024exh1901.htm")
    assert "collar" in ev["excerpts"][0]["excerpt"].lower()
    assert len(ev["excerpts"][0]["excerpt"]) <= 2 * edgar.EXCERPT_WIDTH + len("collars")

    absent = _line(r, "hedged production")
    assert absent["status"] == "verified" and absent["found"] is False
    assert absent["search_complete"] and absent["url"].startswith(edgar.EFTS_URL)
    assert len(r["data"]["filings_searched"]) == 17

    assert _line(r, "insider_form4")["status"] == "verified"
    assert len(_line(r, "atm_424b")["prospectus"]) == 4
    assert _line(r, "share_count")["latest"]["value"] == 287927461


def test_sweep_canadian_absence_points_to_sedar_and_sedi(edgar_home):
    r = edgar.structural_sweep("SU")
    assert r["status"] == "partial" and r["data"]["canadian"]
    conv = _line(r, "convertible")
    assert conv["status"] == "not_verified" and conv["url"] == edgar.SEDAR_SEARCH_URL
    assert "SUNCOR ENERGY INC" in conv["reason"]

    collar = _line(r, "collar")  # EDGAR 6-K hits still count as evidence
    assert collar["status"] == "verified" and collar["evidence"][0]["form"] == "6-K"

    insider = _line(r, "insider_form4")
    assert insider["status"] == "not_verified" and insider["url"] == edgar.SEDI_ISSUER_URL
    assert _line(r, "atm_424b")["status"] == "not_verified"
    nv_items = {n["item"] for n in r["not_verified"]}
    assert {"convertible", "insider_form4", "atm_424b"} <= nv_items


def test_manual_entry_needs_document_ref_to_verify(edgar_home):
    edgar.add_manual_entry("SU", "hedged production", "no hedging program", None)
    assert _line(edgar.structural_sweep("SU"), "hedged production")["status"] == "not_verified"

    edgar.add_manual_entry("SU", "hedged production", "no hedging program",
                           "2025 AIF p.41", url="https://www.sedarplus.ca/")
    line = _line(edgar.structural_sweep("SU"), "hedged production")
    assert line["status"] == "verified" and line["verified_by"] == "manual"
    assert line["edgar_reason"] and len(line["manual"]) == 2

    stored = json.loads((edgar_home / "manual" / "sweep.json").read_text())
    assert stored["SU"][1]["document_ref"] == "2025 AIF p.41"
    with pytest.raises(ValueError):
        edgar.add_manual_entry("SU", "not-an-item", "x", "ref")


def test_missing_user_agent_is_surfaced(edgar_home, monkeypatch):
    monkeypatch.delenv("OPTIONSLAB_SEC_UA")
    assert edgar.UA_WARNING in edgar.recent_filings("VLO")["warnings"]


def test_unknown_ticker_and_offline_miss_do_not_crash(edgar_home):
    r = edgar.structural_sweep("ZZZZ")
    assert r["status"] == "not_verified" and r["data"] is None
    assert edgar.insider_trades("ZZZZ")["not_verified"][0]["item"] == "insider_form4"
