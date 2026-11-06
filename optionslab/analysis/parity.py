"""Put-call parity verification on a live chain.

Parity (European, continuous dividend q):

    C − P  =  S · e^(−qT)  −  K · e^(−rT)

For a non-dividend stock (q=0):

    C − P  =  S − K · e^(−rT)

We pull live mids from the chain at the chosen strike+expiration,
compute both sides, and report:
  - the gap (LHS − RHS),
  - whether it falls inside the joined call+put bid-ask range (a benign
    bid/ask artifact) or outside (which would be an arbitrage signal).

This is exactly the W1.D5 build: pull a chain, run the check, document
the gap.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from ..data.chain import load_chain
from ..data.quotes import get_dividend_yield, get_risk_free_rate, make_ticker
from ..errors import OptionsLabError
from ..pricing import year_fraction


@dataclass(frozen=True, slots=True)
class ParityResult:
    """Live put-call parity check at one strike + expiration."""

    symbol: str
    expiration: str
    strike: float
    spot: float
    T_years: float
    r: float
    q: float
    call_mid: float
    put_mid: float
    call_bid: float
    call_ask: float
    put_bid: float
    put_ask: float
    lhs_c_minus_p: float           # market: call mid − put mid
    rhs_synthetic: float           # theory: S·e^(−qT) − K·e^(−rT)
    gap: float                     # LHS − RHS (positive = calls rich vs puts)
    combined_bid_ask: float        # call spread + put spread
    gap_within_bid_ask: bool       # |gap| ≤ combined spread → benign

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol, "expiration": self.expiration,
            "strike": round(self.strike, 4),
            "spot": round(self.spot, 4),
            "T_years": round(self.T_years, 5),
            "r": round(self.r, 4), "q": round(self.q, 4),
            "call_mid": round(self.call_mid, 4),
            "put_mid": round(self.put_mid, 4),
            "call_bid_ask": [round(self.call_bid, 4), round(self.call_ask, 4)],
            "put_bid_ask": [round(self.put_bid, 4), round(self.put_ask, 4)],
            "lhs_c_minus_p": round(self.lhs_c_minus_p, 4),
            "rhs_synthetic": round(self.rhs_synthetic, 4),
            "gap": round(self.gap, 4),
            "combined_bid_ask": round(self.combined_bid_ask, 4),
            "gap_within_bid_ask": self.gap_within_bid_ask,
            "interpretation": _interpret(self.gap, self.combined_bid_ask),
        }


def _interpret(gap: float, ba: float) -> str:
    if abs(gap) <= ba:
        return ("gap is inside the combined bid-ask spread — benign noise; "
                "no actionable arbitrage")
    direction = "calls rich vs puts" if gap > 0 else "puts rich vs calls"
    return (f"gap exceeds the combined bid-ask ({direction}); "
            "investigate dividends, hard-to-borrow, or stale quotes")


def _pick_row(df, strike: float):
    """Return the chain row whose Strike is closest to `strike`."""
    if df.empty:
        raise OptionsLabError("chain side is empty")
    idx = (df["Strike"] - strike).abs().idxmin()
    return df.loc[idx]


def parity_check(
    symbol: str,
    strike: float,
    expiration: str,
    *,
    r: Optional[float] = None,
    q: Optional[float] = None,
) -> ParityResult:
    """Verify put-call parity at one strike on a live chain.

    `r` and `q` default to live values (13-week T-bill, trailing
    dividend yield); override to pin a scenario.
    """
    ticker = make_ticker(symbol)
    chain = load_chain(symbol, expiration=expiration, greeks=False)
    r_used = get_risk_free_rate() if r is None else r
    q_used = get_dividend_yield(ticker) if q is None else q

    call = _pick_row(chain.calls, strike)
    put = _pick_row(chain.puts, strike)
    K = float(call["Strike"])
    if abs(float(put["Strike"]) - K) > 1e-6:
        raise OptionsLabError(
            f"call and put closest strikes disagree "
            f"({call['Strike']} vs {put['Strike']})"
        )

    T = year_fraction(expiration)
    lhs = float(call["Mid"]) - float(put["Mid"])
    rhs = chain.spot * math.exp(-q_used * T) - K * math.exp(-r_used * T)
    combined = (float(call["Ask"]) - float(call["Bid"])) + \
               (float(put["Ask"]) - float(put["Bid"]))
    gap = lhs - rhs

    return ParityResult(
        symbol=chain.symbol, expiration=chain.expiration, strike=K,
        spot=chain.spot, T_years=T, r=r_used, q=q_used,
        call_mid=float(call["Mid"]), put_mid=float(put["Mid"]),
        call_bid=float(call["Bid"]), call_ask=float(call["Ask"]),
        put_bid=float(put["Bid"]), put_ask=float(put["Ask"]),
        lhs_c_minus_p=lhs, rhs_synthetic=rhs, gap=gap,
        combined_bid_ask=combined,
        gap_within_bid_ask=abs(gap) <= combined,
    )
