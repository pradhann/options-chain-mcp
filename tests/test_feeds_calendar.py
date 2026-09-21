"""Calendar feed: official schedule parsers and offline replay of real pages (read 2026-09-21)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from optionslab.feeds import calendar as cal
from optionslab.feeds import http
from optionslab.storage import snapshots as snap

FIX = Path(__file__).parent / "fixtures" / "calendar"
DAY = date(2026, 9, 21)
OPEC_RELEASES = ("1835613-6-september-2026", "1870612-2-august-2026",
                 "1854611-2-august-2026", "1891610-15-july-2026")


def _text(name: str) -> str:
    return (FIX / name).read_text()


def _by_date(rows: list[dict]) -> dict:
    return {r["date"]: r for r in rows}


@pytest.fixture
def pinned(offline_home, monkeypatch):
    """Offline home with 'today' pinned to the day the fixtures were captured."""
    monkeypatch.setattr(snap, "today_et", lambda: DAY)
    return offline_home


def _seed_macro(*, opec: bool = True) -> None:
    for url, name in ((cal.WPSR_URL, "wpsr_schedule.html"), (cal.STEO_URL, "steo_schedule.html"),
                      (cal.COT_URL, "cot_schedule.html"), (cal.FOMC_URL, "fomc.html")):
        http.record(url, (FIX / name).read_bytes(), day=DAY)
    if opec:
        http.record(cal.OPEC_HOME_URL, (FIX / "opec_home.html").read_bytes(), day=DAY)
        for rel in OPEC_RELEASES:
            http.record(f"{cal.OPEC_HOME_URL}pr-detail/{rel}.html",
                        (FIX / f"opec_{rel}.html").read_bytes(), day=DAY)


def _seed_ticker(sym: str) -> None:
    http.record(cal.NASDAQ_EARNINGS_URL.format(sym=sym),
                (FIX / f"nasdaq_earnings_{sym}.json").read_bytes(), day=DAY)
    http.record(cal.NASDAQ_DIVIDENDS_URL.format(sym=sym),
                (FIX / f"nasdaq_dividends_{sym}.json").read_bytes(), day=DAY)
    snap.write_json("yfinance", sym, "calendar",
                    json.loads(_text(f"yfinance_{sym}.json")), day=DAY)


# ---------- pure parsers ----------

def test_wpsr_standard_weeks_and_holiday_exceptions():
    rows, last = cal.parse_wpsr_schedule(_text("wpsr_schedule.html"))
    got = _by_date(rows)
    assert last == date(2026, 11, 6)
    assert got[date(2026, 9, 23)]["event"] == "EIA WPSR release (week ending 2026-09-18)"
    assert "10:30 a.m. ET" in got[date(2026, 9, 23)]["notes"]
    assert "Columbus Day" in got[date(2026, 10, 15)]["notes"]      # Thursday, not Wed 10-14
    assert date(2026, 10, 14) not in got
    assert max(got) == date(2026, 11, 12)                            # nothing past the table


def test_wpsr_without_the_rule_sentence_emits_nothing():
    text = _text("wpsr_schedule.html").replace("standard release time", "usual time")
    assert cal.parse_wpsr_schedule(text) == ([], None)


def test_steo_release_dates():
    got = _by_date(cal.parse_steo_schedule(_text("steo_schedule.html")))
    assert got[date(2026, 10, 6)]["event"] == "EIA STEO release (October 2026 issue)"
    assert got[date(2026, 10, 6)]["notes"] == "Winter Fuels Outlook"
    assert got[date(2026, 9, 9)]["notes"] == "(Wednesday)"


def test_cot_dates_with_holiday_marks():
    rows = cal.parse_cot_schedule(_text("cot_schedule.html"))
    got = _by_date(rows)
    assert len([d for d in got if d.year == 2026]) == 52
    assert "delayed by a federal holiday" in got[date(2026, 11, 16)]["notes"]
    assert "3:30 p.m. ET" in got[date(2026, 9, 25)]["notes"]
    assert "delayed" not in got[date(2026, 9, 25)]["notes"]


def test_fomc_decision_day_is_last_meeting_day():
    got = _by_date(cal.parse_fomc_calendar(_text("fomc.html")))
    assert got[date(2026, 10, 28)]["notes"] == "meeting October 27-28"
    assert "Summary of Economic Projections" in got[date(2026, 12, 9)]["notes"]
    assert date(2027, 1, 27) in got


def test_fomc_cross_month_meeting():
    row = cal._fomc_row(2024, "Apr/May", "30-1")
    assert row["date"] == date(2024, 5, 1) and row["notes"] == "meeting Apr/May 30-1"


def test_opec_links_and_next_meeting_sentences():
    links = cal.parse_opec_home_links(_text("opec_home.html"))
    assert links[0] == "https://www.opec.org/pr-detail/1835613-6-september-2026.html"
    assert len(links) == 4                                           # pn-detail news excluded
    jmmc = cal.parse_opec_release(_text("opec_1870612-2-august-2026.html"))
    assert [(r["date"], r["event"]) for r in jmmc] == [(date(2026, 10, 4), "OPEC+ JMMC meeting (68th)")]
    group = cal.parse_opec_release(_text("opec_1835613-6-september-2026.html"))
    assert group[0]["event"] == "OPEC+ meeting" and group[0]["date"] == date(2026, 10, 4)


def test_nasdaq_earnings_parse_flags_algorithmic_estimates():
    vlo = cal.parse_nasdaq_earnings(json.loads(_text("nasdaq_earnings_VLO.json")))
    su = cal.parse_nasdaq_earnings(json.loads(_text("nasdaq_earnings_SU.json")))
    assert (vlo["date"], vlo["timing"], vlo["is_estimate"]) == ("2026-10-22", "before market open", False)
    assert (su["date"], su["is_estimate"]) == ("2026-11-03", True)


def test_date_in_text_forms():
    d = date(2026, 10, 22)
    for s in ("Q3 results on October 22, 2026", "Oct. 22, 2026", "22 October 2026", "10/22/2026"):
        assert cal.date_in_text(d, s)
    assert not cal.date_in_text(d, "October 2, 2026")


def test_past_earnings_date_is_never_next():
    src = [{"source": "nasdaq", "url": "u", "dates": ["2026-09-01"], "is_estimate": False,
            "detail": None}]
    assert cal.next_earnings_event("X", src, DAY, "t") is None


def test_disagreeing_sources_are_not_verified():
    src = [{"source": "yfinance", "url": "y", "dates": ["2026-10-22"], "is_estimate": False,
            "detail": None},
           {"source": "nasdaq", "url": "n", "dates": ["2026-10-29"], "is_estimate": False,
            "detail": None}]
    ev = cal.next_earnings_event("X", src, DAY, "t")
    assert ev["date"] == "2026-10-22" and ev["verified"] is False
    assert "do not agree" in ev["notes"]


def test_ir_page_counts_as_second_source():
    src = [{"source": "yfinance", "url": "y", "dates": ["2026-10-22"], "is_estimate": True,
            "detail": None},
           {"source": "ir_calendar", "url": "ir", "dates": [], "is_estimate": False,
            "detail": None, "text": "Third quarter 2026 earnings call October 22, 2026"}]
    assert cal.next_earnings_event("X", src, DAY, "t")["verified"] is True


# ---------- public functions, offline ----------

def test_macro_calendar_offline(pinned):
    _seed_macro()
    env = cal.macro_calendar()
    events = env["data"]["events"]
    assert env["status"] == "partial" and env["provenance"]["quality"] == "snapshot"
    assert [e["item"] for e in env["not_verified"]] == [
        "EIA WPSR releases for weeks ending after 2026-11-06"]
    assert events == sorted(events, key=lambda e: (e["date"], e["event"]))
    assert events[0]["date"] == "2026-09-23" and all(e["verified"] for e in events)
    assert all(e["date"] >= "2026-09-21" for e in events)
    names = {e["event"] for e in events}
    assert {"OPEC+ JMMC meeting (68th)", "FOMC decision", "CFTC Commitments of Traders release"} <= names
    opec = next(e for e in events if e["event"].startswith("OPEC+ JMMC"))
    assert opec["source_url"] == "https://www.opec.org/pr-detail/1870612-2-august-2026.html"


def test_unreachable_opec_is_a_not_verified_line_not_a_date(pinned):
    _seed_macro(opec=False)
    env = cal.macro_calendar()
    assert not any(e["event"].startswith("OPEC") for e in env["data"]["events"])
    line = next(x for x in env["not_verified"] if x["item"] == "OPEC / OPEC+ meeting dates")
    assert line["url"] == cal.OPEC_HOME_URL


def test_earnings_offline_verified_when_sources_agree(pinned):
    _seed_ticker("VLO")
    env = cal.next_earnings("vlo")
    ev = env["data"]["next_earnings"]
    assert env["status"] == "ok" and ev["date"] == "2026-10-22" and ev["verified"] is True
    assert [s["source"] for s in env["data"]["sources"]] == ["yfinance", "nasdaq"]


def test_earnings_two_estimates_do_not_verify(pinned):
    _seed_ticker("SU")
    ev = cal.next_earnings("SU")["data"]["next_earnings"]
    assert ev["date"] == "2026-11-03" and ev["verified"] is False


def test_earnings_without_any_source(pinned):
    env = cal.next_earnings("ZZZZ")
    assert env["data"]["next_earnings"] is None
    assert {x["item"] for x in env["not_verified"]} == {
        "yfinance calendar ZZZZ", "Nasdaq earnings date ZZZZ", "ZZZZ next earnings"}


def test_ex_dividend_verified_by_nasdaq_history(pinned):
    _seed_ticker("AAPL")
    data = cal.ex_dividend("AAPL")["data"]
    ev = data["ex_dividend"]
    assert ev["date"] == "2026-08-10" and ev["verified"] is True and data["upcoming"] is False
    assert "0.27" in ev["notes"]


def test_ex_dividend_non_nasdaq_symbol_stays_unverified(pinned):
    _seed_ticker("VLO")
    ev = cal.ex_dividend("VLO")["data"]["ex_dividend"]
    assert ev["verified"] is False and "Non-Nasdaq" in ev["notes"]


def test_events_between_merges_tickers_and_extra_events(pinned):
    _seed_macro()
    _seed_ticker("VLO")
    roll = cal.event("2026-10-13", "USO roll window start", source_url="https://example.test",
                     verified=True, fetched_at="2026-09-21T00:00:00+00:00")
    env = cal.events_between(date(2026, 10, 1), "2026-10-31", tickers=["VLO"],
                             extra_events=[roll, {"event": "bad"}])
    events = env["data"]["events"]
    assert all("2026-10-01" <= e["date"] <= "2026-10-31" for e in events)
    assert events == sorted(events, key=lambda e: (e["date"], e["event"]))
    names = {e["event"] for e in events}
    assert {"VLO earnings", "USO roll window start", "FOMC decision"} <= names
    assert "VLO ex-dividend" not in names                            # 2026-07-31, outside range
    assert any(x["item"] == "bad" for x in env["not_verified"])
