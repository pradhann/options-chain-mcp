"""Vectorized Black-Scholes pricing and Greeks — the analytics foundation.

This is the engine ~10 features compose on (chain Greeks, theoretical
pricing, scenario grids, roll candidates). We own the math rather than lean
on py_vollib because it must be vectorized, dependency-stable, and testable;
py_vollib is kept only as a cross-check oracle in the test suite.

Conventions (returned in every Greeks dict so numbers are never ambiguous):
  delta  — dV per +$1 in spot
  gamma  — d(delta) per +$1 in spot
  theta  — dV per +1 CALENDAR day (time decay; usually negative for longs)
  vega   — dV per +1 IV POINT (i.e. +0.01 in sigma)
  rho    — dV per +1 PERCENTAGE POINT in the risk-free rate (+0.01 in r)

All inputs use decimals: sigma 0.42 = 42% vol, r 0.045 = 4.5%, q 0.018 = 1.8%.
Time is ACT/365.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Union

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm

ArrayLike = Union[float, int, np.ndarray]

_SECONDS_PER_YEAR = 365.0 * 24.0 * 3600.0


def year_fraction(expiration: str, now: Optional[datetime] = None) -> float:
    """Years from `now` to an expiration date 'YYYY-MM-DD' (ACT/365).

    Options stop trading at the close; we anchor expiry to 16:00 local on
    the expiration date. Never returns negative (clamped at 0.0).
    """
    now = now or datetime.now()
    exp = datetime.strptime(expiration, "%Y-%m-%d").replace(hour=16, minute=0)
    return max((exp - now).total_seconds() / _SECONDS_PER_YEAR, 0.0)


def _validate_option_type(option_type: str) -> int:
    if option_type == "call":
        return 1
    if option_type == "put":
        return -1
    raise ValueError(f"option_type must be 'call' or 'put', got {option_type!r}")


def d1_d2(S: ArrayLike, K: ArrayLike, T: ArrayLike, r: float,
          sigma: ArrayLike, q: float = 0.0):
    """The two Black-Scholes auxiliary terms (vectorized)."""
    S = np.asarray(S, dtype=float)
    K = np.asarray(K, dtype=float)
    T = np.maximum(np.asarray(T, dtype=float), 1e-12)
    sigma = np.maximum(np.asarray(sigma, dtype=float), 1e-12)
    vol_t = sigma * np.sqrt(T)
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * T) / vol_t
    return d1, d1 - vol_t


def _all_scalar(*xs) -> bool:
    return all(np.isscalar(x) for x in xs)


def bs_price(S: ArrayLike, K: ArrayLike, T: ArrayLike, r: float,
             sigma: ArrayLike, option_type: str, q: float = 0.0) -> ArrayLike:
    """Black-Scholes price with continuous dividend yield q.

    At/after expiry (T<=0) returns intrinsic value. Returns a float when
    all array-like inputs are scalars, else a numpy array.
    """
    cp = _validate_option_type(option_type)
    S_a = np.asarray(S, dtype=float)
    K_a = np.asarray(K, dtype=float)
    T_a = np.asarray(T, dtype=float)

    intrinsic = np.maximum(cp * (S_a - K_a), 0.0)
    d1, d2 = d1_d2(S_a, K_a, T_a, r, sigma, q)
    disc_S = S_a * np.exp(-q * T_a)
    disc_K = K_a * np.exp(-r * T_a)
    priced = cp * (disc_S * norm.cdf(cp * d1) - disc_K * norm.cdf(cp * d2))

    out = np.where(T_a > 0.0, priced, intrinsic)
    return float(out) if _all_scalar(S, K, T, sigma) else out


def bs_greeks(S: ArrayLike, K: ArrayLike, T: ArrayLike, r: float,
              sigma: ArrayLike, option_type: str,
              q: float = 0.0) -> dict[str, ArrayLike]:
    """Greeks in the conventions documented at module top.

    Includes a `conventions` key so any consumer (MCP/Claude) reads the
    units alongside the numbers.
    """
    cp = _validate_option_type(option_type)
    S_a = np.asarray(S, dtype=float)
    K_a = np.asarray(K, dtype=float)
    T_a = np.maximum(np.asarray(T, dtype=float), 1e-12)
    sig = np.maximum(np.asarray(sigma, dtype=float), 1e-12)

    d1, d2 = d1_d2(S_a, K_a, T_a, r, sig, q)
    pdf = norm.pdf(d1)
    eqt = np.exp(-q * T_a)
    ert = np.exp(-r * T_a)
    sqrtT = np.sqrt(T_a)

    delta = cp * eqt * norm.cdf(cp * d1)
    gamma = eqt * pdf / (S_a * sig * sqrtT)
    vega_raw = S_a * eqt * pdf * sqrtT
    theta_annual = (
        -(S_a * eqt * pdf * sig) / (2.0 * sqrtT)
        - cp * r * K_a * ert * norm.cdf(cp * d2)
        + cp * q * S_a * eqt * norm.cdf(cp * d1)
    )
    rho_raw = cp * K_a * T_a * ert * norm.cdf(cp * d2)

    # --- higher-order Greeks (W2.D6) -------------------------------------
    # Vanna  = ∂Δ/∂σ = ∂Vega/∂S. Per 1 IV point (0.01 sigma).
    vanna_raw = -eqt * pdf * d2 / sig
    # Vomma  = ∂Vega/∂σ. Per 1 IV point squared (0.01² sigma).
    vomma_raw = vega_raw * d1 * d2 / sig
    # Charm  = ∂Δ/∂t  (per CALENDAR day). Standard BS form, dividend-aware:
    #   dΔ/dT  =  −q·e^(−qT) N(d1)
    #             + e^(−qT) · N'(d1) · [ (r − q)/(σ√T) − d2/(2T) ]      (call)
    #   put version is the same but with N(d1) → N(d1) − 1 outside the
    #   pdf term — handled by the sign cp on the leading N(d1) term.
    charm_annual = (
        - q * cp * eqt * norm.cdf(cp * d1)
        + eqt * pdf * ((r - q) / (sig * sqrtT) - d2 / (2.0 * T_a))
    )
    # Convert dΔ/dT → dΔ/dt (calendar time, t = − τ), then per-day.
    charm_per_day = -charm_annual / 365.0

    is_scalar = _all_scalar(S, K, T, sigma)
    sc = (lambda x: float(x)) if is_scalar else (lambda x: x)
    return {
        "delta": sc(delta),
        "gamma": sc(gamma),
        "theta": sc(theta_annual / 365.0),     # per calendar day
        "vega": sc(vega_raw * 0.01),            # per 1 IV point
        "rho": sc(rho_raw * 0.01),              # per 1 pct-point in r
        # Higher-order
        "vanna": sc(vanna_raw * 0.01),          # Δ-change per +1 IV point
        "vomma": sc(vomma_raw * (0.01 ** 2)),   # Vega-change per +1 IV point
        "charm": sc(charm_per_day),             # Δ-change per +1 calendar day
        "conventions": {
            "delta": "per +$1 spot",
            "gamma": "per +$1 spot",
            "theta": "per +1 calendar day",
            "vega":  "per +1 IV point (0.01 sigma)",
            "rho":   "per +1 percentage point in r (0.01)",
            "vanna": "Δ-change per +1 IV point (0.01 sigma)",
            "vomma": "Vega-change per +1 IV point",
            "charm": "Δ-change per +1 calendar day",
        },
    }


def implied_vol(price: float, S: float, K: float, T: float, r: float,
                option_type: str, q: float = 0.0,
                lo: float = 1e-4, hi: float = 5.0) -> Optional[float]:
    """Solve BS for sigma given a market price (Brent root-find).

    Returns None when the price is outside no-arbitrage bounds (no IV
    exists) rather than raising — callers iterate over many strikes.
    """
    _validate_option_type(option_type)
    if T <= 0 or price <= 0:
        return None
    intrinsic = max((1 if option_type == "call" else -1) * (S - K), 0.0)
    if price < intrinsic - 1e-8:
        return None

    def f(sig: float) -> float:
        return bs_price(S, K, T, r, sig, option_type, q) - price

    try:
        if f(lo) * f(hi) > 0:
            return None
        return float(brentq(f, lo, hi, maxiter=100, xtol=1e-6))
    except (ValueError, RuntimeError):
        return None
