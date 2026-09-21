"""ATM-IV and delta interpolation across a chain.

A chain prints IV at discrete listed strikes. To talk about "30-day ATM
IV" or "25-delta IV" we interpolate. Linear in log-moneyness for ATM,
linear in delta for delta-targeting — same conventions practitioners
use on a dealer surface.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def atm_iv(df: pd.DataFrame, spot: float) -> float | None:
    """ATM IV (%) by linear interpolation in log-moneyness around K=spot.

    Returns None if the chain has fewer than two strikes with usable IV.
    """
    valid = df[df["IV %"] > 0][["Strike", "IV %"]].dropna()
    if len(valid) < 2:
        return None
    k = np.log(valid["Strike"].to_numpy(dtype=float) / spot)
    iv = valid["IV %"].to_numpy(dtype=float)
    order = np.argsort(k)
    k, iv = k[order], iv[order]
    return float(np.interp(0.0, k, iv))


def iv_at_delta(df: pd.DataFrame, target_delta: float,
                option_type: str) -> float | None:
    """IV (%) at exactly `target_delta` by linear interpolation in delta.

    `target_delta` is signed conventionally: +0.25 for a 25Δ call,
    -0.25 for a 25Δ put. Returns None if Delta isn't in the chain or
    the target lies outside the listed delta range.
    """
    if "Delta" not in df.columns:
        return None
    valid = df[(df["IV %"] > 0)].dropna(subset=["Delta", "IV %"])
    if len(valid) < 2:
        return None
    sign = 1 if option_type == "call" else -1
    d = valid["Delta"].to_numpy(dtype=float) * sign        # all positive after this
    iv = valid["IV %"].to_numpy(dtype=float)
    tgt = abs(target_delta)
    order = np.argsort(d)
    d, iv = d[order], iv[order]
    if tgt < d.min() or tgt > d.max():
        return None
    return float(np.interp(tgt, d, iv))


def interpolate_term_iv(term: dict[str, float], target_days: int = 30,
                        now: pd.Timestamp | None = None) -> float | None:
    """Interpolate the IV curve {expiration: IV%} at `target_days` to expiry.

    Linear in days-to-expiry. Returns None if the target is outside the
    available expirations.
    """
    if not term:
        return None
    now = now or pd.Timestamp.now()
    days = np.array([(pd.Timestamp(exp) - now).days for exp in term])
    ivs = np.array(list(term.values()), dtype=float)
    order = np.argsort(days)
    days, ivs = days[order], ivs[order]
    if target_days < days.min() or target_days > days.max():
        return None
    return float(np.interp(target_days, days, ivs))
