# Data feeds

`optionslab.feeds` is the sourced data layer. One rule governs it: **a number
the tool cannot source is a number it does not return.** Every result is an
envelope:

```json
{
  "status": "ok | partial | not_verified",
  "provenance": {"asof": "...", "source": "...", "source_url": "...",
                 "session": "regular | pre | post | closed", "quality": "live | delayed | stale | last_only | snapshot | unavailable"},
  "data": {...},
  "warnings": ["..."],
  "not_verified": [{"item": "...", "reason": "...", "url": "..."}]
}
```

Missing is `null`, never zero. No value is interpolated, estimated, or taken from memory.

## Feeds

| Module | Spec item | Source | What it returns |
|---|---|---|---|
| `chains` | 1 | yfinance chain, CBOE delayed JSON | mid from two-sided quotes, IV from mid with bid/ask bands, parity spot, FEED_SUSPECT, usability, OI walls |
| `chain_history` | 2 | own snapshots | ATM IV, 25-delta skew, OI by strike, OI change DoD/WoW |
| `bars` | 3 | yfinance history (3y) | daily bars, dividends/splits, the 5x3 RV matrix with worst cells and gap signature |
| `futures` | 4 | yfinance month-coded contracts | curves (CL, BZ, HO, RB, NG), prompt spread health, cracks, expiries |
| `etf_roll` | 5 | USCF prospectus + roll-date CSV | roll rule with quoted evidence, window, expected roll yield; holdings `not_verified` |
| `eia` | 6 | EIA API v2 | WPSR by PADD vs five-year band, lowest-since, STEO |
| `cot` | 7 | CFTC Socrata, ICE COT CSV | managed money net/gross, % OI, 1y/3y percentiles, staleness |
| `edgar` | 8 | EDGAR full-text search, submissions, Form 4 XML | the capital-structure sweep with excerpts; SEDAR+/SEDI `not_verified` for Canadian issuers |
| `calendar` | 9 | EIA, CFTC, Fed, OPEC pages; yfinance + Nasdaq | dated events with `source_url` and `verified` |
| `polymarket` | 10 | Gamma API | probability, 24h/7d change, resolution text |
| `order_check` | — | the chain snapshot | fill, size vs strike OI, spread cost, breakeven vs implied move, risk-neutral median / P(total loss) / P(profit), IV vs RV |
| `fundamentals` | 11-12 | yfinance info | spot quote, ^IRX rate, dividend policy, analyst targets (no mean) |
| `book` | 13 | `.optionslab/ledger.csv` + chains | marks, P&L, delta-notional per name vs cap |
| `pretrade` | — | all of the above | the one-call pre-trade page |

## Snapshots and offline mode

Every fetch writes a dated copy under `.optionslab/snapshots/` (raw HTTP bodies
and yfinance reads alike). History that no free provider keeps (IV, OI, curves)
accrues there.

```bash
OPTIONSLAB_OFFLINE=1 optionslab pretrade SU 2027-03-19
```

- `OPTIONSLAB_OFFLINE=1` reads snapshots only; the network is never touched.
- `OPTIONSLAB_ASOF=YYYY-MM-DD` pins offline reads to the latest snapshot on or before that date.
- `OPTIONSLAB_HOME` moves the `.optionslab` directory.

## Setup

| Variable | Needed for |
|---|---|
| `OPTIONSLAB_SEC_UA="Your Name you@example.com"` | SEC: www.sec.gov refuses requests without a contact |
| `EIA_API_KEY` | EIA: `DEMO_KEY` works but is rate limited ([register](https://www.eia.gov/opendata/register.php)) |

`.optionslab/config.json` holds your lists and thresholds (never market data):
`watchlist`, `book_value`, `delta_notional_cap_pct`, `usability`,
`prompt_spread_thresholds`, `cot_stale_days`, `polymarket`, `ir_calendar_urls`,
`pretrade_context`.

## Daily and weekly jobs

- `optionslab refresh-all` at 15:45 ET on trading days snapshots every feed for
  the watchlist and open ledger positions.
- `optionslab weekly-check` (Sundays) reads prompt spreads, the configured EIA
  series, COT staleness, Form 4 codes, the next 45 days of calendar, and
  Polymarket alerts.

## Not feeds (tier 3)

Dated Brent and other physical assessments, tanker rates, Kpler/Vortexa flows,
the WCS differential, dealer gamma, and news sentiment are not fetched. Enter
them by hand with a document reference (`sec_manual_entry` for sweep items), or
they stay `not_verified`.
