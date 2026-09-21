"""EIA feed: band / lowest-since definitions and offline replay of real API bodies (2026-09-21)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from optionslab.feeds import eia, http
from optionslab.storage import snapshots as snap

FIX = Path(__file__).parent / "fixtures" / "eia"
DAY = date(2026, 9, 21)


@pytest.fixture
def pinned(offline_home, monkeypatch):
    monkeypatch.setattr(snap, "today_et", lambda: DAY)
    monkeypatch.delenv(eia.KEY_ENV, raising=False)
    return offline_home


def _weeks(start: date, values: list[float]) -> list[tuple[date, float]]:
    return [(start + timedelta(weeks=i), v) for i, v in enumerate(values)]


# ---------- definitions (pure) ----------

def test_band_uses_same_iso_week_of_prior_five_years():
    # one value per week for 6 years + 1 week; value = ISO year, so the band is the years.
    start = date(2020, 9, 11)
    obs = [(start + timedelta(weeks=i), float((start + timedelta(weeks=i)).isocalendar()[0]))
           for i in range(6 * 52 + 1)]
    last_d = obs[-1][0]
    band = eia.five_year_band(obs)
    assert band["n"] == 5
    assert all(date.fromisoformat(w).isocalendar()[1] == last_d.isocalendar()[1]
               for w in band["weeks"])
    assert band["values"] == [float(last_d.isocalendar()[0] - k) for k in range(5, 0, -1)]


def test_band_is_none_without_five_prior_years():
    assert eia.five_year_band(_weeks(date(2023, 1, 6), [1.0] * 150)) is None


def test_week_53_takes_week_52_in_52_week_years():
    assert eia._same_week(date(2025, 12, 26), 53)        # 2025 has 52 ISO weeks
    assert not eia._same_week(date(2026, 12, 25), 53)    # 2026 has week 53 itself


def test_band_stats_definitions():
    band = {"min": 10.0, "max": 20.0, "mean": 15.0, "n": 5, "values": [10.0, 12, 15, 18, 20]}
    s = eia.band_stats(12.0, band)
    assert s["percentile"] == 40.0 and s["band_position"] == 20.0 and s["vs_mean"] == -3.0
    assert eia.band_stats(5.0, band)["band_position"] == -50.0      # below the band, unclamped


def test_lowest_since_all_weeks_and_same_week():
    obs = _weeks(date(2020, 1, 3), [50.0] * 53 + [8.0] + [40.0] * 100 + [9.0])
    ls = eia.lowest_since(obs)
    assert ls["all_weeks"]["week_ending"] == obs[53][0].isoformat()
    assert ls["same_week"]["week_ending"] is None                    # lowest for this week
    assert "starts 2020-01-03" in ls["same_week"]["note"]


def test_normalize_region():
    assert [eia.normalize_region(r) for r in ("PADD 1", "padd3", "5", "us")] == [
        "PADD1", "PADD3", "PADD5", "US"]


# ---------- public functions, offline ----------

def test_weekly_padd1_distillate_offline(pinned):
    http.record(eia.series_url("WDISTP11", eia.DEMO_KEY),
                (FIX / "seriesid_WDISTP11.json").read_bytes(), day=DAY)
    env = eia.weekly_series("distillate_stocks", "PADD 1")
    d = env["data"]
    assert env["status"] == "ok" and env["provenance"]["quality"] == "snapshot"
    assert "api_key" not in d["url"] and "DEMO_KEY" in env["warnings"][0]
    assert (d["series_id"], d["unit"], d["week_ending"], d["value"]) == (
        "WDISTP11", "MBBL", "2026-09-11", 21583.0)
    assert d["five_year_band"]["weeks"] == [
        "2021-09-17", "2022-09-16", "2023-09-15", "2024-09-13", "2025-09-12"]
    assert d["five_year_band"]["min"] == 27917.0 and d["percentile"] == 0.0
    assert d["lowest_since"]["all_weeks"]["week_ending"] == "2026-08-28"
    assert d["lowest_since"]["same_week"]["week_ending"] is None     # lowest for the week since 1990
    assert d["history_start"] == "1990-01-05"


def test_weekly_unknown_pair_is_not_verified(pinned):
    env = eia.weekly_series("spr_stocks", "PADD2")
    assert env["status"] == "not_verified" and env["data"] is None
    assert "['US']" in env["not_verified"][0]["reason"]


def test_weekly_offline_without_snapshot(pinned):
    env = eia.weekly_series("crude_stocks")
    assert env["data"] is None and "fetch failed" in env["not_verified"][0]["reason"]


def test_weekly_uses_env_key_without_warning(pinned, monkeypatch):
    monkeypatch.setenv(eia.KEY_ENV, "abc123")
    http.record(eia.series_url("WDISTP11", "abc123"),
                (FIX / "seriesid_WDISTP11.json").read_bytes(), day=DAY)
    env = eia.weekly_series("distillate_stocks", "PADD1")
    assert env["status"] == "ok" and env["warnings"] == []


def test_steo_offline(pinned):
    http.record(eia.steo_url(eia.DEMO_KEY), (FIX / "steo.json").read_bytes(), day=DAY)
    env = eia.steo()
    series = env["data"]["series"]
    assert env["status"] == "ok" and set(series) == set(eia.STEO_SERIES.values())
    wti = series["wti_price"]
    assert wti["unit"] == "dollars per barrel"
    assert [m["period"] for m in wti["months"]] == sorted(m["period"] for m in wti["months"])
    assert all(m["after_current_month"] == (m["period"] > "2026-09") for m in wti["months"])
    isc = env["data"]["implied_stock_change"]["months"]
    prod = {m["period"]: m["value"] for m in series["world_liquids_production"]["months"]}
    cons = {m["period"]: m["value"] for m in series["world_liquids_consumption"]["months"]}
    assert isc[-1]["value"] == pytest.approx(prod[isc[-1]["period"]] - cons[isc[-1]["period"]])


def test_api_error_body_is_a_not_verified_line(pinned):
    body = json.dumps({"error": "API_KEY_INVALID"}).encode()
    http.record(eia.steo_url(eia.DEMO_KEY), body, day=DAY)
    env = eia.steo()
    assert env["data"] is None and "API_KEY_INVALID" in env["not_verified"][0]["reason"]


def test_seasonal_history_offline(pinned):
    http.record(eia.series_url("WDISTP11", eia.DEMO_KEY),
                (FIX / "seriesid_WDISTP11.json").read_bytes(), day=DAY)
    env = eia.seasonal_history("distillate_stocks", "PADD1")
    d = env["data"]
    assert env["status"] == "ok" and d["unit"] == "MBBL"
    assert [y["year"] for y in d["years"]] == [2021, 2022, 2023, 2024, 2025, 2026]
    assert d["years"][-1]["points"][-1] == {"week": 37, "week_ending": "2026-09-11",
                                            "value": 21583.0}
    wk37 = next(b for b in d["band"] if b["week"] == 37)
    assert (wk37["min"], wk37["max"]) == (27917.0, 39848.0)
