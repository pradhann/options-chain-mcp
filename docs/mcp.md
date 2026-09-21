# MCP tools

`optionslab` ships a Model-Context-Protocol server so Claude clients can call its analytics directly. Tool names mirror the CLI verbs one-to-one.

## Register the server

=== "Claude Desktop"

    Settings → Developer → Edit Config → add to `mcpServers`:

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

    Save, then **Cmd+Q** and reopen.

=== "Claude Code (terminal)"

    ```bash
    claude mcp add optionslab -s user -- python -m optionslab.mcp_server
    claude mcp list                    # should show optionslab ✓ Connected
    ```

=== "Project-scoped `.mcp.json`"

    Commit alongside the code:

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

## Tool catalogue

### Sourced data feeds (see [Data feeds](feeds.md))
Every tool below returns `{status, provenance, data, warnings, not_verified}`.

- Pre-trade and jobs: `pretrade_page`, `refresh_all`, `weekly_check`.
- Chains: `chain_report`, `skew_history`, `atm_iv_history`, `oi_change`.
- Underlying: `get_spot_price`, `realized_vol` (5x3 matrix), `corporate_actions`,
  `dividend_policy`, `analyst_targets` (staleness only), `next_earnings`.
- Futures and ETFs: `futures_curve`, `prompt_spread`, `crack_spreads`,
  `etf_holdings`, `etf_roll_rule`.
- Physical and positioning: `eia_weekly`, `eia_summary`, `eia_steo`, `cot_positioning`.
- SEC: `sec_sweep`, `sec_filings`, `sec_insiders`, `sec_manual_entry`.
- Calendar and markets: `calendar_events`, `polymarket_reads`, `polymarket_search`.
- Book: `book_marks`, `delta_notional_after`, `ledger_open`, `ledger_close`, `ledger_review`.

### Market data (legacy chain and vol tools)
`list_expirations_tool`, `chain`, `iv_term_structure`, `vix_term_structure`,
`vix_strip`, `recent_news`.

### Position analysis
`payoff`, `value`, `greeks`, `metrics`, `scenario`, `theoretical_price`.

### Week 1 — Architecture
`parity_check`, `verify_synthetic_stock`.

### Week 3 — Volatility
`vrp_today`, `vrp_history`, `skew_metrics`, `event_implied_move`, `vol_dashboard`.

### Positions storage
`positions_list`, `positions_get`, `positions_save`, `positions_delete`.

### Charts (return inline image + save a copy)
`chart_payoff`, `chart_primitives`, `chart_verticals`,
`chart_greek`, `chart_greek_time`,
`chart_extrinsic`, `chart_scenario`,
`chart_smile`, `chart_rv`, `chart_vrp`, `chart_term`,
`chart_skew_curve`, `chart_vix_strip`, `chart_dashboard`.

## How positions flow through MCP

Most position-taking tools accept *either* a `position` dict (shaped like `Position.to_dict()`) **or** a bare list of leg dicts, **or** a `position_name` referring to a saved entry.

```text
position = { "legs": [
  {"side": "long",  "option_type": "call", "strike": 100, "premium": 6.0},
  {"side": "short", "option_type": "call", "strike": 110, "premium": 2.5}
]}
```

## Example agent prompts

Once `optionslab` is registered, ask Claude things like:

> *"Pull the SPY chain at the nearest monthly. What's the 25-delta skew?"*
>
> *"Build a bull call spread on AAPL 100/110 paying $3.50, then show me what it pays at expiry under a 10% rally."*
>
> *"What's today's VRP, term structure regime, and 25Δ RR for SPY? Print the dashboard."*
>
> *"Run a put-call parity check on SPY's 580 strike for 2026-07-17 and tell me if the gap is within the bid-ask."*

Claude will pick the right tool, structure the inputs, and return the result (charts come back as inline images).
