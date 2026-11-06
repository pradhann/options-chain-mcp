"""Public API surface: the documented imports must keep working."""

from __future__ import annotations


def test_top_level_imports():
    """Every name in the README quick-start must import cleanly."""
    from optionslab import (
        Leg, Position, MarketContext,
        PayoffResult, ValueResult, GreeksResult,
        MetricsResult, ScenarioResult,
        bs_price, bs_greeks, implied_vol, year_fraction,
        OptionsLabError,
    )
    # Touch the symbols so the imports aren't optimized out.
    for sym in (Leg, Position, MarketContext, PayoffResult, ValueResult,
                GreeksResult, MetricsResult, ScenarioResult, bs_price,
                bs_greeks, implied_vol, year_fraction, OptionsLabError):
        assert sym is not None


def test_analysis_layer_imports():
    from optionslab.analysis import (
        expiration_payoff, position_metrics, value, greeks,
        scenario_grid, theoretical_price,
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
