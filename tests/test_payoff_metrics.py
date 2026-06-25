"""Payoff and closed-form metrics for the bull call spread.

These are the canonical Day-3 / Day-4 numbers from the curriculum:
  bull call 100/110 debit $3.50 ⇒ max P $650, max L −$350, BE $103.50.
"""

from __future__ import annotations

import pytest

from optionslab.analysis import expiration_payoff, position_metrics


def test_payoff_at_max_profit(bull_call_spread):
    res = expiration_payoff(bull_call_spread, 115)
    assert res.total_per_share == pytest.approx(6.5)
    assert res.total_dollars == pytest.approx(650.0)


def test_payoff_at_max_loss(bull_call_spread):
    res = expiration_payoff(bull_call_spread, 90)
    assert res.total_per_share == pytest.approx(-3.5)
    assert res.total_dollars == pytest.approx(-350.0)


def test_payoff_at_breakeven_is_zero(bull_call_spread):
    res = expiration_payoff(bull_call_spread, 103.5)
    assert res.total_dollars == pytest.approx(0.0, abs=1e-6)


def test_payoff_vectorized(bull_call_spread):
    """An array S_T returns array totals of matching length."""
    res = expiration_payoff(bull_call_spread, [90.0, 103.5, 115.0])
    assert res.total_dollars == pytest.approx([-350.0, 0.0, 650.0], abs=1e-6)


def test_metrics_closed_form(bull_call_spread):
    m = position_metrics(bull_call_spread)
    assert m.max_profit == pytest.approx(650.0)
    assert m.max_loss == pytest.approx(-350.0)
    assert len(m.breakevens) == 1
    assert m.breakevens[0] == pytest.approx(103.5, abs=1e-4)
    assert m.net_entry_cost_dollars == pytest.approx(350.0)


def test_long_call_metrics_unbounded():
    """A bare long call has unlimited max profit, capped loss = premium."""
    from optionslab import Position
    pos = Position.from_dicts([
        {"side": "long", "option_type": "call",
         "strike": 100, "premium": 5},
    ], name="long-100-call")
    m = position_metrics(pos)
    assert m.max_profit == "Unlimited"
    assert m.max_loss == pytest.approx(-500.0)
    assert m.breakevens[0] == pytest.approx(105.0, abs=1e-4)


def test_short_put_metrics_capped():
    """Short put: profit capped at premium, loss capped at K − premium (× 100)."""
    from optionslab import Position
    pos = Position.from_dicts([
        {"side": "short", "option_type": "put",
         "strike": 50, "premium": 3},
    ], name="short-50-put")
    m = position_metrics(pos)
    assert m.max_profit == pytest.approx(300.0)
    assert m.max_loss == pytest.approx(-4700.0)
    assert m.breakevens[0] == pytest.approx(47.0, abs=1e-4)
