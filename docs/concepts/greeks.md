# Greeks conventions

`optionslab` is opinionated about Greeks units so the numbers in any tool's response are never ambiguous. The `conventions` field rides along in every Greeks dict.

## Single-leg Greeks (from `bs_greeks`)

| Greek | Unit | Meaning |
|---|---|---|
| `delta` | per +$1 spot | option price change per +$1 in S |
| `gamma` | per +$1 spot | change in delta per +$1 in S |
| `theta` | per +1 calendar day | time decay; ≤ 0 for longs (typically) |
| `vega`  | per +1 IV point (0.01 σ) | sensitivity to a 1-point IV move |
| `rho`   | per +1 pct-point in r (0.01) | sensitivity to a 1pp risk-free shift |
| `vanna` | Δ-change per +1 IV point | cross-sensitivity ∂Δ/∂σ |
| `vomma` | Vega-change per +1 IV point | ∂Vega/∂σ; vol-of-vol exposure |
| `charm` | Δ-change per +1 calendar day | ∂Δ/∂t; relevant for overnight delta drift |

`bs_greeks` is vectorized — pass arrays for S/K/T/σ and the dict's values are arrays.

## Portfolio Greeks

`greeks(position, market, ivs=σ)` returns:

```text
delta, gamma, theta, vega, rho                  # qty-weighted, side-signed sums
delta_dollars, gamma_dollars, ...               # × 100 (per-contract multiplier)
per_leg                                          # each leg's Greeks + contributions
```

Aggregation rule:

```
X_portfolio = Σ_i  sign_i · qty_i · X_leg_i
```

where `sign = +1 long, −1 short`. The `*_dollars` versions multiply by the contract size (100 shares per contract).

So `delta_dollars = 25` means *"+$25 P&L per +$1 move in spot."*

## Verified math

Every analytic Greek matches the `py_vollib` reference to **1e-6** (run `pytest tests/test_pricing.py`). Higher-order Greeks (vanna, vomma, charm) are additionally verified against centered finite-difference bumps in `tests/test_week1_week2.py`.
