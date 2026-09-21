"""The pre-trade page offline: sourced sections pass through, the rest say not_verified."""

from __future__ import annotations

import json

import pytest

from optionslab.adapters.cli_feeds import render_pretrade
from optionslab.feeds import pretrade
from tests import market_fixtures as mf


@pytest.fixture
def seeded(offline_home, monkeypatch):
    monkeypatch.setenv("OPTIONSLAB_ASOF", mf.DAY.isoformat())
    mf.seed_chain()
    mf.seed_info()
    mf.seed_ohlc()
    (offline_home / "config.json").write_text(json.dumps({"book_value": 100_000}))
    return offline_home


def test_page_has_every_section_and_never_invents_the_missing_ones(seeded):
    order = {"symbol": "TEST", "instrument": "call", "side": "long", "qty": 10,
             "strike": 100.0, "expiration": mf.EXPIRY}
    page = pretrade.pretrade("TEST", mf.EXPIRY, order)["data"]

    assert tuple(page["sections"]) == pretrade.SECTIONS
    assert page["sections"]["spot"]["data"]["parity_spot"] == pytest.approx(mf.SPOT, abs=0.05)
    assert len(page["sections"]["realized_vol"]["data"]["matrix"]) == 5
    assert page["sections"]["book"]["data"]["pct_of_book_after"] > 0

    # No snapshots exist for curves, EIA, COT, EDGAR, or Polymarket: each section
    # carries no data and lists why, instead of a number.
    for name in ("crack", "physical", "positioning", "sweep"):
        sec = page["sections"][name]
        assert sec["status"] in ("not_verified", "partial")
        assert sec["not_verified"], name
    assert {u["section"] for u in page["unsourced"]} >= {"crack", "physical", "positioning",
                                                         "sweep", "markets"}


def test_unlisted_expiry_uses_neighbours_for_spot_only(seeded):
    page = pretrade.pretrade("TEST", "2027-04-16")["data"]
    assert page["sections"]["spot"]["data"]["expiration_used"] == mf.EXPIRY
    assert page["sections"]["chain"]["data"]["listed"] is False
    assert any(u["item"] == "TEST 2027-04-16 chain" for u in page["unsourced"])


def test_text_page_renders_every_section_and_the_unsourced_list(seeded):
    text = render_pretrade(pretrade.pretrade("TEST", mf.EXPIRY))
    for name in pretrade.SECTIONS:
        assert name.upper() in text
    assert "NOT VERIFIED" in text
