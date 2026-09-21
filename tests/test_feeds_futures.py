"""Futures curves, prompt spreads and cracks, replayed offline from real closes.

Fixtures are the per-contract yfinance closes and expiries captured live on
2026-09-21 (tests/fixtures/futures/), seeded as that day's snapshots.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from optionslab.feeds import config
from optionslab.feeds import futures as fut
from optionslab.feeds.envelope import NOT_VERIFIED
from optionslab.storage import snapshots as snap

FIX = Path(__file__).parent / "fixtures" / "futures"
DAY = date(2026, 9, 21)


@pytest.fixture
def seeded(offline_home, monkeypatch):
    """Snapshots as the live run wrote them; yfinance must never be touched."""
    monkeypatch.setenv("OPTIONSLAB_ASOF", DAY.isoformat())

    def no_network(*_a, **_k):
        raise AssertionError("yfinance called in offline mode")

    monkeypatch.setattr(fut, "_download", no_network)
    monkeypatch.setattr(fut, "_fetch_expiry", no_network)
    for root in fut.ROOTS:
        df = pd.read_csv(FIX / f"{root}_closes.csv")
        meta = json.loads((FIX / f"{root}_closes.meta.json").read_text())
        snap.write_table("futures", root, "closes", df, meta=meta, day=DAY)
    snap.write_json("futures", fut.EXPIRY_KEY, "expiries",
                    json.loads((FIX / "expiries.json").read_text()), day=DAY)
    return offline_home


def _close(root: str, sym: str, day: str) -> float:
    df = pd.read_csv(FIX / f"{root}_closes.csv")
    return float(df[(df.symbol == sym) & (df.date == day)].close.iloc[0])


# ---------- pure helpers ----------

def test_month_symbols_roll_over_the_year():
    assert fut.month_symbols("CL", date(2026, 11, 1), 3) == ["CLX26.NYM", "CLZ26.NYM", "CLF27.NYM"]
    assert fut.parse_symbol("BZF28.NYM") == ("BZ", 2028, 1)
    with pytest.raises(ValueError):
        fut.parse_symbol("CL=F")


def test_parse_expiry_takes_the_date_part_or_none():
    assert fut.parse_expiry({"expireIsoDate": "2026-10-20T00:00:00Z"}) == "2026-10-20"
    assert fut.parse_expiry({"expireDate": 1792454400}) is None
    assert fut.parse_expiry(None) is None


def test_closes_long_drops_missing_and_keeps_iso_dates():
    idx = pd.to_datetime(["2026-09-17", "2026-09-18"])
    cols = pd.MultiIndex.from_product([["Close", "Open"], ["CLV26.NYM", "BZV26.NYM"]])
    frame = pd.DataFrame([[1.0, None, 0, 0], [2.0, None, 0, 0]], index=idx, columns=cols)
    out = fut.closes_long(frame)
    assert list(out.columns) == ["date", "symbol", "close"]
    assert out.to_dict("records") == [
        {"date": "2026-09-17", "symbol": "CLV26.NYM", "close": 1.0},
        {"date": "2026-09-18", "symbol": "CLV26.NYM", "close": 2.0}]
    assert fut.closes_long(pd.DataFrame()).empty


@pytest.mark.parametrize(("settles", "label"), [
    ([5.0, 4.0, 4.0, 3.0], "backwardation"),
    ([3.0, None, 4.0, 5.0], "contango"),
    ([3.0, 4.0, 3.5], "mixed"),
    ([3.0, 3.0], "mixed"),
    ([3.0, None], None),
])
def test_curve_shape_labels(settles, label):
    assert fut.curve_shape(settles)["label"] == label


@pytest.mark.parametrize(("spread", "label"), [
    (3.01, "healthy"), (3.0, "thinning"), (2.0, "thinning"), (1.99, "flat_case_gone"),
    (-0.5, "flat_case_gone"), (None, None)])
def test_health_label_boundaries(spread, label):
    assert fut.health_label(spread, {"healthy": 3.0, "thinning": 2.0}) == label


def test_session_changes_use_the_session_calendar_and_never_fill():
    sessions = ["d1", "d2", "d3", "d4"]
    series = pd.Series({"d1": 1.0, "d3": 4.0, "d4": 6.0})
    ch = fut.session_changes(series, sessions, (1, 2, 3, 5))
    assert ch[1] == {"change": 2.0, "from_date": "d3", "reason": None}
    assert ch[2]["change"] is None and ch[2]["from_date"] == "d2"
    assert ch[3]["change"] == 5.0
    assert ch[5]["change"] is None and "fewer than 5" in ch[5]["reason"]


# ---------- public API, offline ----------

def test_curve_cl_uses_latest_complete_session(seeded):
    env = fut.curve("CL")
    d = env["data"]
    assert env["provenance"]["quality"] == "snapshot"
    assert "settlement proxy" in env["provenance"]["source"]
    assert env["provenance"]["official_settlement_url"].endswith(
        "light-sweet-crude.settlements.html")
    assert d["curve_date"] == "2026-09-18"
    assert any("partial row for 2026-09-21" in w for w in env["warnings"])
    m = d["months"]
    assert len(m) == 24 and m[0]["contract"] == "CLV26" and m[1]["contract"] == "CLX26"
    assert m[1]["expiry"] == "2026-10-20" and m[1]["expiry_source"] == fut.EXPIRY_SOURCE
    assert m[0]["first_notice"] is None
    expect = _close("CL", "CLV26.NYM", "2026-09-18") - _close("CL", "CLX26.NYM", "2026-09-18")
    assert d["spreads"]["M1-M2"]["value"] == pytest.approx(expect)
    assert d["spreads"]["M1-M12"]["legs"] == ["CLV26", "CLU27"]
    assert d["shape"]["label"] == "backwardation"
    assert [n["item"] for n in env["not_verified"]] == ["CL first notice dates"]
    assert "contractSpecs" in env["not_verified"][0]["url"]


def test_curve_bz_keeps_unresolved_months_empty(seeded):
    env = fut.curve("BZ")
    rows = {r["contract"]: r for r in env["data"]["months"]}
    assert env["data"]["months"][0]["contract"] == "BZX26"
    assert rows["BZH28"]["settle"] is None and rows["BZH28"]["expiry"] is None
    assert rows["BZM28"]["settle"] is not None
    missing = {n["item"] for n in env["not_verified"]}
    assert {"BZV26.NYM settle", "BZH28.NYM settle", "BZV28.NYM settle"} <= missing


def test_curve_skips_expired_contracts(seeded, monkeypatch):
    monkeypatch.setenv("OPTIONSLAB_ASOF", "2026-09-23")
    assert fut.curve("CL", months=12)["data"]["months"][0]["contract"] == "CLX26"


def test_curve_rejects_bad_args(seeded):
    with pytest.raises(ValueError):
        fut.curve("XX")
    with pytest.raises(ValueError):
        fut.curve("CL", months=25)


def test_prompt_spread_cl_health_changes_and_note(seeded):
    env = fut.prompt_spread("CL")
    d = env["data"]
    expect = _close("CL", "CLV26.NYM", "2026-09-21") - _close("CL", "CLX26.NYM", "2026-09-21")
    assert (d["front"], d["second"], d["spread_date"]) == ("CLV26", "CLX26", "2026-09-21")
    assert d["spread"] == pytest.approx(expect)
    assert d["health"] == "healthy" and d["thresholds"] == {"healthy": 3.0, "thinning": 2.0}
    then = _close("CL", "CLV26.NYM", "2026-09-14") - _close("CL", "CLX26.NYM", "2026-09-14")
    assert d["change_5_from_date"] == "2026-09-14"
    assert d["change_5"] == pytest.approx(expect - then)
    assert d["change_21_from_date"] == "2026-08-20"
    assert "Do not annualize" in d["annualize_note"]
    assert any("expires 2026-09-22" in w for w in env["warnings"])


def test_prompt_spread_reads_user_thresholds(seeded):
    config.save({"prompt_spread_thresholds": {"healthy": 5.0, "thinning": 3.5}})
    assert fut.prompt_spread("CL")["data"]["health"] == "thinning"


def test_prompt_spread_thresholds_not_applied_outside_crude(seeded):
    d = fut.prompt_spread("HO")["data"]
    assert d["health"] is None and d["thresholds"] is None
    assert "$/gal" in d["thresholds_note"]


def test_cracks_use_matching_delivery_months(seeded):
    env = fut.cracks()
    by = {c["name"]: c for c in env["data"]["cracks"]}
    assert set(by) == {"ulsd_crack", "rbob_crack", "crack_321", "brent_wti"}
    ulsd = by["ulsd_crack"]
    assert ulsd["delivery_month"] == "2026-10"
    assert [leg["contract"] for leg in ulsd["legs"]] == ["HOV26", "CLV26"]
    day = ulsd["value_date"]
    assert ulsd["value"] == pytest.approx(
        _close("HO", "HOV26.NYM", day) * 42 - _close("CL", "CLV26.NYM", day))
    c321 = by["crack_321"]
    assert c321["value"] == pytest.approx(
        (2 * _close("RB", "RBV26.NYM", day) * 42 + _close("HO", "HOV26.NYM", day) * 42
         - 3 * _close("CL", "CLV26.NYM", day)) / 3)
    bw = by["brent_wti"]
    assert bw["delivery_month"] == "2026-11"
    assert [leg["contract"] for leg in bw["legs"]] == ["BZX26", "CLX26"]
    assert bw["change_5"] is not None and bw["change_21"] is not None


def test_contract_events_are_expiries_in_calendar_shape(seeded):
    events = fut.contract_events(["CL", "NG"])
    assert events[0]["date"] == "2026-09-22" and events[0]["event"].startswith("CLV26")
    assert set(events[0]) == {"date", "event", "source_url", "verified", "fetched_at", "notes"}
    assert not any("first notice" in e["event"] for e in events)


def test_offline_without_snapshots_is_not_verified(offline_home, monkeypatch):
    monkeypatch.setattr(fut, "_download", lambda *_: pytest.fail("network used"))
    env = fut.curve("NG")
    assert env["status"] == NOT_VERIFIED and env["data"] is None
    assert fut.contract_events(["NG"]) == []
