# optionslab

**Options analytics for traders and learners — a clean Python library, a friendly CLI, and a Model-Context-Protocol server, all in one package.**

[![PyPI](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/release/python-3120/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/pradhann/options-chain-mcp/blob/main/LICENSE)
[![MCP](https://img.shields.io/badge/MCP-compatible-7c3aed.svg)](https://modelcontextprotocol.io)

---

## What's here

`optionslab` is one Python package with three front doors:

<div class="grid cards" markdown>

- :material-language-python: __Library__

    ---

    Typed dataclasses, vectorized math, every analysis function takes a
    `Position` and a `MarketContext`.

    [:octicons-arrow-right-24: Quick start](quickstart.md)

- :material-console: __CLI__

    ---

    One verb per task. `chain`, `payoff`, `value`, `greeks`, `metrics`,
    `scenario`, `vrp`, `dashboard`, `parity`, `synthetic`, and more.

    [:octicons-arrow-right-24: CLI reference](cli.md)

- :material-robot-outline: __MCP server__

    ---

    40+ tools any Claude client can call. Charts come back as inline
    images. Mirrors the CLI vocabulary one-to-one.

    [:octicons-arrow-right-24: MCP tools](mcp.md)

</div>

## What it covers

- **The math**: Black-Scholes price + all five first-order Greeks (Δ Γ Θ V ρ) plus three higher-order (vanna, vomma, charm), an IV solver, put-call parity checker.
- **Positions**: arbitrary multi-leg, payoff at expiration, mid-flight value, portfolio Greeks, closed-form max P / max L / breakevens, scenario grids over spot × time.
- **Volatility**: realized-vol estimator zoo (close-to-close, Parkinson, Garman-Klass, Rogers-Satchell, Yang-Zhang), VRP, VIX term structure + regime label, 25Δ Risk Reversal / Butterfly, model-free VIX strip, event-implied move, daily Vol Dashboard.
- **Charts**: payoff diagrams, Greek curves (vs spot AND vs time), IV smile, VRP history, VIX term, skew, dashboard panel.

## Install

```bash
pip install optionslab
```

## Quick taste

=== "Library"

    ```python
    from optionslab import Position, MarketContext
    from optionslab.analysis import expiration_payoff, position_metrics, greeks

    pos = Position.from_dicts([
        {"side": "long",  "option_type": "call", "strike": 100, "premium": 6.0},
        {"side": "short", "option_type": "call", "strike": 110, "premium": 2.5},
    ], name="bull-call-100-110")

    expiration_payoff(pos, s_t=115).total_dollars   # 650.0
    position_metrics(pos).breakevens                 # [103.5]

    market = MarketContext.explicit(spot=105, r=0.045)
    greeks(pos, market, ivs=0.30).delta_dollars      # ≈ $25 per +$1 spot
    ```

=== "CLI"

    ```bash
    optionslab chain --ticker SPY --near-money 6
    optionslab metrics --legs '[{"side":"long","option_type":"call","strike":100,"premium":6},
                                {"side":"short","option_type":"call","strike":110,"premium":2.5}]'
    optionslab dashboard --save-plot dashboard.png
    ```

=== "MCP"

    ```json
    {
      "mcpServers": {
        "optionslab": {
          "command": "python",
          "args": ["-m", "optionslab.mcp_server"]
        }
      }
    }
    ```

## Verified math

50 pytest cases pin every number. Every analytic Greek matches `py_vollib` to **1e-6**.
Hand-checked: bull call spread (max P $650 / max L −$350 / BE $103.50), put-call parity, synthetic-stock construction, finite-difference vanna/vomma/charm.

```text
============================= test session starts ==============================
collected 50 items
..................................................                       [100%]
============================== 50 passed in 1.23s ==============================
```
