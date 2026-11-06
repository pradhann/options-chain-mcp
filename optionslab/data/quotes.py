"""Quote-layer fetchers — spot, expirations, dividend yield, risk-free.

Pure I/O. Functions raise typed errors instead of printing or exiting so
notebooks / adapters can decide what to do.
"""

from __future__ import annotations

from typing import Optional

import yfinance as yf

from ..errors import (
    ExpirationNotFoundError,
    NoOptionsDataError,
    SpotUnavailableError,
)


def make_ticker(symbol: str) -> yf.Ticker:
    """Wrap a symbol in a yfinance Ticker. No network call yet."""
    if not symbol or not symbol.strip():
        raise ValueError("symbol must be a non-empty string")
    return yf.Ticker(symbol.strip().upper())


def get_spot(ticker: yf.Ticker) -> float:
    """Current underlying price. Tries fast quote, then daily close."""
    try:
        price = ticker.fast_info.get("last_price")
        if price:
            return float(price)
    except Exception:
        pass
    try:
        hist = ticker.history(period="1d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    raise SpotUnavailableError(
        f"No spot price available for {getattr(ticker, 'ticker', '?')}"
    )


def list_expirations(ticker: yf.Ticker) -> list[str]:
    """All listed expiration dates, nearest first."""
    expirations = list(ticker.options or ())
    if not expirations:
        raise NoOptionsDataError(
            f"No options listed for {getattr(ticker, 'ticker', '?')}"
        )
    return expirations


def resolve_expiration(
    expirations: list[str],
    *,
    requested: Optional[str] = None,
    index: Optional[int] = None,
) -> str:
    """Pick one expiration: explicit index, then date, else nearest."""
    if index is not None:
        if not 0 <= index < len(expirations):
            raise ExpirationNotFoundError(
                f"exp-index {index} out of range (0..{len(expirations) - 1})"
            )
        return expirations[index]
    if requested is not None:
        if requested not in expirations:
            preview = ", ".join(expirations[:5])
            raise ExpirationNotFoundError(
                f"Expiration {requested!r} not listed. Available: {preview} ..."
            )
        return requested
    return expirations[0]


def get_dividend_yield(ticker: yf.Ticker) -> float:
    """Best-effort continuous dividend yield as a decimal (0.018 = 1.8%).

    yfinance is inconsistent across fields; we normalize and fall back to
    0.0 rather than raising.
    """
    try:
        info = ticker.info
    except Exception:
        return 0.0
    v = info.get("trailingAnnualDividendYield") or info.get("dividendYield")
    if v is None:
        return 0.0
    v = float(v)
    if v <= 0:
        return 0.0
    # >1 can only be a percent (1.8 meaning 1.8%); normalize.
    return v / 100.0 if v > 1.0 else v


def get_risk_free_rate(default: float = 0.045) -> float:
    """Live 13-week T-bill yield (^IRX) as a decimal; `default` on failure."""
    try:
        irx = yf.Ticker("^IRX")
        price = irx.fast_info.get("last_price")
        if not price:
            hist = irx.history(period="5d")
            price = float(hist["Close"].iloc[-1]) if not hist.empty else None
        if price and price > 0:
            return float(price) / 100.0
    except Exception:
        pass
    return default
