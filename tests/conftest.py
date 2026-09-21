"""Shared pytest fixtures.

These positions and contexts are the same numbers I hand-verified
through the session: known max profit / max loss / breakeven /
Greeks. Keep them stable so refactors that break the math fail tests.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from optionslab import MarketContext, Position


@pytest.fixture
def future_exp() -> str:
    """An expiration ~90 days out so all tests use a non-degenerate T."""
    return (datetime(2026, 6, 5) + timedelta(days=90)).strftime("%Y-%m-%d")


@pytest.fixture
def bull_call_spread(future_exp: str) -> Position:
    """The canonical bull call spread: long 100, short 110, debit 3.50."""
    return Position.from_dicts([
        {"side": "long",  "option_type": "call",
         "strike": 100, "premium": 6.0, "qty": 1, "expiration": future_exp},
        {"side": "short", "option_type": "call",
         "strike": 110, "premium": 2.5, "qty": 1, "expiration": future_exp},
    ], name="bull-call-100-110")


@pytest.fixture
def market_atm() -> MarketContext:
    """A deterministic mid-flight context for valuation tests."""
    return MarketContext.explicit(
        spot=100.0, r=0.045, q=0.0,
        asof=datetime(2026, 6, 5, 12, 0, 0),
    )


@pytest.fixture
def offline_home(tmp_path, monkeypatch):
    """An isolated `.optionslab` root with the network switched off."""
    root = tmp_path / ".optionslab"
    root.mkdir()
    monkeypatch.setenv("OPTIONSLAB_HOME", str(root))
    monkeypatch.setenv("OPTIONSLAB_OFFLINE", "1")
    monkeypatch.delenv("OPTIONSLAB_ASOF", raising=False)
    return root
