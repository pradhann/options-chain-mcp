"""Scenario grid: shape + a deterministic cell value."""

from __future__ import annotations

import pytest

from optionslab.analysis import scenario_grid


def test_grid_shape(bull_call_spread, market_atm):
    res = scenario_grid(
        bull_call_spread, market_atm,
        spot_pcts=[-10, 0, 10], days_forward=[0, 30],
        ivs=0.30,
    )
    assert len(res.pnl_dollars) == 3
    assert all(len(row) == 2 for row in res.pnl_dollars)
    assert res.spot_prices == pytest.approx([90.0, 100.0, 110.0])


def test_grid_cell_equals_value_call(bull_call_spread, market_atm):
    """Cell (pct=0, days=0) must equal the standalone valuation PnL."""
    from optionslab.analysis import value
    v = value(bull_call_spread, market_atm, ivs=0.30)
    res = scenario_grid(
        bull_call_spread, market_atm,
        spot_pcts=[0], days_forward=[0],
        ivs=0.30,
    )
    assert res.pnl_dollars[0][0] == pytest.approx(
        v.total_pnl_vs_entry_dollars, abs=0.05  # grid rounds to 2dp
    )


def test_grid_caps_at_max_profit(bull_call_spread, market_atm):
    """Far up and far in time the spread is capped at $650."""
    res = scenario_grid(
        bull_call_spread, market_atm,
        spot_pcts=[40], days_forward=[90],  # 90 days = at/past expiry
        ivs=0.30,
    )
    assert res.pnl_dollars[0][0] <= 650.0
