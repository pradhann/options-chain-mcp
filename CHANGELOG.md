# Changelog

All notable changes to `optionslab` are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project uses
[SemVer](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-09-21

The sourced data layer. One rule: a number the tool cannot source is a number it
does not return; it returns `not_verified` with a reason and a URL instead.

### Added

- **`optionslab.feeds`**: every result is an envelope with `asof`, `source`,
  `source_url`, `session`, `quality`, `warnings`, `not_verified`.
  - `chains`: two-sided mids only, IV from mid with bid/ask bands, parity-implied
    spot, CBOE cross-check and `FEED_SUSPECT`, usability verdict, OI walls.
  - `chain_history`: ATM IV, 25-delta skew, OI change from our own snapshots;
    legacy skew CSVs migrated.
  - `order_check`: fill, size vs strike OI, spread cost, breakeven vs implied
    move, risk-neutral median / P(total loss) / P(profit), IV vs every RV cell.
  - `bars`: three years of daily bars, dividends/splits, the always-15-cell RV
    matrix with worst cells and the gap signature.
  - `futures`: NYMEX month curves (CL, BZ, HO, RB, NG), prompt-spread health,
    cracks on matching delivery months, curve overlay, expiries.
  - `etf_roll`: USCF roll rules quoted from the prospectus, published windows,
    expected roll yield.
  - `eia`: WPSR by PADD vs five-year seasonal bands, lowest-since, STEO.
  - `cot`: CFTC disaggregated (combined) and ICE Brent managed money with
    percentiles, release dates, staleness.
  - `edgar`: the structural sweep (converts, warrants, ATM, hedges, variable
    dividends, buybacks) with excerpts and links, Form 4 trades, share count;
    SEDAR+/SEDI manual entries for Canadian issuers.
  - `calendar`: EIA, STEO, CFTC, FOMC, OPEC+, earnings (two sources), ex-div,
    futures expiries, ETF roll windows — each with a URL and `verified`.
  - `polymarket`: probabilities with the market's resolution text.
  - `book`: append-only ledger, marks, delta-notional per name vs your cap.
  - `pretrade`: the one-call pre-trade page with red flags.
  - `jobs`: `refresh_all` (daily snapshots) and `weekly_check`.
- Dated snapshots of every fetch under `.optionslab/snapshots/`; `OPTIONSLAB_OFFLINE=1`
  replays them without touching the network.
- CLI: `pretrade`, `plot`, `feed`, `ledger`, `refresh-all`, `weekly-check`.
- MCP: 40+ feed and chart tools (`pretrade_page`, `chart_pretrade_sheet`, ...).
- Charts: one theme with a source/as-of footer on every figure; chain quality,
  RV heatmap, curve overlay, cracks, COT, EIA seasonal band, order P&L over the
  risk-neutral distribution, and the eight-panel pre-trade sheet.

### Changed

- `get_spot_price`, `realized_vol`, `analyst_targets`, `next_earnings` return
  sourced envelopes. `realized_vol` returns the full 5x3 matrix;
  `analyst_targets` no longer returns a mean (no revision dates to support one).
- The risk-free rate no longer falls back to 4.5% when ^IRX is unavailable; pass
  `r` explicitly. `MarketContext.explicit` requires `r`.
- A zero or missing bid/ask leaves `Mid` empty instead of 0.
- Charts are saved to `.optionslab/charts/` instead of the working directory.

### Removed

- `realized_vol_extended` (single-cell RV), the syllabus alias helpers
  (`option_payoff`, `portfolio_payoff`, `position_delta`), unused `Position` /
  `Leg` / `MarketContext` convenience methods.

### Fixed

- `chart_greek_time` raised `NameError` when `r` was omitted.
- Dividend yield misread `dividendYield` percent values below 1%.

## [0.1.0] — 2026-06-25

Initial public release.

### Added

- **Core**: `Leg`, `Position`, `MarketContext` dataclasses; typed
  `PayoffResult`, `ValueResult`, `GreeksResult`, `MetricsResult`,
  `ScenarioResult` (all with `.to_dict()` for JSON).
- **Pricing**: vectorized Black-Scholes price and Greeks
  (Δ, Γ, Θ, V, ρ, vanna, vomma, charm), IV solver via Brent.
  Every analytic Greek matches `py_vollib` to 1e-6.
- **Data layer**: option chain with per-strike Greeks, RV estimator zoo
  (close-to-close, Parkinson, Garman-Klass, Rogers-Satchell, Yang-Zhang),
  VIX-family fetcher, earnings / news / analyst targets.
- **Analysis**: payoff, valuation, portfolio Greeks, scenario grid,
  closed-form metrics (max P / max L / breakevens), put-call parity
  checker, synthetic-position verifier.
- **Vol analytics**: VRP today + history, VIX term structure + regime,
  25Δ Risk Reversal / Butterfly, model-free VIX strip + wing sensitivity,
  event-implied move, the daily Vol Dashboard.
- **Plotting**: payoff diagrams (single + 2×2 grids), Greek vs spot and
  Greek vs time, IV smile, VRP history, VIX term, skew curve, VIX strip
  overlay, dashboard tile panel.
- **CLI**: 17 verbs with one consistent position spec; unified `chart`
  dispatcher with 14 kinds.
- **MCP server**: 42 tools mirroring the CLI vocabulary; charts return
  as inline images.
- **Storage**: project-local `.optionslab/positions.json` for named
  positions; per-key CSV history files for percentile accumulation.
- **Tests**: 50 pytest cases covering pricing math, position aggregation,
  closed-form metrics, valuation, scenario grids, storage round-trip,
  RV estimators, percentile machinery, ATM/Δ interpolation, parity,
  synthetics, higher-order Greeks.
- **Docs**: MkDocs Material site with quick-start, CLI reference, MCP
  tool catalogue, Greeks conventions, and the Vol Dashboard concept page.

[0.1.0]: https://github.com/pradhann/options-chain-mcp/releases/tag/v0.1.0
