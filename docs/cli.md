# CLI reference

`python -m optionslab <verb> [options]` (or just `optionslab` once installed).

Every position-taking verb accepts the same position spec — `--position NAME` (saved file) **or** `--legs '[{...}]'` (inline JSON).

## Position analysis

| Verb | What it returns |
|---|---|
| `chain --ticker SYM` | option chain + per-strike Greeks (Δ Γ Θ V ρ) |
| `payoff --legs '...' --at 115` | expiration P&L at one or more S_T |
| `value --legs '...' --spot 105 --sigma 0.30` | mark-to-model value + PnL vs entry |
| `greeks --legs '...' --spot 105 --sigma 0.30` | portfolio Greeks, raw + dollar versions |
| `metrics --legs '...'` | max profit, max loss, breakeven(s), net debit/credit |
| `scenario --legs '...' --spot 100 --sigma 0.30 --plot` | dollar P&L matrix (spot × time) |

## Positions storage

| Verb | What it does |
|---|---|
| `positions save NAME --legs '[...]'` | save a named position to `.optionslab/positions.json` |
| `positions list` | list saved positions |
| `positions show NAME` | print one saved position |
| `positions delete NAME` | remove a saved position |
| `positions path` | print the positions-file path |

## Week 1 — Architecture

| Verb | What it gives you |
|---|---|
| `parity --ticker SYM --strike 580 --expiration 2026-07-17` | put-call parity check: LHS, RHS, gap, bid-ask context |
| `synthetic --kind long-stock --strike 100 --call-premium 6 --put-premium 5.5` | synthetic-stock verifier; reports parallel offset = parity-priced cost |

## Week 2 — Greeks

`chain` already returns per-strike Greeks. For the teaching plots:

```bash
# Delta sigmoid + py_vollib oracle dots
optionslab chart --kind greek --strike 100 --type call --sigma 0.30 --days 90 --verify

# Greek vs time: gamma peaks at expiry, theta asymptotes, vega scales √T
optionslab chart --kind greek-time --greek gamma --strike 100 --spot 100 --type call --sigma 0.30
```

The `greeks` verb returns vanna, vomma, and charm alongside the five canonical ones — all carry their conventions in the response.

## Week 3 — Volatility

| Verb | What it gives you |
|---|---|
| `rv --ticker SPY --all` | all five RV estimators side-by-side |
| `vrp` | today's VRP + 2-year percentile + interpretation |
| `vrp --history --years 15` | summary stats over a long window |
| `term-structure` | VIX-family curve + ratio + regime label |
| `skew --ticker SPY --exp-index 4 --save-history` | 25Δ RR + 25Δ BF + ATM IV |
| `vix-strip --ticker SPX` | model-free VIX replication + wing-boost sensitivity |
| `event-vol --ticker NVDA --front-exp ... --back-exp ...` | event-implied 1-day move |
| `dashboard` | the daily Vol Dashboard — all five fields in one read |

## Data fetchers

| Verb | What it gives you |
|---|---|
| `data realized-vol --ticker SPY` | single RV reading |
| `data iv-term --ticker SPY` | ATM IV by expiration |
| `data earnings --ticker NVDA` | next earnings + EPS/rev estimates |
| `data news --ticker OXY` | recent headlines |
| `data targets --ticker SPY` | analyst price-target consensus |

## Charts (unified dispatcher)

`chart --kind <name> [options]`. Add `--save-plot path.png` to write a PNG; `--plot` to open in a window.

| Kind | What it draws |
|---|---|
| `payoff` | expiration P&L curve for any position |
| `primitives` | 2×2 long/short × call/put |
| `verticals` | 2×2 bull/bear × call/put verticals |
| `greek` | a Greek vs spot (sigmoid for delta) |
| `greek-time` | a Greek vs days-to-expiry |
| `chain` | extrinsic-by-strike (the "tent") |
| `scenario` | P&L heatmap (spot × time) |
| `smile` | 3-panel IV smile |
| `rv` | 5-estimator RV history |
| `vrp` | VRP time series + histogram |
| `term` | VIX-family levels + ratio history |
| `skew-curve` | IV(K) snapshot with 25Δ markers |
| `vix-strip` | replicated vs published VIX + wing-boost |
| `dashboard` | compact tile panel of all 5 dashboard fields |

## Interactive shell

```bash
optionslab interactive
```

Pick a ticker, pick an expiration by number or date, view the chain. Light browsing — for analysis use the verbs above.
