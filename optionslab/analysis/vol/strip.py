"""Model-free VIX as a 1/K²-weighted OTM-strip — the Day-6 mechanic.

We don't derive the formula. We compute it and feel it:

    σ²(T)  =  (2/T) · Σ_K  ΔK/K² · e^(rT) · Q(K)    −   (1/T)·(F/K_0 − 1)²

where Q(K) is the OTM option price at K (puts below F, calls above) and
K_0 is the highest strike below F.

The trader-level lesson is the wing sensitivity: bid the OTM puts by
20% and VIX moves materially even if ATM didn't budge. That's why VIX
can spike on a quiet day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf

from ...data.chain import load_chain
from ...data.quotes import get_risk_free_rate, make_ticker
from ...errors import OptionsLabError
from ...pricing import year_fraction


@dataclass(frozen=True, slots=True)
class VixStripResult:
    """Replicated VIX value + comparison + wing-sensitivity check."""

    asof: str
    symbol: str
    front_expiration: str
    back_expiration: str | None
    days_target: int
    replicated_vix: float
    published_vix: float | None
    diff_vol_points: float | None
    wing_boost_pct: float
    replicated_vix_with_wing_boost: float

    def to_dict(self) -> dict:
        return {
            "asof": self.asof, "symbol": self.symbol,
            "front_expiration": self.front_expiration,
            "back_expiration": self.back_expiration,
            "days_target": self.days_target,
            "replicated_vix": round(self.replicated_vix, 3),
            "published_vix": (round(self.published_vix, 3)
                              if self.published_vix is not None else None),
            "diff_vol_points": (round(self.diff_vol_points, 3)
                                if self.diff_vol_points is not None else None),
            "wing_boost_pct": self.wing_boost_pct,
            "replicated_vix_with_wing_boost":
                round(self.replicated_vix_with_wing_boost, 3),
        }


def _strip_variance(df_calls: pd.DataFrame, df_puts: pd.DataFrame,
                    F: float, T: float, r: float,
                    wing_boost: float = 0.0) -> float:
    """One-expiration σ² contribution from the OTM strip.

    `wing_boost` boosts OTM option prices by that fraction (e.g., 0.2
    for the +20% wing sensitivity check).
    """
    # Mid prices: prefer Mid (already in chain), fall back to (bid+ask)/2.
    def mid(df: pd.DataFrame) -> pd.Series:
        if "Mid" in df.columns:
            return df["Mid"]
        return (df["Bid"] + df["Ask"]) / 2

    calls = df_calls.dropna(subset=["Strike"]).copy()
    puts = df_puts.dropna(subset=["Strike"]).copy()
    calls["Q"] = mid(calls)
    puts["Q"] = mid(puts)

    # OTM = put below F, call above F. Right at F we average.
    otm_put = puts[puts["Strike"] < F].sort_values("Strike")
    otm_call = calls[calls["Strike"] > F].sort_values("Strike")
    strip = pd.concat([
        otm_put[["Strike", "Q"]],
        otm_call[["Strike", "Q"]],
    ]).sort_values("Strike").reset_index(drop=True)
    strip = strip[strip["Q"] > 0]
    if len(strip) < 5:
        raise OptionsLabError(
            "Strip needs at least 5 OTM strikes with positive mid prices"
        )

    if wing_boost:
        strip["Q"] = strip["Q"] * (1.0 + wing_boost)

    K = strip["Strike"].to_numpy(dtype=float)
    Q = strip["Q"].to_numpy(dtype=float)
    # ΔK using a centered-difference rule.
    dK = np.empty_like(K)
    dK[0] = K[1] - K[0]
    dK[-1] = K[-1] - K[-2]
    dK[1:-1] = (K[2:] - K[:-2]) / 2.0

    sum_term = float(np.sum(dK / (K ** 2) * np.exp(r * T) * Q))
    # K0 = largest listed strike <= F (forward).
    below = K[K <= F]
    K0 = float(below.max()) if len(below) else float(K.min())
    correction = (F / K0 - 1.0) ** 2 / T
    return (2.0 / T) * sum_term - correction


def vix_strip(symbol: str = "SPX", target_days: int = 30,
              wing_boost_pct: float = 20.0) -> VixStripResult:
    """Replicate the 30-day variance strike from an OTM option strip.

    Picks the two listed expirations straddling `target_days`, computes
    each one's strip variance, linearly interpolates to `target_days`,
    converts to vol points, and compares to the published VIX (when
    `symbol` is SPX). Also re-runs with the wing-boost sensitivity.
    """
    ticker = make_ticker(symbol)
    expirations = list(ticker.options or ())
    if not expirations:
        raise OptionsLabError(f"No options listed for {symbol}")

    now = datetime.now()
    days_list = [(exp, (pd.Timestamp(exp) - now).days) for exp in expirations]
    front = next((e for e, d in days_list if d >= 1), days_list[0][0])
    back = next((e for e, d in days_list if d > target_days), None)

    r = get_risk_free_rate()
    ch_front = load_chain(symbol, expiration=front, greeks=False)
    F_front = ch_front.spot  # approximate forward = spot for short T
    T_front = year_fraction(front)
    var_front = _strip_variance(ch_front.calls, ch_front.puts,
                                F_front, T_front, r)
    if back:
        ch_back = load_chain(symbol, expiration=back, greeks=False)
        F_back = ch_back.spot
        T_back = year_fraction(back)
        var_back = _strip_variance(ch_back.calls, ch_back.puts,
                                   F_back, T_back, r)
        target_T = target_days / 365.0
        if T_front <= target_T <= T_back and T_back > T_front:
            w = (T_back - target_T) / (T_back - T_front)
            var_30 = w * var_front + (1 - w) * var_back
        else:
            var_30 = var_front if abs(T_front - target_T) < abs(T_back - target_T) else var_back
    else:
        ch_back = None
        var_30 = var_front

    replicated_vix = float(np.sqrt(max(var_30, 0.0)) * 100)

    # Wing-boost sensitivity (same machinery, wings +20%).
    var_front_b = _strip_variance(ch_front.calls, ch_front.puts,
                                  F_front, T_front, r,
                                  wing_boost=wing_boost_pct / 100.0)
    if ch_back:
        var_back_b = _strip_variance(ch_back.calls, ch_back.puts,
                                     F_back, T_back, r,
                                     wing_boost=wing_boost_pct / 100.0)
        var_30_b = w * var_front_b + (1 - w) * var_back_b \
            if (T_front <= target_T <= T_back and T_back > T_front) \
            else (var_front_b if abs(T_front - target_T) < abs(T_back - target_T) else var_back_b)
    else:
        var_30_b = var_front_b
    replicated_vix_boost = float(np.sqrt(max(var_30_b, 0.0)) * 100)

    # Compare to published VIX (only meaningful for SPX-derived symbols).
    published: float | None = None
    try:
        vix_t = yf.Ticker("^VIX")
        published = vix_t.fast_info.get("last_price")
        if not published:
            h = vix_t.history(period="5d")
            published = float(h["Close"].iloc[-1]) if not h.empty else None
    except Exception:
        published = None

    return VixStripResult(
        asof=now.strftime("%Y-%m-%d"),
        symbol=symbol.upper(),
        front_expiration=front,
        back_expiration=back,
        days_target=target_days,
        replicated_vix=replicated_vix,
        published_vix=float(published) if published else None,
        diff_vol_points=(replicated_vix - float(published)) if published else None,
        wing_boost_pct=wing_boost_pct,
        replicated_vix_with_wing_boost=replicated_vix_boost,
    )
