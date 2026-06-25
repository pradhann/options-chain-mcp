"""Pricing engine tests: textbook value, parity, Greeks, IV roundtrip."""

from __future__ import annotations

import math

import numpy as np
import pytest

from optionslab import bs_greeks, bs_price, implied_vol


# Hull's canonical textbook example: S=K=100, r=5%, σ=20%, T=1 → 10.4506.
def test_bs_price_textbook_value():
    assert bs_price(100, 100, 1.0, 0.05, 0.20, "call") == pytest.approx(
        10.4506, abs=1e-4
    )


def test_put_call_parity_with_dividend():
    """C − P = S·e^(−qT) − K·e^(−rT) for any S, K, T, σ."""
    S, K, T, r, sig, q = 105.0, 100.0, 0.5, 0.04, 0.30, 0.02
    c = bs_price(S, K, T, r, sig, "call", q)
    p = bs_price(S, K, T, r, sig, "put", q)
    rhs = S * math.exp(-q * T) - K * math.exp(-r * T)
    assert (c - p) == pytest.approx(rhs, abs=1e-10)


def test_pyvollib_oracle_agreement():
    """Our analytic BS must match the py_vollib reference to 1e-6."""
    pv = pytest.importorskip("py_vollib.black_scholes")
    bs = pv.black_scholes
    for ot, flag in (("call", "c"), ("put", "p")):
        ours = bs_price(100, 95, 0.5, 0.04, 0.30, ot)
        oracle = bs(flag, 100, 95, 0.5, 0.04, 0.30)
        assert ours == pytest.approx(oracle, abs=1e-6)


def test_greeks_finite_difference_delta_vega():
    """Analytic delta and vega match a central finite-difference bump."""
    S, K, T, r, sig = 100, 100, 1.0, 0.05, 0.20
    g = bs_greeks(S, K, T, r, sig, "call")
    h = 1e-4
    fd_delta = (bs_price(S + h, K, T, r, sig, "call")
                - bs_price(S - h, K, T, r, sig, "call")) / (2 * h)
    fd_vega = ((bs_price(S, K, T, r, sig + 1e-5, "call")
                - bs_price(S, K, T, r, sig - 1e-5, "call"))
               / (2e-5) * 0.01)
    assert g["delta"] == pytest.approx(fd_delta, abs=1e-6)
    assert g["vega"] == pytest.approx(fd_vega, abs=1e-6)


def test_delta_bounds_and_monotonicity():
    """Call delta ∈ (0, 1) and increases with S; put delta is its mirror."""
    S = np.linspace(50, 150, 50)
    d_call = bs_greeks(S, 100, 0.5, 0.04, 0.30, "call")["delta"]
    d_put = bs_greeks(S, 100, 0.5, 0.04, 0.30, "put")["delta"]
    assert (d_call > 0).all() and (d_call < 1).all()
    assert (np.diff(d_call) > 0).all()
    assert (d_put < 0).all() and (d_put > -1).all()


def test_implied_vol_roundtrip():
    """A price priced at σ, then inverted, returns the same σ."""
    sigma = 0.27
    price = bs_price(100, 105, 0.5, 0.04, sigma, "put")
    recovered = implied_vol(price, 100, 105, 0.5, 0.04, "put")
    assert recovered == pytest.approx(sigma, abs=1e-5)
