"""Week 1 + Week 2 curriculum coverage tests — offline.

  * Synthetic position verifier — long/short stock under put-call parity
  * Higher-order Greeks finite-difference cross-checks (vanna, vomma, charm)
  * Vega call ≡ vega put (W2.D5 consistency)
"""

from __future__ import annotations

import pytest

from optionslab import bs_greeks
from optionslab.analysis import (
    long_stock_payoff,
    short_stock_payoff,
    synthetic_long_stock,
    synthetic_short_stock,
    verify_synthetic,
)

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
