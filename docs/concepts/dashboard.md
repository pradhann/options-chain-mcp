# The Vol Dashboard

Five computed fields read every morning. The daily ritual.

## What's in it

| Field | What it is | Source |
|---|---|---|
| **30d ATM IV** | VIX as proxy for SPX 30-day ATM | live `^VIX` |
| **VRP** | VIX − trailing 21-day SPX RV (vol points) | `^VIX` − rolling RV of `^GSPC` |
| **VIX3M / VIX** | term-structure ratio | `^VIX3M / ^VIX` |
| **25Δ Risk Reversal** | IV(25Δ call) − IV(25Δ put) | live chain |
| **25Δ Butterfly**     | ½(IV_25Δc + IV_25Δp) − ATM IV | live chain |

Each field comes with:

- its raw value,
- its 2-year empirical percentile,
- a one-line interpretation.

Plus two **human passthroughs**:

- `--yesterdays-call` — your self-grade on the prior day's vol view
- `--vol-view` — your 150-word read of the current regime + trade idea

## Percentile machinery

VIX-derived fields have real 2-year percentiles from day 1 because yfinance returns history.

Skew percentiles can't be computed historically — yfinance doesn't surface past option chains. So the first time you run

```bash
optionslab dashboard
optionslab skew --ticker SPY --exp-index 4 --save-history
```

each value is appended to a CSV under `.optionslab/history/`. The percentile builds organically over time; the dashboard reports `n=…` and flags `thin history` when the sample is small.

```
.optionslab/history/
  skew_SPY_<exp>_RR.csv     timestamp,value
  skew_SPY_<exp>_BF.csv     timestamp,value
  ...
```

## The daily ritual

```bash
optionslab dashboard \
    --vol-view "Strong contango + thin VRP — sell selectively, wide wings." \
    --yesterdays-call "Right on direction; sized too small." \
    --save-plot ~/Desktop/vol.png
```

Run it every morning; eventually you feel the regime without thinking.
