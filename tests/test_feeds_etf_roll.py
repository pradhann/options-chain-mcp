"""USCF fund roll rules, windows and roll yield, replayed offline.

Fixtures: fund and holdings pages and the 2026 roll-dates CSV captured live
on 2026-09-21 (tests/fixtures/etf_roll/), plus the futures closes captured
the same day (tests/fixtures/futures/).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from optionslab.feeds import etf_roll as er
from optionslab.feeds import futures as fut
from optionslab.feeds import http
from optionslab.feeds.envelope import NOT_VERIFIED
from optionslab.storage import snapshots as snap

FIX = Path(__file__).parent / "fixtures"
DAY = date(2026, 9, 21)
CSV_URL = er.ROLL_DATES_URL.format(year=2026)


def _seed_futures() -> None:
    for root in fut.ROOTS:
        df = pd.read_csv(FIX / "futures" / f"{root}_closes.csv")
        meta = json.loads((FIX / "futures" / f"{root}_closes.meta.json").read_text())
        snap.write_table("futures", root, "closes", df, meta=meta, day=DAY)
    snap.write_json("futures", fut.EXPIRY_KEY, "expiries",
                    json.loads((FIX / "futures" / "expiries.json").read_text()), day=DAY)


@pytest.fixture
def seeded(offline_home, monkeypatch):
    monkeypatch.setenv("OPTIONSLAB_ASOF", DAY.isoformat())
    _seed_futures()
    for code, f in er.FUNDS.items():
        http.record(f.page_url, (FIX / "etf_roll" / f"fund_{code.lower()}.html").read_bytes(),
                    day=DAY)
    http.record(er.FUNDS["USO"].holdings_url,
                (FIX / "etf_roll" / "holdings_uso.html").read_bytes(), day=DAY)
    http.record(CSV_URL, _csv_bytes(), day=DAY)
    return offline_home


def _csv_bytes() -> bytes:
    return (FIX / "etf_roll" / "uscf-rolldates-commodities-2026.csv").read_bytes()


def _csv_text() -> str:
    """Decoded the way http.Fetched.text() does (the CSV footer has a latin-1 byte)."""
    return _csv_bytes().decode("utf-8", "replace")


def _close(root: str, sym: str, day: str) -> float:
    df = pd.read_csv(FIX / "futures" / f"{root}_closes.csv")
    return float(df[(df.symbol == sym) & (df.date == day)].close.iloc[0])


# ---------- pure ----------

def test_parse_roll_dates_reads_real_csv():
    windows = er.parse_roll_dates(_csv_text())
    uso = [w for w in windows if w.fund == "USO"]
    assert len(uso) == 12
    assert (uso[8].begin, uso[8].end) == (date(2026, 9, 1), date(2026, 9, 8))
    uga_sep = next(w for w in windows if w.fund == "UGA" and w.begin.month == 9)
    assert uga_sep.begin == date(2026, 9, 16) and uga_sep.end is None
    assert {w.fund for w in windows} >= {"USO", "BNO", "UGA", "UNG"}


def test_roll_target_moves_to_next_window_once_this_month_is_done():
    windows = [w for w in er.parse_roll_dates(_csv_text()) if w.fund == "USO"]
    this_month, target = er.roll_target(windows, DAY)
    assert this_month.status(DAY) == "completed"
    assert target.begin == date(2026, 10, 1)
    bno = [w for w in er.parse_roll_dates(_csv_text()) if w.fund == "BNO"]
    this_month, target = er.roll_target(bno, DAY)
    assert this_month == target and target.status(DAY) == "in_progress"


def test_roll_legs_pick_near_month_at_window_start():
    rows = [{"contract": "V", "expiry": "2026-09-22"}, {"contract": "X", "expiry": "2026-10-20"},
            {"contract": "Z", "expiry": "2026-11-20"}]
    assert [r["contract"] for r in er.roll_legs(rows, date(2026, 9, 1))] == ["V", "X"]
    assert [r["contract"] for r in er.roll_legs(rows, date(2026, 10, 1))] == ["X", "Z"]
    assert er.roll_legs(rows, date(2026, 12, 1)) is None


def test_roll_yield_is_simple_period_return():
    assert er.roll_yield(102.0, 100.0) == pytest.approx(0.02)
    assert er.roll_yield(None, 100.0) is None and er.roll_yield(1.0, None) is None


@pytest.mark.parametrize("code", sorted(er.FUNDS))
def test_page_sentence_is_on_the_captured_fund_page(code):
    body = (FIX / "etf_roll" / f"fund_{code.lower()}.html").read_text()
    assert er.FUNDS[code].page_sentence in er.page_text(body)


def test_holdings_rows_detection():
    assert not er.holdings_rows_present((FIX / "etf_roll" / "holdings_uso.html").read_text())
    assert er.holdings_rows_present(
        '<table id="holdings-table"><thead></thead><tbody><tr><td>x</td></tr></tbody></table>')


# ---------- public API, offline ----------

def test_roll_uso_rule_window_and_expected_yield(seeded):
    env = er.roll_rule("USO")
    d = env["data"]
    assert d["rule"]["page_check"] == "matches" and d["rule"]["roll_days"] == 5
    assert d["rule"]["prospectus_url"].endswith("united-states-oil-fund-pro-20260424.pdf")
    assert any("five-day period beginning on the first business day" in q
               for q in d["rule"]["evidence"])
    assert d["this_month_window"] == {"begin": "2026-09-01", "end": "2026-09-08",
                                      "status": "completed"}
    exp = d["expected_roll"]
    assert exp["window"]["begin"] == "2026-10-01"
    assert (exp["sold"]["contract"], exp["bought"]["contract"]) == ("CLX26", "CLZ26")
    sold, bought = (_close("CL", s, exp["price_date"]) for s in ("CLX26.NYM", "CLZ26.NYM"))
    assert exp["roll_yield"] == pytest.approx((sold - bought) / bought)
    assert "not annualized" in exp["basis"]
    assert env["status"] == "partial"
    assert [n["item"] for n in env["not_verified"]] == ["USO portion rolled"]


def test_roll_bno_in_progress_priced_with_bz_proxy(seeded):
    exp = er.roll_rule("BNO")["data"]["expected_roll"]
    assert exp["window"] == {"begin": "2026-09-16", "end": "2026-09-21", "status": "in_progress"}
    assert (exp["sold"]["contract"], exp["bought"]["contract"]) == ("BZX26", "BZZ26")
    assert "ICE Brent" in exp["price_note"]


def test_roll_rule_not_verified_when_page_sentence_disappears(seeded):
    http.record(er.FUNDS["UNG"].page_url, b"<html><body>new wording</body></html>",
                day=DAY)
    env = er.roll_rule("UNG")
    assert env["data"]["rule"] is None and env["data"]["expected_roll"] is None
    assert any(n["item"] == "UNG roll rule" for n in env["not_verified"])


def test_roll_without_roll_dates_csv(offline_home, monkeypatch):
    monkeypatch.setenv("OPTIONSLAB_ASOF", DAY.isoformat())
    http.record(er.FUNDS["UGA"].page_url, (FIX / "etf_roll" / "fund_uga.html").read_bytes(),
                day=DAY)
    env = er.roll_rule("UGA")
    assert env["data"]["rule"]["page_check"] == "matches"
    assert env["data"]["this_month_window"] is None and env["data"]["expected_roll"] is None
    assert any(n["item"] == "USCF roll dates 2026" for n in env["not_verified"])


def test_holdings_are_not_verified_with_reason(seeded):
    env = er.fund_holdings("USO")
    assert env["status"] == NOT_VERIFIED and env["data"] is None
    assert "401" in env["not_verified"][0]["reason"]


def test_roll_events_in_calendar_shape(seeded):
    events = er.roll_events(["USO", "UGA"])
    keys = {"date", "event", "source_url", "verified", "fetched_at", "notes"}
    assert all(set(e) == keys and e["source_url"] == CSV_URL for e in events)
    assert events[0]["date"] == "2026-09-01" and "USO roll window begins" in events[0]["event"]
    uga = [e for e in events if e["event"].startswith("UGA")]
    assert len(uga) == 4 and all("begins" in e["event"] for e in uga)
    assert min(e["date"] for e in events) >= "2026-09-01"
