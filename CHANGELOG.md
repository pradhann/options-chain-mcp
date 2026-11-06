# Changelog

All notable changes to `optionslab` will be documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [SemVer](https://semver.org/spec/v2.0.0.html).

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
