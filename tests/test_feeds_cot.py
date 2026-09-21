"""COT managed-money feed: parsers, derived fields, and offline replay.

Fixtures are real bodies captured 2026-09-21: the CFTC Socrata query for
CL and NG, the CFTC release-schedule page, and the ICE COTHist files
trimmed to the Brent futures-and-options rows.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from optionslab.feeds import cot, http

FIX = Path(__file__).parent / "fixtures" / "cot"
ICE_YEARS = (2023, 2024, 2025, 2026)
CAPTURED = date(2026, 9, 21)


def _bytes(name: str) -> bytes:
    return (FIX / name).read_bytes()


def _seed_cftc(*roots: str, schedule: bool = True) -> None:
    for root in roots:
        http.record(cot.cftc_url(cot.CFTC_MARKETS[root][0]), _bytes(f"cftc_{root}.json"),
                    day=CAPTURED)
    if schedule:
        http.record(cot.CFTC_SCHEDULE_URL, _bytes("cftc_release_schedule.html"), day=CAPTURED)


def _seed_ice(years=ICE_YEARS) -> None:
    for y in years:
        http.record(cot.ice_url(y), _bytes(f"ice_COTHist{y}.csv"), day=CAPTURED)


@pytest.fixture
def asof(offline_home, monkeypatch):
    monkeypatch.setenv("OPTIONSLAB_ASOF", "2026-09-21")
    return offline_home


# ---------- pure parsers ----------

def test_parse_cftc_orders_oldest_first_and_keeps_labels():
    s = cot.parse_cftc(json.loads(_bytes("cftc_CL.json")))
    assert s.market == "WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE"
    assert s.report_type == "Combined"
    assert s.prints[0].report_date < s.prints[-1].report_date == date(2026, 9, 15)
    last = s.prints[-1]
    assert (last.mm_long, last.mm_short, last.open_interest) == (229893, 93125, 2725013)
    assert last.net == 136768


def test_parse_cftc_rejects_empty():
    with pytest.raises(ValueError):
        cot.parse_cftc([])


def test_parse_ice_csv_finds_brent_combined_row():
    s = cot.parse_ice_csv(_bytes("ice_COTHist2026.csv").decode("utf-8"), cot.ICE_BRENT_MARKET)
    assert s.report_type == "Combined"
    last = s.prints[-1]
    assert last.report_date == date(2026, 9, 15)
    assert (last.mm_long, last.mm_short, last.open_interest) == (373354, 90697, 3754697)


def test_parse_ice_csv_unknown_market_raises():
    with pytest.raises(ValueError):
        cot.parse_ice_csv(_bytes("ice_COTHist2026.csv").decode("utf-8"), "No Such Market")


def test_release_schedule_parses_holiday_marks():
    sched = cot.parse_release_schedule(_bytes("cftc_release_schedule.html").decode("utf-8"))
    assert date(2026, 1, 5) in sched            # "05*" holiday-delayed
    assert date(2026, 9, 18) in sched
    assert sched == sorted(sched)


def test_release_date_is_first_scheduled_date_after_report():
    sched = [date(2026, 1, 5), date(2026, 1, 9), date(2026, 9, 11), date(2026, 9, 18)]
    assert cot.release_date_for(date(2026, 9, 15), sched) == date(2026, 9, 18)
    # before the page's first listed release: the page does not cover that week
    assert cot.release_date_for(date(2025, 12, 30), sched) is None
    assert cot.release_date_for(date(2026, 9, 22), sched) is None


def test_percentile_rank_window_and_coverage():
    days = [date.fromordinal(date(2025, 9, 16).toordinal() + 7 * i) for i in range(53)]
    values = [(d, float(i)) for i, d in enumerate(days)]
    assert cot.percentile_rank(values, 1) == 100.0
    assert cot.percentile_rank(values, 3) is None       # history too short
    lowest_last = values[:-1] + [(values[-1][0], -1.0)]
    assert cot.percentile_rank(lowest_last, 1) == pytest.approx(100 / 53)


# ---------- public functions, offline ----------

def test_managed_money_cl(asof):
    _seed_cftc("CL")
    env = cot.managed_money("cl")
    d = env["data"]
    assert env["status"] == "ok", env["not_verified"]
    assert env["provenance"]["asof"] == "2026-09-15"
    assert env["provenance"]["quality"] == "snapshot"
    assert d["report_date"] == "2026-09-15" and d["release_date"] == "2026-09-18"
    assert d["release_date_source"] == cot.CFTC_SCHEDULE_URL
    assert d["managed_money"] == {"long": 229893, "short": 93125, "net": 136768}
    assert d["net_pct_oi"] == pytest.approx(100 * 136768 / 2725013)
    assert d["days_since_report"] == 6 and d["stale"] is False
    for name in ("net", "net_pct_oi"):
        for window in ("1y", "3y"):
            assert 0 < d["percentiles"][name][window] <= 100


def test_release_date_not_verified_without_schedule(asof):
    _seed_cftc("CL", schedule=False)
    env = cot.managed_money("CL")
    assert env["data"]["release_date"] is None
    assert env["status"] == "partial"
    assert env["not_verified"][0]["item"] == "CL release_date"


def test_stale_flag_follows_config(asof, monkeypatch):
    _seed_cftc("CL")
    (asof / "config.json").write_text(json.dumps({"cot_stale_days": 5}))
    env = cot.managed_money("CL")
    assert env["data"]["stale"] is True
    assert any("days old" in w for w in env["warnings"])


def test_brent_from_ice_release_date_not_verified(asof):
    _seed_ice()
    env = cot.managed_money("BZ")
    d = env["data"]
    assert d["report_type"] == "Combined"
    assert d["managed_money"]["net"] == 373354 - 90697
    assert d["release_date"] is None
    assert [n["item"] for n in env["not_verified"]] == ["BZ release_date"]
    assert env["not_verified"][0]["url"] == cot.ICE_REPORT_PAGE
    assert d["percentiles"]["net"]["3y"] is not None


def test_brent_with_only_current_year_has_no_percentiles(asof):
    _seed_ice(years=(2026,))
    env = cot.managed_money("BZ")
    items = {n["item"] for n in env["not_verified"]}
    assert env["data"]["percentiles"]["net"]["1y"] is None
    assert {"BZ COT 2023", "BZ net 3y percentile", "BZ net_pct_oi 1y percentile"} <= items


def test_missing_source_is_not_verified_never_zero(asof):
    env = cot.managed_money("NG")
    assert env["status"] == "not_verified" and env["data"] is None
    assert env["provenance"]["quality"] == "unavailable"
    env = cot.managed_money("BZ")
    assert env["data"] is None and env["not_verified"][0]["url"] == cot.ICE_REPORT_PAGE


def test_unknown_root(asof):
    assert cot.managed_money("ZZ")["status"] == "not_verified"


def test_positioning_summary_rows_and_gaps(asof):
    _seed_cftc("CL", "NG")
    _seed_ice()
    env = cot.positioning_summary(("CL", "NG", "HO", "BZ"))
    rows = {r["root"]: r for r in env["data"]["rows"]}
    assert set(rows) == {"CL", "NG", "BZ"}
    assert rows["NG"]["mm_net"] < 0
    assert env["status"] == "partial"
    assert {"HO managed money", "BZ release_date"} <= {n["item"] for n in env["not_verified"]}

