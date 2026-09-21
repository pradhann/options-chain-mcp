"""Envelope contract, snapshot store, and offline HTTP replay."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from optionslab.feeds import http
from optionslab.feeds.envelope import NOT_VERIFIED, clean, envelope, provenance, unverified
from optionslab.storage import snapshots as snap


def test_envelope_status_follows_not_verified_lines():
    prov = provenance(source="test")
    assert envelope({"x": 1}, prov=prov)["status"] == "ok"
    assert envelope({"x": 1}, prov=prov, not_verified=[unverified("y", "why")])["status"] == "partial"
    assert envelope(None, prov=prov)["status"] == NOT_VERIFIED


def test_clean_turns_nan_into_none_never_zero():
    assert clean({"a": float("nan"), "b": [float("inf"), 1.5]}) == {"a": None, "b": [None, 1.5]}


def test_provenance_rejects_unknown_quality():
    with pytest.raises(ValueError):
        provenance(source="x", quality="approximate")


def test_offline_get_reads_latest_snapshot_on_or_before_asof(offline_home, monkeypatch):
    url = "https://example.com/data.csv?api_key=SECRET&x=1"
    http.record(url, b"old", day=date(2026, 9, 1))
    http.record(url, b"new", day=date(2026, 9, 20))

    got = http.get(url)
    assert got.body == b"new" and got.from_snapshot
    assert "SECRET" not in got.url

    monkeypatch.setenv("OPTIONSLAB_ASOF", "2026-09-10")
    assert http.get(url).body == b"old"


def test_offline_get_without_snapshot_raises(offline_home):
    with pytest.raises(snap.OfflineMiss):
        http.get("https://example.com/never-fetched")


def test_table_roundtrip_and_history(offline_home):
    df = pd.DataFrame({"strike": [100.0, 105.0], "oi": [10, 20]})
    snap.write_table("chains", "SU", "2027-04-16", df, meta={"spot": 101}, day=date(2026, 9, 18))
    snap.write_table("chains", "SU", "2027-04-16", df.assign(oi=[11, 25]), day=date(2026, 9, 19))

    frame, meta, day = snap.read_table("chains", "SU", "2027-04-16")
    assert day == date(2026, 9, 19) and list(frame["oi"]) == [11, 25] and meta == {}
    assert [d for d, _, _ in snap.history("chains", "SU", "2027-04-16")] == [
        date(2026, 9, 18), date(2026, 9, 19)]
