"""IV Crush — extracting event-implied move from two expirations.

For a single name with an event between two listed expirations (front
straddles the event, back is later), the difference in variance is the
event's implied variance contribution:

    v_event = (σ²_back · T_back  −  σ²_front · T_front)
              / (T_back − T_front)        (variance per year)

Convert to a 1-day implied move via σ_event · √(1/252) · S.

This is the front-balloon-over-candle picture from the Day-7 brief.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ...data.chain import load_chain
from ...errors import OptionsLabError
from ...pricing import year_fraction
from .atm import atm_iv


@dataclass(frozen=True, slots=True)
class EventVolResult:
    """Event-implied 1-day move from two straddling expirations."""

    asof: str
    symbol: str
    spot: float
    front_expiration: str
    back_expiration: str
    front_iv_pct: float
    back_iv_pct: float
    front_T_years: float
    back_T_years: float
    event_var_annualized_pct2: float
    event_iv_pct: float
    implied_one_day_move_pct: float
    implied_one_day_move_dollars: float

    def to_dict(self) -> dict:
        return {
            "asof": self.asof, "symbol": self.symbol,
            "spot": round(self.spot, 4),
            "front_expiration": self.front_expiration,
            "back_expiration": self.back_expiration,
            "front_iv_pct": round(self.front_iv_pct, 3),
            "back_iv_pct": round(self.back_iv_pct, 3),
            "front_T_years": round(self.front_T_years, 5),
            "back_T_years": round(self.back_T_years, 5),
            "event_var_annualized_pct2": round(self.event_var_annualized_pct2, 4),
            "event_iv_pct": round(self.event_iv_pct, 3),
            "implied_one_day_move_pct": round(self.implied_one_day_move_pct, 3),
            "implied_one_day_move_dollars":
                round(self.implied_one_day_move_dollars, 4),
        }


def event_implied_move(
    symbol: str,
    front_expiration: str,
    back_expiration: str,
) -> EventVolResult:
    """Forward-variance event extraction between two expirations.

    Uses each expiration's ATM IV (interpolated in log-moneyness from
    the chain). The two IVs and tenors give one event-variance number.
    """
    ch_front = load_chain(symbol, expiration=front_expiration, greeks=False)
    ch_back = load_chain(symbol, expiration=back_expiration, greeks=False)

    iv_front = atm_iv(ch_front.calls, ch_front.spot)
    iv_back = atm_iv(ch_back.calls, ch_back.spot)
    if iv_front is None or iv_back is None:
        raise OptionsLabError(
            "Could not extract ATM IV from one or both expirations"
        )

    T_front = year_fraction(front_expiration)
    T_back = year_fraction(back_expiration)
    if T_back <= T_front:
        raise OptionsLabError(
            f"back_expiration must be strictly later than front "
            f"(got T_front={T_front:.4f}, T_back={T_back:.4f})"
        )

    # Variance decomposition (both IVs are in percent → convert to decimals).
    s_front = iv_front / 100.0
    s_back = iv_back / 100.0
    var_event_per_year = (s_back ** 2 * T_back - s_front ** 2 * T_front) \
        / (T_back - T_front)
    iv_event = float(np.sqrt(max(var_event_per_year, 0.0)) * 100)
    one_day_move_pct = float(np.sqrt(max(var_event_per_year, 0.0)) / np.sqrt(252) * 100)
    spot = ch_front.spot
    return EventVolResult(
        asof=pd.Timestamp.now().strftime("%Y-%m-%d"),
        symbol=ch_front.symbol, spot=spot,
        front_expiration=front_expiration,
        back_expiration=back_expiration,
        front_iv_pct=iv_front, back_iv_pct=iv_back,
        front_T_years=T_front, back_T_years=T_back,
        event_var_annualized_pct2=var_event_per_year * 10_000,  # to (%)²
        event_iv_pct=iv_event,
        implied_one_day_move_pct=one_day_move_pct,
        implied_one_day_move_dollars=spot * one_day_move_pct / 100.0,
    )
