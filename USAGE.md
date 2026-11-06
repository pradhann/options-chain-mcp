# optionslab — what it can do

One toolkit, two front doors. CLI verbs and MCP tools share the same vocabulary; everything below works in both, with the same flags.

```
python -m optionslab <verb> ...      # CLI
mcp__optionslab__<tool>              # MCP (after Claude restart)
```

---

## Position spec (used by every position-taking verb)

```
--position NAME       # saved in .optionslab/positions.json
--legs '[{...}]'      # inline JSON list of legs
```

Each leg: `{side: long|short, option_type: call|put, strike, premium, qty?, expiration?}`.

---

## What you can do

### 1. Pull live market data

| Verb | What it gives you |
|---|---|
| `chain --ticker SPY` | option chain + per-strike Greeks (Δ Γ Θ V ρ) |
| `data realized-vol --ticker SPY` | quick realized-vol number |
| `data iv-term --ticker SPY` | ATM IV by expiration |
| `data earnings --ticker NVDA` | next earnings + EPS/rev estimates |
| `data news --ticker OXY` | recent headlines |
| `data targets --ticker SPY` | analyst price-target consensus |

### 2. Position analysis (build once, query many ways)

| Verb | Answers |
|---|---|
| `payoff --legs '[...]' --at 115` | expiration P&L at S_T |
| `value --legs '[...]' --spot 105 --sigma 0.30` | mark-to-model value + PnL vs entry |
| `greeks --legs '[...]' --spot 105 --sigma 0.30` | full portfolio Greek vector (raw + dollar) |
| `metrics --legs '[...]'` | max profit, max loss, breakeven(s), net debit/credit |
| `scenario --legs '[...]' --spot 100 --sigma 0.30 --plot` | dollar P&L matrix: spot moves × days forward |

### 3. Save and reuse positions

```bash
optionslab positions save bull-100-110 \
    --legs '[{"side":"long","option_type":"call","strike":100,"premium":6},
             {"side":"short","option_type":"call","strike":110,"premium":2.5}]' \
    --symbol AAPL

optionslab positions list
optionslab positions show bull-100-110
optionslab metrics --position bull-100-110    # reference by name everywhere
```

### 4. Week 1 — Architecture (no-arb identities)

| Verb | What it checks |
|---|---|
| `parity --ticker SPY --strike 580 --expiration 2026-07-17` | put-call parity: C − P vs S − K·e^(−rT), with bid-ask context |
| `synthetic --kind long-stock --strike 100 --call-premium 6 --put-premium 5.5` | synthetic stock verifier; reports parallel offset = the parity-priced cost |
| `chart --kind primitives --strike 100 --premium 5` | classic 2×2 long/short × call/put grid |
| `chart --kind verticals --k1 100 --k2 110 --c1 6 --c2 2.5 --p1 1.5 --p2 4` | bull/bear × call/put 2×2 |
| `chart --kind chain --ticker SPY` | extrinsic-by-strike (the "tent") |

### 5. Week 2 — Greeks (sensitivities)

| Verb | What it gives you |
|---|---|
| `chart --kind greek --strike 100 --type call --sigma 0.30 --days 90 --verify` | delta sigmoid (or gamma/theta/vega/rho) vs spot, with py_vollib oracle dots |
| `chart --kind greek-time --strike 100 --spot 100 --type call --sigma 0.30 --greek gamma` | gamma vs days-to-expiry (peak near 0) |
| `chart --kind greek-time --greek theta` | theta asymptote near expiry |
| `chart --kind greek-time --greek vega`  | √T scaling of vega |

Every `greeks` / `chain` / `theoretical_price` call returns **delta, gamma, theta, vega, rho, vanna, vomma, charm** with conventions stated in the response (delta per $1 spot, theta per calendar day, vega per IV point, etc.).

### 6. Week 3 — Volatility I (vol as a market)

| Verb | What it gives you |
|---|---|
| `rv --ticker SPY --all` | all five RV estimators side-by-side (c2c, Parkinson, GK, RS, YZ) |
| `vrp` | today's VRP (VIX − 21d RV) + 2-year percentile + interpretation |
| `vrp --history --years 15` | summary stats over a long window |
| `term-structure` | VIX-family curve + ratio + regime label + action |
| `skew --ticker SPY --exp-index 4 --save-history` | 25Δ RR + 25Δ BF + ATM IV |
| `vix-strip --ticker SPX` | model-free VIX replication + wing-boost sensitivity |
| `event-vol --ticker NVDA --front-exp ... --back-exp ...` | event-implied 1-day move |
| `dashboard` | the daily Vol Dashboard — all five fields in one read |

### 7. Charts (unified dispatcher)

`chart --kind <name>` returns a PNG (inline + saved when over MCP):

```
payoff       expiration P&L curve for any position
primitives   2×2 long/short × call/put (curriculum classic)
verticals    2×2 bull/bear × call/put verticals
greek        a Greek vs spot (sigmoid for delta)
greek-time   a Greek vs days-to-expiry
chain        extrinsic-by-strike
scenario     P&L heatmap: spot moves × days forward
smile        3-panel IV smile (vs K, vs log-money, vs Δ)
rv           5-estimator RV history
vrp          VRP time series + histogram with marquee dates
term         VIX-family levels + ratio history
skew-curve   IV(K) snapshot with 25Δ markers
vix-strip    replicated vs published VIX + wing-boost bar
dashboard    compact tile-panel of all 5 dashboard fields
```

---

## Common workflows (one-liners)

```bash
# "Is this position rich or cheap right now?"
optionslab value   --position my-spread --spot $(optionslab data realized-vol --ticker SPY | jq .ticker)
optionslab greeks  --position my-spread --spot 105 --sigma 0.30

# "What does this position look like if SPY moves 10% in 30 days?"
optionslab scenario --position my-spread --spot 100 --sigma 0.30 --plot

# "Are SPY options structurally rich today?"
optionslab dashboard

# "Is the earnings move priced in correctly?"
optionslab event-vol --ticker NVDA --front-exp 2026-08-22 --back-exp 2026-09-19

# "Walk me through a put-call parity check on a live chain"
optionslab parity --ticker SPY --strike 580 --expiration 2026-07-17

# "Show me the textbook delta sigmoid for an option I'm pricing"
optionslab chart --kind greek --strike 100 --type call --sigma 0.30 --days 90 \
    --verify --save-plot delta.png
```

---

## The daily vol-dashboard ritual

```bash
optionslab dashboard \
    --vol-view "Strong contango + thin VRP — sell selectively, wide wings." \
    --yesterdays-call "Right on direction; sized too small." \
    --save-plot ~/Desktop/vol.png
```

Five computed fields (30d ATM IV, VRP, VIX3M/VIX, 25Δ RR, 25Δ BF) each with 2-year percentile + interpretation. VIX-derived fields work from day 1; skew percentiles accumulate in `.optionslab/history/*.csv` as you run it daily.

---

## All CLI verbs at a glance

```
chain payoff value greeks metrics scenario chart
positions data interactive
rv vrp term-structure skew vix-strip event-vol dashboard
parity synthetic
```

17 verbs, 42 MCP tools, 14 chart kinds. `python -m optionslab <verb> --help` for every flag.
