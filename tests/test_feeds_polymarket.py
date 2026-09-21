"""Polymarket feed: parsers, change sourcing, alerts, watchlist, search.

Fixtures are real Gamma API bodies captured 2026-09-21 (the search body is
trimmed to three events of three markets each).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from optionslab.feeds import http
from optionslab.feeds import polymarket as pm
from optionslab.storage import snapshots as snap

FIX = Path(__file__).parent / "fixtures" / "polymarket"
CAPTURED = date(2026, 9, 21)
HORMUZ_SLUG = "strait-of-hormuz-traffic-returns-to-normal-by-december-31"


def _bytes(name: str) -> bytes:
    return (FIX / name).read_bytes()


def _seed_market(key: str, fixture: str) -> None:
    http.record(pm.market_url(key), _bytes(fixture), day=CAPTURED)


@pytest.fixture
def asof(offline_home, monkeypatch):
    monkeypatch.setenv("OPTIONSLAB_ASOF", CAPTURED.isoformat())
    return offline_home


# ---------- pure ----------

def test_market_url_id_vs_slug():
    assert pm.market_url("2176270") == "https://gamma-api.polymarket.com/markets/2176270"
    assert pm.market_url(HORMUZ_SLUG).endswith(f"/markets?slug={HORMUZ_SLUG}")


def test_parse_market_yes_probability_and_resolution():
    m = pm.parse_market_body(json.loads(_bytes("market_slug_hormuz_dec31.json")))
    assert m["id"] == "2176270" and m["slug"] == HORMUZ_SLUG
    assert m["probability"] == 0.195
    assert m["outcomes"] == {"Yes": 0.195, "No": 0.805}
    assert (m["best_bid"], m["best_ask"]) == (0.19, 0.2)
    assert m["gamma_change"] == {"24h": 0.02, "7d": 0.02}
    assert m["resolution_text"].startswith("This market will resolve to")
    assert m["end_date"] == "2027-01-01T04:59:00Z"


def test_parse_market_non_binary_has_no_probability():
    obj = json.loads(_bytes("market_3399468.json"))
    obj["outcomes"] = '["Up", "Down"]'
    m = pm.parse_market(obj)
    assert m["probability"] is None and set(m["outcomes"]) == {"Up", "Down"}


def test_parse_market_body_empty_slug_list():
    with pytest.raises(LookupError):
        pm.parse_market_body([])


def test_change_prefers_gamma_then_own_snapshot():
    m = pm.parse_market(json.loads(_bytes("market_4641064.json")))
    ref = {"read_at": "2026-09-14T15:00:00+00:00", "probability": 0.80}
    assert pm.change(m, "24h", ref) == {"value": 0.12, "source": "gamma:oneDayPriceChange"}
    own = pm.change(m, "7d", ref)
    assert own["source"] == "own_snapshot"
    assert own["value"] == pytest.approx(0.095)
    assert own["reference_read_at"] == ref["read_at"]
    assert pm.change(m, "7d", None) is None


def test_alert():
    assert pm.alert(0.4, 0.35) is True
    assert pm.alert(0.3, 0.35) is False
    assert pm.alert(None, 0.35) is None and pm.alert(0.4, None) is None


def test_parse_search_rows():
    rows = pm.parse_search(json.loads(_bytes("search_hormuz.json")))
    assert rows and {"id", "slug", "question", "end_date", "active"} <= set(rows[0])
    assert rows[0]["slug"] == HORMUZ_SLUG
    assert not any(r["closed"] for r in rows)


# ---------- public, offline ----------

def test_market_by_slug_offline(asof):
    _seed_market(HORMUZ_SLUG, "market_slug_hormuz_dec31.json")
    env = pm.market(HORMUZ_SLUG)
    d = env["data"]
    assert env["status"] == "ok"
    assert env["provenance"]["quality"] == "snapshot"
    assert d["id"] == "2176270" and d["resolution_text"]
    assert d["change_24h"]["source"] == "gamma:oneDayPriceChange"
    assert "gamma_change" not in d


def test_missing_week_change_uses_own_read_or_is_not_verified(asof):
    _seed_market("4641064", "market_4641064.json")
    env = pm.market("4641064")
    assert env["data"]["change_7d"] is None
    assert [n["item"] for n in env["not_verified"]] == ["polymarket 4641064 change_7d"]

    snap.write_json("polymarket", "4641064", "read",
                    {"read_at": "2026-09-14T15:00:00+00:00", "probability": 0.8},
                    day=date(2026, 9, 14))
    env = pm.market("4641064")
    assert env["status"] == "ok"
    assert env["data"]["change_7d"]["source"] == "own_snapshot"
    assert env["data"]["change_7d"]["value"] == pytest.approx(0.095)


def test_one_sided_book_is_flagged(asof):
    _seed_market("4788902", "market_4788902.json")
    env = pm.market("4788902")
    assert env["data"]["probability"] == 0.245
    assert any("one-sided" in w for w in env["warnings"])
    assert {n["item"] for n in env["not_verified"]} == {
        "polymarket 4788902 change_24h", "polymarket 4788902 change_7d"}


def test_unfetchable_market_is_not_verified(asof):
    env = pm.market("no-such-market")
    assert env["status"] == "not_verified" and env["data"] is None


def test_live_read_is_recorded_with_resolution_text(asof, monkeypatch):
    body = _bytes("market_3399468.json")
    live = http.Fetched(url=pm.market_url("3399468"), body=body,
                        fetched_at="2026-09-21T21:30:00+00:00", snapshot_date=CAPTURED,
                        from_snapshot=False)
    monkeypatch.setattr(pm.http, "get", lambda url: live)
    monkeypatch.delenv("OPTIONSLAB_ASOF")   # live reads are filed under today
    env = pm.market("3399468")
    assert env["provenance"]["quality"] == "live"
    doc, _ = snap.read_json("polymarket", "3399468", "read")
    assert doc["id"] == "3399468" and doc["probability"] == 0.685
    assert doc["resolution_text"] == env["data"]["resolution_text"]
    assert doc["read_at"] == "2026-09-21T21:30:00+00:00"


def test_watchlist_markets_with_alerts(asof):
    _seed_market(HORMUZ_SLUG, "market_slug_hormuz_dec31.json")
    _seed_market("3399468", "market_3399468.json")
    (asof / "config.json").write_text(json.dumps({"polymarket": [
        {"slug": HORMUZ_SLUG, "label": "Hormuz normal by Dec 31", "alert_above": 0.15},
        {"id": "3399468", "label": "Israel-Iran ceasefire holds", "alert_above": 0.9},
        {"id": "3399469", "label": "not seeded"},
        {"label": "no key"},
    ]}))
    env = pm.watchlist_markets()
    rows = {r["label"]: r for r in env["data"]["markets"]}
    assert rows["Hormuz normal by Dec 31"]["alert"] is True
    assert rows["Israel-Iran ceasefire holds"]["alert"] is False
    assert env["status"] == "partial"
    assert {"polymarket 3399469", "polymarket entry 'no key'"} <= {
        n["item"] for n in env["not_verified"]}


def test_watchlist_empty_config(asof):
    env = pm.watchlist_markets()
    assert env["data"] == {"markets": []}
    assert env["not_verified"][0]["item"] == "polymarket watchlist"


def test_search_offline(asof):
    http.record(pm.search_url("hormuz"), _bytes("search_hormuz.json"), day=CAPTURED)
    env = pm.search("hormuz")
    assert env["status"] == "ok"
    assert env["data"]["results"][0]["id"] == "2176270"
