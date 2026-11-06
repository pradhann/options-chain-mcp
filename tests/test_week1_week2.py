"""Week 1 + Week 2 curriculum coverage tests — offline.

  * Curriculum-named helpers (option_payoff / portfolio_payoff / position_delta)
  * Synthetic position verifier — long/short stock under put-call parity
  * Higher-order Greeks finite-difference cross-checks (vanna, vomma, charm)
  * Vega call ≡ vega put (W2.D5 consistency)
"""

from __future__ import annotations

import numpy as np
import pytest

from optionslab import bs_greeks, bs_price
from optionslab.analysis import (
    long_stock_payoff,
    option_payoff,
    portfolio_payoff,
    position_delta,
    short_stock_payoff,
    synthetic_long_stock,
    synthetic_short_stock,
    verify_synthetic,
)


# ---------- curriculum-named helpers ----------

def test_option_payoff_long_call_matches_algebra():
    """Long call @ K=100 prem 5: at S_T=110, P&L = 110-100-5 = 5."""
    v = option_payoff(110, strike=100, option_type="call",
                      position="long", premium=5)
    assert v == pytest.approx(5.0)


def test_option_payoff_short_put_unlimited_downside_capped():
    """Short put @ K=50 prem 3: at S_T=20, P&L = 3 − (50−20) = −27."""
    v = option_payoff(20, strike=50, option_type="put",
                      position="short", premium=3)
    assert v == pytest.approx(-27.0)


def test_portfolio_payoff_bull_call_spread():
    """Bull call 100/110 debit 3.50: at S_T=115, dollar P&L = +$650."""
    legs = [
        {"side": "long",  "option_type": "call",
         "strike": 100, "premium": 6, "qty": 1},
        {"side": "short", "option_type": "call",
         "strike": 110, "premium": 2.5, "qty": 1},
    ]
    out = portfolio_payoff(115, legs)
    assert out["total_per_share"] == pytest.approx(6.5)
    assert out["total_dollars"] == pytest.approx(650.0)


def test_position_delta_matches_hand_calc():
    """Σ sign · qty · δ — single ATM call, S=100, K=100, T=0.25, σ=0.3."""
    legs = [{"side": "long", "option_type": "call",
             "strike": 100, "premium": 5, "qty": 1}]
    d = position_delta(legs, S=100, T=0.25, r=0.045, sigma=0.30)
    expected = bs_greeks(100, 100, 0.25, 0.045, 0.30, "call")["delta"]
    assert d == pytest.approx(expected, abs=1e-9)


# ---------- synthetic verifier (W1.D6) ----------

def test_synthetic_long_stock_matches_within_offset():
    """Long call + short put at K matches +1 share's payoff up to a
    parallel premium offset.

    The offset IS the no-arbitrage cost of the synthetic (parity-priced
    premiums would zero it out). Residual after removing the offset
    must be zero numerically.
    """
    synth = synthetic_long_stock(strike=100, call_premium=6.0, put_premium=5.5)
    res = verify_synthetic(long_stock_payoff(100), synth,
                           s_range=(50, 150), points=21)
    assert res.matches_within_tol is True
    assert res.residual_max < 1e-9


def test_synthetic_short_stock_matches_within_offset():
    synth = synthetic_short_stock(strike=100, call_premium=6.0, put_premium=5.5)
    res = verify_synthetic(short_stock_payoff(100), synth,
                           s_range=(50, 150), points=21)
    assert res.matches_within_tol is True
    assert res.residual_max < 1e-9


# ---------- higher-order Greeks (W2.D6) ----------

@pytest.fixture(scope="module")
def standard_inputs():
    return dict(S=100.0, K=100.0, T=0.5, r=0.04, sigma=0.30, q=0.02)


def test_vanna_matches_finite_difference(standard_inputs):
    """Vanna = ∂Δ/∂σ. Compare analytic to centered FD bump."""
    i = standard_inputs
    h = 1e-5
    g = bs_greeks(option_type="call", **i)
    fd = (bs_greeks(i["S"], i["K"], i["T"], i["r"], i["sigma"] + h, "call", i["q"])["delta"]
          - bs_greeks(i["S"], i["K"], i["T"], i["r"], i["sigma"] - h, "call", i["q"])["delta"]) / (2 * h) * 0.01
    assert g["vanna"] == pytest.approx(fd, abs=1e-6)


def test_vomma_matches_finite_difference(standard_inputs):
    """Vomma = ∂Vega/∂σ. FD over sigma."""
    i = standard_inputs
    h = 1e-5
    g = bs_greeks(option_type="call", **i)
    fd = (bs_greeks(i["S"], i["K"], i["T"], i["r"], i["sigma"] + h, "call", i["q"])["vega"]
          - bs_greeks(i["S"], i["K"], i["T"], i["r"], i["sigma"] - h, "call", i["q"])["vega"]) / (2 * h) * 0.01
    assert g["vomma"] == pytest.approx(fd, abs=1e-6)


def test_charm_matches_finite_difference(standard_inputs):
    """Charm = ∂Δ/∂t (per calendar day; t = − τ)."""
    i = standard_inputs
    h_T = 1.0 / 365
    g = bs_greeks(option_type="call", **i)
    fd = (bs_greeks(i["S"], i["K"], i["T"] - h_T, i["r"], i["sigma"], "call", i["q"])["delta"]
          - bs_greeks(i["S"], i["K"], i["T"] + h_T, i["r"], i["sigma"], "call", i["q"])["delta"]) / (2 * h_T) / 365
    assert g["charm"] == pytest.approx(fd, abs=1e-5)


# ---------- W2.D5 sanity: vega(call) == vega(put) ----------

def test_vega_call_equals_vega_put():
    """At the same K, T, r, σ, q, vega is identical for calls and puts."""
    args = dict(S=100, K=100, T=0.5, r=0.045, sigma=0.30, q=0.02)
    vc = bs_greeks(option_type="call", **args)["vega"]
    vp = bs_greeks(option_type="put", **args)["vega"]
    assert vc == pytest.approx(vp, abs=1e-12)
