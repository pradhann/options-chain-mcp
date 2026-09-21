"""Public API surface: the documented imports must keep working."""

from __future__ import annotations


def test_top_level_imports():
    """Every name in the README quick-start must import cleanly."""
    from optionslab import (
        GreeksResult,
        Leg,
        MarketContext,
        MetricsResult,
        OptionsLabError,
        PayoffResult,
        Position,
        ScenarioResult,
        ValueResult,
        bs_greeks,
        bs_price,
        implied_vol,
        year_fraction,
    )
    # Touch the symbols so the imports aren't optimized out.
    for sym in (Leg, Position, MarketContext, PayoffResult, ValueResult,
                GreeksResult, MetricsResult, ScenarioResult, bs_price,
                bs_greeks, implied_vol, year_fraction, OptionsLabError):
        assert sym is not None


def test_analysis_layer_imports():
    from optionslab.analysis import (
        expiration_payoff,
        greeks,
        position_metrics,
        scenario_grid,
        theoretical_price,
        value,
    )
    for sym in (expiration_payoff, position_metrics, value, greeks,
                scenario_grid, theoretical_price):
        assert callable(sym)


def test_mcp_server_registers_tools():
    """The MCP entrypoint module must register at least 20 tools."""
    import asyncio

    from optionslab.adapters import mcp as m
    tools = asyncio.run(m.mcp.list_tools())
    assert len(tools) >= 20
    names = {t.name for t in tools}
    for required in ("chain", "payoff", "value", "greeks", "metrics",
                     "scenario", "chart_payoff", "positions_list"):
        assert required in names, f"missing MCP tool: {required}"


def test_feed_tools_registered_and_optional_params_not_required():
    """Every feed tool is exposed; no parameter with a default is marked required."""
    import asyncio

    from optionslab.adapters import mcp as m
    tools = {t.name: t for t in asyncio.run(m.mcp.list_tools())}
    for name in ("pretrade_page", "chain_report", "realized_vol", "futures_curve",
                 "crack_spreads", "eia_weekly", "cot_positioning", "sec_sweep",
                 "calendar_events", "polymarket_reads", "book_marks", "ledger_open",
                 "refresh_all", "weekly_check"):
        assert name in tools, f"missing MCP tool: {name}"
    for tool in tools.values():
        schema = tool.inputSchema
        for param in schema.get("required", []):
            assert "default" not in schema["properties"][param], (tool.name, param)
