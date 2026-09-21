"""Items 11-12 and the spot quote: yfinance `info` reads with provenance.

  spot_quote(symbol)       last regular-session price with its timestamp and
                           yfinance marketState
  risk_free_rate()         13-week T-bill (^IRX) last daily close, decimal
  dividend_policy(symbol)  next declared ex-date (when in the future), the
                           last four declared amounts, and the policy yield
                           (latest amount x inferred payments per year / spot)
                           beside the trailing yield
  analyst_targets(symbol)  count, high, low, and staleness ONLY. yfinance
                           supplies no per-analyst revision dates, so no mean
                           is returned (a mean needs >= 3 revisions inside 30
                           days, which this provider cannot show).
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..storage import snapshots as snap
from .bars import corporate_actions
from .envelope import envelope, iso, num, provenance, unsourced, unverified
from .recorded import recorded

INFO_SOURCE = "yfinance Ticker.info (Yahoo quoteSummary)"
_INFO_FIELDS = (
    "marketState", "regularMarketPrice", "regularMarketTime",
    "postMarketPrice", "postMarketTime", "preMarketPrice", "preMarketTime",
    "currency", "quoteType", "exDividendDate", "lastDividendValue",
    "lastDividendDate", "dividendRate", "trailingAnnualDividendYield",
    "targetHighPrice", "targetLowPrice", "numberOfAnalystOpinions",
    "sharesOutstanding", "shortName",
)
_MARKET_STATE = {"REGULAR": "regular", "PRE": "pre", "PREPRE": "pre",
                 "POST": "post", "POSTPOST": "post", "CLOSED": "closed"}


def _yahoo_url(symbol: str) -> str:
    return f"https://finance.yahoo.com/quote/{symbol}"


def _epoch_iso(v) -> str | None:
    t = num(v)
    return iso(datetime.fromtimestamp(t, UTC)) if t else None


def quote_summary(symbol: str):
    """Recorded subset of yfinance `info` (raises OfflineMiss / LookupError)."""
    import yfinance as yf

    def fetch():
        raw = yf.Ticker(symbol).info or {}
        if not raw.get("regularMarketPrice") and not raw.get("quoteType"):
            raise LookupError(f"yfinance info empty for {symbol}")
        return {k: raw.get(k) for k in _INFO_FIELDS}

    return recorded("info", symbol.upper(), "info", fetch)


def spot_quote(symbol: str) -> dict:
    """Last regular-session price, its exchange timestamp, and session."""
    symbol = symbol.upper()
    try:
        rec = quote_summary(symbol)
    except (snap.OfflineMiss, LookupError) as e:
        return unsourced(f"{symbol} spot", str(e), _yahoo_url(symbol), source=INFO_SOURCE)
    v = rec.value
    price = num(v.get("regularMarketPrice"))
    session = _MARKET_STATE.get(str(v.get("marketState")).upper())
    data = {
        "spot": price,
        "spot_time": _epoch_iso(v.get("regularMarketTime")),
        "extended_price": num(v.get("postMarketPrice") or v.get("preMarketPrice")),
        "currency": v.get("currency"),
    }
    quality = "snapshot" if rec.from_snapshot else ("delayed" if session == "regular" else "stale")
    prov = provenance(source=INFO_SOURCE, source_url=_yahoo_url(symbol), asof=rec.fetched_at,
                      session=session, quality=quality)
    if price is None:
        return envelope(None, prov=prov, not_verified=[unverified(f"{symbol} spot", "no regularMarketPrice")])
    return envelope(data, prov=prov, warnings=[rec.note] if rec.note else [])


def risk_free_rate() -> dict:
    """13-week T-bill discount yield (^IRX) from the last daily close, decimal."""
    import yfinance as yf

    def fetch():
        h = yf.Ticker("^IRX").history(period="10d")
        if h.empty:
            raise LookupError("yfinance returned no ^IRX history")
        return {"close_pct": float(h["Close"].iloc[-1]),
                "bar_date": h.index[-1].strftime("%Y-%m-%d")}

    try:
        rec = recorded("rates", "IRX", "daily", fetch)
    except (snap.OfflineMiss, LookupError) as e:
        return unsourced("risk-free rate", str(e), _yahoo_url("^IRX"), source="yfinance ^IRX")
    prov = provenance(source="yfinance ^IRX daily close", source_url=_yahoo_url("%5EIRX"),
                      asof=rec.fetched_at, quality="snapshot" if rec.from_snapshot else "delayed",
                      bar_date=rec.value["bar_date"])
    return envelope({"r": round(rec.value["close_pct"] / 100.0, 5),
                     "bar_date": rec.value["bar_date"]}, prov=prov)


def _payments_per_year(ex_dates: list[str]) -> int | None:
    """Infer frequency from the median gap between the last ex-dates."""
    if len(ex_dates) < 2:
        return None
    ds = sorted(datetime.fromisoformat(d) for d in ex_dates)
    gaps = sorted((b - a).days for a, b in zip(ds, ds[1:], strict=False))
    median_gap = gaps[len(gaps) // 2]
    return max(1, round(365 / median_gap)) if median_gap > 0 else None


def dividend_policy(symbol: str) -> dict:
    """Declared next ex-date, last four amounts, policy vs trailing yield."""
    symbol = symbol.upper()
    try:
        rec = quote_summary(symbol)
    except (snap.OfflineMiss, LookupError) as e:
        return unsourced(f"{symbol} dividends", str(e), _yahoo_url(symbol), source=INFO_SOURCE)
    v = rec.value
    actions = corporate_actions(symbol)
    history = actions["data"]["dividends"] if actions["data"] else []
    last_four = history[-4:]
    amounts = [d["amount"] for d in last_four]
    per_year = _payments_per_year([d["ex_date"] for d in last_four])
    spot = num(v.get("regularMarketPrice"))

    not_verified = []
    ex_iso = _epoch_iso(v.get("exDividendDate"))
    next_ex = ex_iso[:10] if ex_iso and ex_iso[:10] > snap.today_et().isoformat() else None
    if next_ex is None:
        not_verified.append(unverified("next declared ex-date",
                               "provider shows no ex-date after today", _yahoo_url(symbol)))
    policy_yield = (round(amounts[-1] * per_year / spot, 5)
                    if amounts and per_year and spot else None)
    data = {
        "next_ex_date": next_ex,
        "next_amount": None,  # yfinance does not carry the declared next amount
        "last_four": last_four,
        "variable": len(set(amounts)) > 1 if len(amounts) >= 2 else None,
        "payments_per_year_inferred": per_year,
        "policy_yield": policy_yield,
        "trailing_yield": num(v.get("trailingAnnualDividendYield")),
    }
    not_verified.append(unverified("next declared amount",
                           "yfinance exposes last paid, not declared-next; read the "
                           "dividend declaration (8-K / press release)", _yahoo_url(symbol)))
    prov = provenance(source=f"{INFO_SOURCE}; dividends from yfinance history",
                      source_url=_yahoo_url(symbol), asof=rec.fetched_at,
                      quality="snapshot" if rec.from_snapshot else "delayed")
    notes = ["policy_yield = latest declared amount x inferred payments/yr / spot; "
             "use it, not the trailing average, for variable-dividend names"]
    return envelope(data, prov=prov, warnings=notes, not_verified=not_verified)


def analyst_targets(symbol: str) -> dict:
    """Target count and range as a staleness read. No mean, by design."""
    symbol = symbol.upper()
    try:
        rec = quote_summary(symbol)
    except (snap.OfflineMiss, LookupError) as e:
        return unsourced(f"{symbol} targets", str(e), _yahoo_url(symbol), source=INFO_SOURCE)
    v = rec.value
    data = {
        "count": v.get("numberOfAnalystOpinions"),
        "high": num(v.get("targetHighPrice")),
        "low": num(v.get("targetLowPrice")),
        "provider_asof": None,
        "staleness": "unknown",
        "mean": None,
    }
    prov = provenance(source=INFO_SOURCE, source_url=f"{_yahoo_url(symbol)}/analysis",
                      asof=rec.fetched_at, quality="snapshot" if rec.from_snapshot else "delayed")
    return envelope(data, prov=prov, warnings=[
        "yfinance gives no per-analyst revision dates; the mean is withheld because it "
        "cannot be shown to rest on >= 3 revisions inside 30 days",
        "targets are a staleness signal, not a price level, breakeven test, or direction",
    ])
