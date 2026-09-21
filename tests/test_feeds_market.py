"""Chain quality, chain history, OHLC/RV matrix, fundamentals, and the book.

All offline against synthetic snapshots from `market_fixtures`.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from optionslab.feeds import bars, book, chain_history, chains, fundamentals
from optionslab.storage import ledger
from tests import market_fixtures as mf


@pytest.fixture
def market(offline_home, monkeypatch):
    monkeypatch.setenv("OPTIONSLAB_ASOF", mf.DAY.isoformat())
    mf.seed_chain()
    mf.seed_info()
    mf.seed_ohlc()
    return offline_home


# ---------- chain quality ----------

def test_parity_recovers_true_spot_and_iv_recovers_vol(market):
    rep = chains.chain_report("TEST", mf.EXPIRY)
    d = rep["data"]
    assert d["parity"]["spot"] == pytest.approx(mf.SPOT, abs=0.05)
    assert d["spot_check"]["flag"] is None
    atm = [r for r in d["rows"] if r["type"] == "call" and r["strike"] == 100.0][0]
    assert atm["iv_mid_pct"] == pytest.approx(mf.VOL * 100, abs=0.2)
    assert atm["iv_bid_pct"] < atm["iv_mid_pct"] < atm["iv_ask_pct"]
    assert rep["provenance"]["quality"] == "snapshot"


def test_feed_suspect_when_feed_spot_is_off_the_chain(offline_home):
    # The chain is priced at 154.30 while the feed says 158.74 (the G2 case).
    mf.seed_chain(feed_spot=158.74, true_spot=154.30, cboe_spot=None)
    d = chains.chain_report("TEST", mf.EXPIRY)["data"]["spot_check"]
    assert d["flag"] == "FEED_SUSPECT"
    assert d["parity_spot"] == pytest.approx(154.30, abs=0.1)


def test_after_hours_zeros_are_not_prices(offline_home):
    mf.seed_chain(zero_quotes=True)
    rep = chains.chain_report("TEST", mf.EXPIRY)
    rows = rep["data"]["rows"]
    assert all(r["mid"] is None and r["bid"] is None for r in rows)
    assert rep["data"]["usability"]["verdict"] == "unusable"
    assert rep["provenance"]["quality"] == "last_only"


def test_usability_verdicts_follow_thresholds():
    rows = chains.derive_quote_fields(mf.chain_frame(), mf.SPOT, 0.5, mf.R, 0.0)
    th = {"max_median_spread_pct": 10.0, "min_share_oi_500": 0.1}
    assert chains.chain_usability(rows, mf.SPOT, th)["verdict"] == "thin"  # OI 100 everywhere
    deep = chains.derive_quote_fields(mf.chain_frame(oi={("call", k): 900 for k in mf.STRIKES}),
                                      mf.SPOT, 0.5, mf.R, 0.0)
    assert chains.chain_usability(deep, mf.SPOT, th)["verdict"] == "usable"


def test_walls_rank_by_oi_with_percent_of_side(offline_home):
    mf.seed_chain(oi={("call", 120.0): 5000, ("call", 110.0): 2000})
    walls = chains.chain_report("TEST", mf.EXPIRY)["data"]["walls"]
    assert [w["strike"] for w in walls["call"][:2]] == [120.0, 110.0]
    assert walls["call"][0]["pct_of_side"] == pytest.approx(5000 / walls["call_total_oi"] * 100, abs=0.1)


def test_unlisted_expiry_names_neighbours(market):
    rep = chains.chain_report("TEST", "2027-04-16")
    assert rep["status"] == "not_verified"
    assert rep["not_verified"][0]["listed_before"] == mf.EXPIRY


def test_missing_rate_means_no_iv_and_a_not_verified_line(offline_home):
    mf.seed_chain(r=None)
    rep = chains.chain_report("TEST", mf.EXPIRY)
    assert all(r["iv_mid_pct"] is None for r in rep["data"]["rows"])
    assert any(n["item"] == "risk-free rate" for n in rep["not_verified"])


# ---------- chain history ----------

def test_oi_change_and_skew_history_from_snapshots(offline_home, monkeypatch):
    mf.seed_chain(day=date(2026, 9, 10))
    mf.seed_chain(day=date(2026, 9, 17), oi={("call", 120.0): 900})
    mf.seed_chain(day=date(2026, 9, 18), oi={("call", 120.0): 1500})
    monkeypatch.setenv("OPTIONSLAB_ASOF", "2026-09-18")

    chg = chain_history.oi_change("TEST", mf.EXPIRY)["data"]
    assert chg["day_over_day"]["largest_changes"][0] == {
        "type": "call", "strike": 120.0, "oi_then": 900, "oi_now": 1500, "change": 600}
    assert chg["week_over_week"]["from"] == "2026-09-10"

    skew = chain_history.skew_history("TEST", mf.EXPIRY)["data"]
    assert len(skew) == 3
    assert skew[-1]["rr_25d_pts"] == pytest.approx(0.0, abs=0.3)   # flat-vol chain


def test_legacy_skew_csvs_migrate(offline_home):
    legacy = offline_home / "history"
    legacy.mkdir()
    (legacy / "skew_TEST_2027-03-19_RR.csv").write_text("timestamp,value\n2026-06-23T14:42:28,-1.98\n")
    (legacy / "skew_TEST_2027-03-19_BF.csv").write_text("timestamp,value\n2026-06-23T14:42:28,-0.18\n")
    assert chain_history.migrate_legacy_skew()["data"]["dated_rows_written"] == 1
    rows = chain_history.skew_history("TEST", mf.EXPIRY)["data"]
    assert rows[0]["date"] == "2026-06-23" and rows[0]["rr_25d_pts"] == -1.98


# ---------- OHLC and RV ----------

def test_rv_matrix_always_has_fifteen_cells_and_names_worst(market):
    d = bars.rv_matrix("TEST")["data"]
    cells = [v for row in d["matrix"].values() for v in row.values()]
    assert len(cells) == 15 and all(v is not None for v in cells)
    assert d["worst_for_long"]["rv_pct"] == min(cells)
    assert d["worst_for_short"]["rv_pct"] == max(cells)
    assert d["gap_signature"]["flag"] is None


def test_gap_regime_flag_on_overnight_jumps(offline_home, monkeypatch):
    monkeypatch.setenv("OPTIONSLAB_ASOF", mf.DAY.isoformat())
    mf.seed_ohlc(gap=0.02)
    assert bars.rv_matrix("TEST")["data"]["gap_signature"]["flag"] == "GAP_REGIME"


def test_corporate_actions_and_dividend_policy(market):
    divs = bars.corporate_actions("TEST")["data"]["dividends"]
    assert [d["amount"] for d in divs] == [0.50, 0.50, 0.60, 0.75]
    pol = fundamentals.dividend_policy("TEST")
    assert pol["data"]["variable"] is True
    assert pol["data"]["payments_per_year_inferred"] == 4
    assert pol["data"]["policy_yield"] == pytest.approx(0.75 * 4 / mf.SPOT)
    assert any(n["item"] == "next declared amount" for n in pol["not_verified"])


def test_analyst_targets_never_return_a_mean(market):
    mf.seed_info(numberOfAnalystOpinions=3, targetHighPrice=120.0, targetLowPrice=80.0)
    d = fundamentals.analyst_targets("TEST")["data"]
    assert d["mean"] is None and d["staleness"] == "unknown" and d["count"] == 3


def test_offline_without_snapshot_is_not_verified(offline_home):
    assert bars.rv_matrix("NOPE")["status"] == "not_verified"
    assert fundamentals.spot_quote("NOPE")["status"] == "not_verified"


# ---------- book ----------

def test_delta_notional_counts_calls_and_flags_the_cap(market):
    (market / "config.json").write_text(json.dumps({"book_value": 200_000}))
    ledger.open_position(symbol="TEST", instrument="stock", side="long", thesis_id="T1",
                         exit_condition="close below 90", recommended_qty=800,
                         executed_qty=800, entry_price=95.0)
    ledger.open_position(symbol="TEST", instrument="call", side="long", thesis_id="T1",
                         exit_condition="expiry", recommended_qty=25, executed_qty=50,
                         entry_price=8.0, strike=110.0, expiration=mf.EXPIRY)
    b = book.mark_positions()["data"]
    name = b["per_name"]["TEST"]
    call = [p for p in b["positions"] if p["instrument"] == "call"][0]
    assert name["delta_notional"] == pytest.approx(800 * mf.SPOT + call["delta"] * 5000 * mf.SPOT, rel=1e-6)
    assert name["breach"] is True                      # stock alone is 40% of book

    after = book.delta_notional_after({"symbol": "TEST", "instrument": "stock",
                                       "side": "short", "qty": 800})["data"]
    assert after["delta_notional_after"] == pytest.approx(name["delta_notional"] - 800 * mf.SPOT)

    review = book.ledger_review()["data"]
    assert review["executed_above_recommended"][0]["executed_qty"] == 50


def test_close_requires_all_three_review_fields(market):
    row = ledger.open_position(symbol="TEST", instrument="stock", side="long", thesis_id="T1",
                               exit_condition="x", recommended_qty=1, executed_qty=1,
                               entry_price=1.0)
    with pytest.raises(ValueError):
        ledger.close_position(row.id, exit_price=2.0, outcome="win", right_for_reason="",
                              autopsy="ok")
    ledger.close_position(row.id, exit_price=2.0, outcome="win", right_for_reason="yes",
                          autopsy="thesis played out")
    assert ledger.blank_reviews() == []
    assert not ledger.current()[row.id].is_open


def test_legacy_chain_mid_is_nan_on_one_sided_quotes():
    import pandas as pd

    from optionslab.data.chain import add_value_decomposition
    df = pd.DataFrame({"Strike": [100.0, 105.0], "Bid": [0.0, 2.0], "Ask": [1.5, 2.2]})
    out = add_value_decomposition(df, 100.0, "call")
    assert pd.isna(out.loc[0, "Mid"]) and out.loc[1, "Mid"] == 2.1
