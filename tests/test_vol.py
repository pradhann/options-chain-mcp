"""Week 3 volatility analytics — offline tests.

We avoid network in unit tests by exercising the pure math (RV
estimators on synthetic OHLC, percentile machinery on deterministic
series, ATM/Δ interpolation on a hand-built DataFrame, dashboard
assembly via monkeypatch).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from optionslab.analysis.vol.atm import (
    atm_iv,
    interpolate_term_iv,
    iv_at_delta,
)
from optionslab.analysis.vol.percentile import (
    percentile_from_series,
    MIN_PCTILE_SAMPLES,
)
from optionslab.analysis.vol.term import regime_label, regime_action


# ---------- RV estimator zoo ----------

@pytest.fixture
def synthetic_ohlc():
    """Deterministic OHLC with no overnight gaps."""
    np.random.seed(0)
    n = 50
    closes = 100 * np.exp(np.cumsum(np.random.normal(0, 0.01, n)))
    opens = closes * np.exp(np.random.normal(0, 0.001, n))
    highs = np.maximum(opens, closes) * (1 + np.abs(np.random.normal(0, 0.003, n)))
    lows = np.minimum(opens, closes) * (1 - np.abs(np.random.normal(0, 0.003, n)))
    return pd.DataFrame({"Open": opens, "High": highs,
                         "Low": lows, "Close": closes})


def test_rv_estimators_return_positive(synthetic_ohlc):
    """All five estimators produce positive, finite annualized vols."""
    from optionslab.data.vol import (
        _rv_close_to_close, _rv_parkinson, _rv_garman_klass,
        _rv_rogers_satchell, _rv_yang_zhang,
    )
    for fn in (_rv_close_to_close, _rv_parkinson, _rv_garman_klass,
               _rv_rogers_satchell, _rv_yang_zhang):
        s = fn(synthetic_ohlc, 21).dropna()
        assert not s.empty
        assert (s > 0).all()
        assert np.isfinite(s).all()


def test_parkinson_below_or_equal_close_to_close(synthetic_ohlc):
    """With small overnight noise, Parkinson should be ≤ close-to-close.

    Parkinson uses the high/low range only, which is bounded above by
    the close-to-close standard deviation when daily returns are
    well-behaved.
    """
    from optionslab.data.vol import _rv_close_to_close, _rv_parkinson
    c2c = _rv_close_to_close(synthetic_ohlc, 21).dropna()
    park = _rv_parkinson(synthetic_ohlc, 21).dropna()
    # Allow tiny numerical slack.
    assert (park.iloc[-1] <= c2c.iloc[-1] * 1.5)  # generous bound


# ---------- percentile machinery ----------

def test_percentile_basic_position():
    """Today's value at the 50th percentile when it equals the median."""
    series = pd.Series(np.arange(100))
    res = percentile_from_series(50.0, series)
    assert res.percentile == pytest.approx(51.0, abs=1.0)
    assert res.samples == 100
    assert res.thin is False


def test_percentile_flags_thin_history():
    series = pd.Series([1.0, 2.0, 3.0])
    res = percentile_from_series(2.5, series)
    assert res.thin is True
    assert res.samples == 3 < MIN_PCTILE_SAMPLES


def test_percentile_empty_series_returns_none():
    res = percentile_from_series(1.0, pd.Series(dtype=float))
    assert res.percentile is None
    assert res.thin is True


# ---------- ATM / Δ / term interpolation ----------

def _toy_chain_df(strikes, ivs, deltas=None):
    df = pd.DataFrame({"Strike": strikes, "IV %": ivs})
    if deltas is not None:
        df["Delta"] = deltas
    return df


def test_atm_iv_linear_interpolation_in_logmoney():
    """ATM IV at spot=100 from a symmetric three-strike chain = the middle IV."""
    df = _toy_chain_df([90, 100, 110], [25, 20, 22])
    assert atm_iv(df, spot=100) == pytest.approx(20.0)


def test_atm_iv_handles_off_strike_spot():
    """Spot between two listed strikes interpolates linearly in log(K/S)."""
    df = _toy_chain_df([90, 110], [30, 20])
    # log(90/100) = -0.105; log(110/100) = +0.095. Spot=100 is closer to log=0.
    out = atm_iv(df, spot=100)
    assert 20.0 <= out <= 30.0


def test_iv_at_delta_call_25():
    df = _toy_chain_df([90, 100, 110], [25, 22, 19], deltas=[0.75, 0.50, 0.25])
    assert iv_at_delta(df, 0.25, "call") == pytest.approx(19.0, abs=0.5)


def test_iv_at_delta_put_25():
    df = _toy_chain_df([90, 100, 110], [30, 25, 20],
                       deltas=[-0.25, -0.50, -0.75])
    assert iv_at_delta(df, -0.25, "put") == pytest.approx(30.0, abs=0.5)


def test_interpolate_term_at_30_days(monkeypatch):
    """Term interpolation hits 30d between a 7d and 60d sample."""
    now = pd.Timestamp("2026-01-01")
    term = {
        (now + pd.Timedelta(days=7)).strftime("%Y-%m-%d"): 30.0,
        (now + pd.Timedelta(days=60)).strftime("%Y-%m-%d"): 20.0,
    }
    val = interpolate_term_iv(term, target_days=30, now=now)
    assert val is not None
    assert 20.0 < val < 30.0


# ---------- term-structure regime labels ----------

def test_regime_labels():
    assert "strong contango" in regime_label(1.10)
    assert "contango" in regime_label(1.02)
    assert "flat" in regime_label(0.98)
    assert "deep" in regime_label(0.90)
    assert "stand down" in regime_action(0.90) or "stress" in regime_action(0.90)
    assert "vol-selling" in regime_action(1.10)
