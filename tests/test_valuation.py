"""Valuation and Greeks aggregation tests (deterministic — no network)."""

from __future__ import annotations

import pytest

from optionslab import bs_greeks
from optionslab.analysis import greeks, value


def test_portfolio_delta_equals_sum_of_leg_deltas(bull_call_spread, market_atm):
    """Σ sign·qty·δ — the basic aggregation contract for portfolio Greeks."""
    g = greeks(bull_call_spread, market_atm, ivs=0.30)
    # Compute legs by hand at the same T (use the leg expirations from fixture).
    legs = bull_call_spread.legs
    from optionslab.pricing import year_fraction
    T = year_fraction(legs[0].expiration, market_atm.asof)
    long_delta = bs_greeks(100, 100, T, 0.045, 0.30, "call")["delta"]
    short_delta = bs_greeks(100, 110, T, 0.045, 0.30, "call")["delta"]
    expected = long_delta - short_delta
    assert g.delta == pytest.approx(expected, abs=1e-6)
    assert g.delta_dollars == pytest.approx(expected * 100, abs=1e-4)


def test_value_pnl_vs_entry_sign(bull_call_spread, market_atm):
    """At spot=100 with σ=0.30, this debit spread should still show a
    positive model value below the debit paid (depends on T)."""
    v = value(bull_call_spread, market_atm, ivs=0.30)
    # Total value must equal the sum of the leg values.
    assert v.total_value_dollars == pytest.approx(sum(v.leg_values_dollars))
    # And per-leg P&L vs entry must equal value − sign·premium·qty·100.
    legs = bull_call_spread.legs
    expected_pnl = [
        v.leg_values_dollars[i] - lg.sign * lg.qty * 100 * lg.premium
        for i, lg in enumerate(legs)
    ]
    assert v.leg_pnls_vs_entry_dollars == pytest.approx(expected_pnl, abs=1e-4)


def test_ivs_scalar_broadcast(bull_call_spread, market_atm):
    """Scalar σ broadcasts to every leg with no behavior difference vs list."""
    a = greeks(bull_call_spread, market_atm, ivs=0.30)
    b = greeks(bull_call_spread, market_atm, ivs=[0.30, 0.30])
    assert a.delta == pytest.approx(b.delta)
    assert a.theta == pytest.approx(b.theta)


def test_ivs_length_mismatch_raises(bull_call_spread, market_atm):
    with pytest.raises(ValueError, match="length"):
        greeks(bull_call_spread, market_atm, ivs=[0.30, 0.30, 0.30])
