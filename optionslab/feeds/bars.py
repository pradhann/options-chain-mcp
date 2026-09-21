"""Item 3: daily OHLC (three years), corporate actions, and the RV matrix.

Source: yfinance `Ticker.history(period="3y", auto_adjust=False, actions=True)`.
Each read is snapshotted as `snapshots/ohlc/{SYMBOL}/{date}/daily.csv`;
offline mode reads only that.

Realized vol is always the full matrix: five estimators x three windows
(21, 63, 126 sessions) = fifteen cells, in percent. A single cell is never
returned on its own, so the estimator cannot be chosen after the fact.
The matrix names:

  worst_for_long   the lowest cell: least realized movement paid for by a
                   long-premium holder
  worst_for_short  the highest cell: most realized movement a premium
                   seller must absorb
  gap_signature    close_to_close minus parkinson per window, in vol
                   points; GAP_REGIME when any window exceeds 8 points
                   (moves are happening between sessions, which the
                   range estimators do not see)
"""

from __future__ import annotations

import pandas as pd
import yfinance as yf

from ..estimators import ESTIMATORS, rv_series
from ..storage import snapshots as snap
from .envelope import envelope, provenance, unsourced
from .recorded import recorded

SOURCE = "yfinance Ticker.history(period='3y', auto_adjust=False, actions=True)"
RV_WINDOWS = (21, 63, 126)
GAP_REGIME_POINTS = 8.0
_COLUMNS = ["Date", "Open", "High", "Low", "Close", "Adj Close", "Volume",
            "Dividends", "Stock Splits"]


def _yahoo_url(symbol: str) -> str:
    return f"https://finance.yahoo.com/quote/{symbol}/history"


def _download(symbol: str) -> list[dict]:
    hist = yf.Ticker(symbol).history(period="3y", auto_adjust=False, actions=True)
    if hist.empty:
        raise LookupError(f"yfinance returned no history for {symbol}")
    hist = hist.reset_index()
    hist["Date"] = pd.to_datetime(hist["Date"]).dt.strftime("%Y-%m-%d")
    return hist.reindex(columns=_COLUMNS).to_dict("records")


def load_bars(symbol: str) -> tuple[pd.DataFrame, dict] | dict:
    """(frame indexed by date, provenance) or a failed envelope."""
    symbol = symbol.upper()
    try:
        rec = recorded("ohlc", symbol, "daily", lambda: _download(symbol))
    except (snap.OfflineMiss, LookupError) as e:
        return unsourced(f"{symbol} daily OHLC", str(e), _yahoo_url(symbol), source=SOURCE)
    df = pd.DataFrame(rec.value).set_index("Date")
    df.index = pd.to_datetime(df.index)
    prov = provenance(
        source=SOURCE, source_url=_yahoo_url(symbol), asof=rec.fetched_at,
        quality="snapshot" if rec.from_snapshot else "delayed",
        last_bar=df.index[-1].strftime("%Y-%m-%d"), bars=len(df),
    )
    return df, prov


def daily_bars(symbol: str) -> dict:
    """Three years of daily bars: date, OHLC, adjusted close, volume."""
    loaded = load_bars(symbol)
    if isinstance(loaded, dict):
        return loaded
    df, prov = loaded
    out = df[["Open", "High", "Low", "Close", "Adj Close", "Volume"]].reset_index()
    out["Date"] = out["Date"].dt.strftime("%Y-%m-%d")
    return envelope(out.to_dict("records"), prov=prov)


def corporate_actions(symbol: str) -> dict:
    """Dividends and splits by ex-date (yfinance action dates are ex-dates)."""
    loaded = load_bars(symbol)
    if isinstance(loaded, dict):
        return loaded
    df, prov = loaded
    divs = df.loc[df["Dividends"] > 0, "Dividends"]
    splits = df.loc[df["Stock Splits"] > 0, "Stock Splits"]
    return envelope({
        "dividends": [{"ex_date": d.strftime("%Y-%m-%d"), "amount": float(v)}
                      for d, v in divs.items()],
        "splits": [{"ex_date": d.strftime("%Y-%m-%d"), "ratio": float(v)}
                   for d, v in splits.items()],
        "window": [df.index[0].strftime("%Y-%m-%d"), df.index[-1].strftime("%Y-%m-%d")],
    }, prov=prov)


def rv_matrix_from_bars(ohlc: pd.DataFrame) -> dict:
    """The fifteen-cell matrix (percent) with worst cells and gap signature."""
    cells: dict[str, dict[int, float | None]] = {}
    for est in ESTIMATORS:
        cells[est] = {}
        for w in RV_WINDOWS:
            s = rv_series(ohlc, est, w).dropna()
            cells[est][w] = round(float(s.iloc[-1]) * 100, 2) if not s.empty else None

    filled = [(v, est, w) for est, row in cells.items() for w, v in row.items() if v is not None]
    if len(filled) < len(ESTIMATORS) * len(RV_WINDOWS):
        missing = len(ESTIMATORS) * len(RV_WINDOWS) - len(filled)
        partial_note = f"{missing} of 15 cells have too little history"
    else:
        partial_note = None
    lo = min(filled) if filled else None
    hi = max(filled) if filled else None

    gaps = {}
    for w in RV_WINDOWS:
        c2c, park = cells["close_to_close"][w], cells["parkinson"][w]
        gaps[w] = round(c2c - park, 2) if c2c is not None and park is not None else None
    gap_regime = any(g is not None and g > GAP_REGIME_POINTS for g in gaps.values())

    return {
        "units": "annualized vol, percent",
        "windows": list(RV_WINDOWS),
        "matrix": cells,
        "worst_for_long": {"estimator": lo[1], "window": lo[2], "rv_pct": lo[0]} if lo else None,
        "worst_for_short": {"estimator": hi[1], "window": hi[2], "rv_pct": hi[0]} if hi else None,
        "gap_signature": {"c2c_minus_parkinson_pts": gaps,
                          "threshold_pts": GAP_REGIME_POINTS,
                          "flag": "GAP_REGIME" if gap_regime else None},
        "incomplete": partial_note,
    }


def rv_matrix(symbol: str) -> dict:
    """Five estimators x (21, 63, 126) sessions, always all fifteen cells."""
    loaded = load_bars(symbol)
    if isinstance(loaded, dict):
        return loaded
    df, prov = loaded
    result = rv_matrix_from_bars(df)
    warnings = [result["incomplete"]] if result["incomplete"] else []
    return envelope(result, prov=prov, warnings=warnings)
