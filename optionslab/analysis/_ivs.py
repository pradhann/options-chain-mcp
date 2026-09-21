"""IV resolution — one place that decides what sigma each leg uses.

Accepts the union shape every analysis converged on:
  * a scalar (broadcast to every leg)
  * a list aligned with the legs
  * None (auto: pull each leg's IV from the chain at its strike+expiration)

Centralized here so `valuation`, `scenario`, and friends all behave the
same.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from ..core.position import Position

IvSpec = float | Sequence[float] | None


def resolve_ivs(position: Position, ivs: IvSpec) -> list[float]:
    """Return a list of decimals, one per leg, in leg order.

    Raises ValueError when auto-resolution would need a symbol/expiration
    that isn't set — explicit failure beats a silent zero.
    """
    n = len(position.legs)
    if ivs is None:
        return _auto_resolve(position)
    if np.isscalar(ivs):
        return [float(ivs)] * n
    out = [float(v) for v in ivs]
    if len(out) != n:
        raise ValueError(
            f"ivs length {len(out)} doesn't match {n} legs"
        )
    return out


def _auto_resolve(position: Position) -> list[float]:
    """Pull each leg's IV from the live chain at its strike + expiration."""
    if position.symbol is None:
        raise ValueError(
            "ivs=None requires position.symbol to be set "
            "(or pass an explicit ivs scalar/list)"
        )
    from ..data.chain import iv_at_strike, load_chain

    cache: dict[str, object] = {}
    ivs: list[float] = []
    for lg in position.legs:
        if lg.expiration is None:
            raise ValueError(
                f"ivs=None needs each leg's expiration set; missing on {lg}"
            )
        if lg.expiration not in cache:
            cache[lg.expiration] = load_chain(
                position.symbol, expiration=lg.expiration, greeks=False
            )
        ch = cache[lg.expiration]
        df = ch.calls if lg.is_call else ch.puts
        iv = iv_at_strike(df, lg.strike)
        if iv is None:
            raise ValueError(
                f"No usable IV at {position.symbol} {lg.strike} "
                f"{lg.option_type} {lg.expiration}; pass ivs explicitly"
            )
        ivs.append(iv)
    return ivs
