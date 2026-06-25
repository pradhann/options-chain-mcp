# Quick start

Three flavors of the same package — pick the one matching your workflow.

## Install

```bash
pip install optionslab
```

Python **3.12+** is required. The install brings in `numpy`, `scipy`, `pandas`, `matplotlib`, `yfinance`, and `mcp`. For development with the test suite, add the `[test]` extra:

```bash
pip install "optionslab[test]"
```

## Library

A `Position` is a list of `Leg`s. Every analysis function takes a `Position` (and, for valuation, a `MarketContext`) and returns a typed result with `.to_dict()` for JSON edge.

```python
from optionslab import Position, MarketContext
from optionslab.analysis import (
    expiration_payoff, position_metrics, value, greeks, scenario_grid,
)

pos = Position.from_dicts([
    {"side": "long",  "option_type": "call",
     "strike": 100, "premium": 6.0, "expiration": "2026-09-18"},
    {"side": "short", "option_type": "call",
     "strike": 110, "premium": 2.5, "expiration": "2026-09-18"},
], name="bull-call-100-110")

expiration_payoff(pos, 115).total_dollars      # 650.0
position_metrics(pos).max_profit               # 650.0
position_metrics(pos).breakevens               # [103.5]

market = MarketContext.explicit(spot=105, r=0.045)
value(pos, market, ivs=0.30).total_value_dollars
greeks(pos, market, ivs=0.30).delta_dollars
scenario_grid(pos, market, ivs=0.30).pnl_dollars
```

### Save a Position by name

```python
from optionslab.storage import save_position, get_position
save_position(pos)
get_position("bull-call-100-110")    # reloaded
```

Saved positions live in `.optionslab/positions.json` under your project root (the same convention `.git` uses).

## CLI

Every position-taking verb accepts the **same spec**:

```
--position NAME      # saved in .optionslab/positions.json
--legs '[{...}]'     # inline JSON list of legs
```

A few representative invocations:

```bash
# Options chain with Greeks per strike
optionslab chain --ticker SPY --near-money 6

# Closed-form metrics for any position
optionslab metrics --position bull-call-100-110

# P&L grid: rows = spot moves, cols = days forward
optionslab scenario --position bull-call-100-110 --spot 100 --sigma 0.30 --plot

# Vol Dashboard — the daily ritual
optionslab dashboard --save-plot dashboard.png
```

See the [CLI reference](cli.md) for every verb and flag, or [USAGE.md](https://github.com/pradhann/options-chain-mcp/blob/main/USAGE.md) for a single-page printable.

## MCP server

`optionslab` ships a stable MCP entrypoint:

```bash
python -m optionslab.mcp_server     # stdio transport, run by Claude
```

Add it to your Claude Desktop config (Settings → Developer → Edit Config):

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

Or, for terminal Claude Code:

```bash
claude mcp add optionslab -s user -- python -m optionslab.mcp_server
```

Restart Claude and ask things like:

> *"Pull the SPY chain at the nearest monthly and chart the 25Δ skew."*
>
> *"Build a bull call spread on AAPL 100/110 and show me the P&L grid for ±20% over 30 days."*
>
> *"What's today's VRP and term-structure regime?"*

Tool names mirror the CLI verbs — see [MCP tools](mcp.md).
