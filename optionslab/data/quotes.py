"""Quote-layer fetchers — spot, expirations, dividend yield, risk-free.

Pure I/O. Functions raise typed errors instead of printing or exiting so
notebooks / adapters can decide what to do.
"""

from __future__ import annotations

import yfinance as yf

from ..errors import (
    ExpirationNotFoundError,
    NoOptionsDataError,
    OptionsLabError,
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
    requested: str | None = None,
    index: int | None = None,
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
    """Trailing annual dividend yield, decimal (0.018 = 1.8%); 0.0 for non-payers.

    Raises OptionsLabError when the provider cannot be read, so an
    unreadable yield is never mistaken for a non-payer.
    """
    from ..feeds import fundamentals
    from ..storage.snapshots import OfflineMiss

    try:
        value = fundamentals.quote_summary(ticker.ticker).value.get("trailingAnnualDividendYield")
    except (OfflineMiss, LookupError) as e:
        raise OptionsLabError(f"dividend yield unavailable for {ticker.ticker}: {e}") from e
    return float(value) if value and value > 0 else 0.0


def get_risk_free_rate() -> float:
    """13-week T-bill yield (^IRX) as a decimal.

    Raises OptionsLabError when ^IRX cannot be read: pass `r` explicitly
    rather than price on a rate nobody sourced.
    """
    from ..feeds import fundamentals

    env = fundamentals.risk_free_rate()
    if env["data"] is None:
        raise OptionsLabError(
            "risk-free rate unavailable (^IRX); pass r explicitly. "
            f"{env['not_verified'][0]['reason']}")
    return env["data"]["r"]
