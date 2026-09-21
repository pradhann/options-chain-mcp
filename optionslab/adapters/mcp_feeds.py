"""MCP tools for the sourced data feeds (optionslab.feeds).

Every tool returns the feed's provenance envelope unchanged:
    {"status", "provenance", "data", "warnings", "not_verified"}
A `not_verified` line is the tool declining to supply a number it could
not source; it carries the reason and a URL to check by hand.

Registered onto the shared FastMCP instance by `register(mcp)`.
"""

from __future__ import annotations

from dataclasses import asdict

from mcp.server.fastmcp import FastMCP, Image

from ..feeds import (
    bars,
    book,
    calendar,
    chain_history,
    chains,
    cot,
    edgar,
    eia,
    etf_roll,
    fundamentals,
    futures,
    jobs,
    polymarket,
    pretrade,
)
from ..storage import ledger
from .charts import render


def _image(name: str, args: list[str], order: dict | None = None) -> list:
    path, png = render(name, args, order=order)
    return [Image(data=png, format="png"), f"Saved to {path}"]


def register(mcp: FastMCP) -> None:
    # ---------- the pre-trade page and scheduled jobs ----------

    @mcp.tool()
    def pretrade_page(ticker: str, expiration: str, order: dict | None = None) -> dict:
        """Every sourced pre-trade input for one name and expiry, in one call.

        Sections: spot (feed vs CBOE vs parity), chain usability and walls,
        RV matrix, crack spreads, EIA physical series, COT, SEC sweep, dated
        calendar to expiry, Polymarket reads, and book delta-notional after
        `order` ({symbol, instrument: stock|call|put, side, qty, strike?,
        expiration?}). `data.unsourced` lists every line the tool could not source.
        """
        return pretrade.pretrade(ticker, expiration, order)

    @mcp.tool()
    def refresh_all(tickers: list[str] | None = None) -> dict:
        """Snapshot every feed for the watchlist and open positions (or `tickers`)."""
        return jobs.refresh_all(tickers)

    @mcp.tool()
    def weekly_check() -> dict:
        """Sunday read: prompt spreads, EIA series, COT staleness, Form 4, calendar, alerts."""
        return jobs.weekly_check()

    # ---------- charts (every chart carries a source / as-of footer) ----------

    @mcp.tool()
    def chart_pretrade_sheet(ticker: str, expiration: str, order: dict | None = None) -> list:
        """One-page sheet: red flags, order P&L vs distribution, OI walls, RV matrix,
        cracks, COT."""
        return _image("sheet", [ticker, expiration], order)

    @mcp.tool()
    def chart_chain_quality(ticker: str, expiration: str) -> list:
        """IV smile with bid/ask bands over mirrored OI; feed vs parity spot marked."""
        return _image("chain", [ticker, expiration])

    @mcp.tool()
    def chart_rv_matrix(ticker: str, expiration: str | None = None) -> list:
        """RV heatmap, worst cells outlined; with `expiration`, cells read against ATM IV."""
        return _image("rv", [ticker, expiration] if expiration else [ticker])

    @mcp.tool()
    def chart_futures_curve(root: str) -> list:
        """Futures curve now vs 5 and 21 sessions earlier (same contracts)."""
        return _image("curve", [root])

    @mcp.tool()
    def chart_cracks() -> list:
        """Crack spreads over the recorded sessions."""
        return _image("cracks", [])

    @mcp.tool()
    def chart_cot(root: str) -> list:
        """Managed-money net % of OI with the trailing-year range."""
        return _image("cot", [root])

    @mcp.tool()
    def chart_order(order: dict) -> list:
        """A proposed option's P&L at expiry over the risk-neutral distribution."""
        return _image("order", [], order)

    # ---------- item 1-3: chains, chain history, OHLC ----------

    @mcp.tool()
    def chain_report(ticker: str, expiration: str, include_rows: bool = False) -> dict:
        """Chain quality for one expiry: spot check (FEED_SUSPECT), usability, walls.

        Rows (optional) carry mid from two-sided quotes only, IV from mid with
        bid/ask bands, spread %, last-outside-bid/ask, and volume/OI.
        """
        return chains.chain_report(ticker, expiration, include_rows=include_rows)

    @mcp.tool()
    def skew_history(ticker: str, expiration: str) -> dict:
        """25-delta risk reversal and butterfly per recorded snapshot date."""
        return chain_history.skew_history(ticker, expiration)

    @mcp.tool()
    def atm_iv_history(ticker: str, expiration: str) -> dict:
        """ATM mid-IV per recorded snapshot date for one expiry."""
        return chain_history.atm_iv_history(ticker, expiration)

    @mcp.tool()
    def oi_change(ticker: str, expiration: str, top: int = 10) -> dict:
        """Largest per-strike OI changes, day-over-day and week-over-week."""
        return chain_history.oi_change(ticker, expiration, top)

    @mcp.tool()
    def corporate_actions(ticker: str) -> dict:
        """Dividends and splits by ex-date over three years."""
        return bars.corporate_actions(ticker)

    @mcp.tool()
    def dividend_policy(ticker: str) -> dict:
        """Next declared ex-date, last four amounts, policy vs trailing yield."""
        return fundamentals.dividend_policy(ticker)

    # ---------- item 4-5: futures and ETF roll ----------

    @mcp.tool()
    def futures_curve(root: str, months: int = 24) -> dict:
        """Month-by-month curve (CL, BZ, HO, RB, NG) with expiries and shape."""
        return futures.curve(root, months)

    @mcp.tool()
    def prompt_spread(root: str) -> dict:
        """M1-M2 with health against your thresholds and 5/21-session change."""
        return futures.prompt_spread(root)

    @mcp.tool()
    def crack_spreads() -> dict:
        """ULSD, RBOB, 3-2-1 cracks and Brent-WTI with 5/21-session changes."""
        return futures.cracks()

    @mcp.tool()
    def etf_holdings(fund: str) -> dict:
        """USCF fund holdings (USO, BNO, UGA, UNG): contract months and weights."""
        return etf_roll.fund_holdings(fund)

    @mcp.tool()
    def etf_roll_rule(fund: str) -> dict:
        """The fund's roll rule with prospectus evidence, window, and roll yield."""
        return etf_roll.roll_rule(fund)

    # ---------- item 6-7: physical and positioning ----------

    @mcp.tool()
    def eia_weekly(series: str, region: str = "US") -> dict:
        """One WPSR series vs its five-year band, with percentile and lowest-since."""
        return eia.weekly_series(series, region)

    @mcp.tool()
    def eia_summary() -> dict:
        """The full WPSR table (stocks by PADD, utilization, trade, days of supply)."""
        return eia.wpsr_summary()

    @mcp.tool()
    def eia_steo() -> dict:
        """STEO forward balances and price forecasts."""
        return eia.steo()

    @mcp.tool()
    def cot_positioning(root: str | None = None) -> dict:
        """Managed-money positioning (CFTC combined; ICE for BZ), with print dates."""
        return cot.managed_money(root) if root else cot.positioning_summary()

    # ---------- item 8: SEC ----------

    @mcp.tool()
    def sec_sweep(ticker: str) -> dict:
        """Capital-structure sweep: converts, warrants, ATM, hedges, dividends, insiders."""
        return edgar.structural_sweep(ticker)

    @mcp.tool()
    def sec_filings(ticker: str) -> dict:
        """Recent 10-K/10-Q, 8-K by item, and 424B supplements with URLs."""
        return edgar.recent_filings(ticker)

    @mcp.tool()
    def sec_insiders(ticker: str, days: int = 90) -> dict:
        """Form 4 transactions with codes, shares, price, and dates."""
        return edgar.insider_trades(ticker, days)

    @mcp.tool()
    def sec_manual_entry(ticker: str, item: str, finding: str,
                         document_ref: str | None = None, url: str | None = None) -> dict:
        """Record a sweep finding read by hand (e.g. SEDAR+); verified only with a document_ref."""
        return edgar.add_manual_entry(ticker, item, finding, document_ref, url)

    # ---------- item 9-11: calendar, prediction markets, targets ----------

    @mcp.tool()
    def calendar_events(start: str, end: str, tickers: list[str] | None = None) -> dict:
        """Every sourced event in [start, end]: macro releases, earnings, ex-dividend."""
        return calendar.events_between(start, end, tickers=tickers or ())

    @mcp.tool()
    def polymarket_reads() -> dict:
        """Configured prediction markets: probability, changes, resolution text."""
        return polymarket.watchlist_markets()

    @mcp.tool()
    def polymarket_search(query: str) -> dict:
        """Find prediction markets to add to the watchlist."""
        return polymarket.search(query)

    # ---------- item 13: book and ledger ----------

    @mcp.tool()
    def book_marks() -> dict:
        """Open positions marked from chain snapshots, with delta-notional per name."""
        return book.mark_positions()

    @mcp.tool()
    def delta_notional_after(order: dict) -> dict:
        """Per-name delta-notional before and after a proposed order vs the cap."""
        return book.delta_notional_after(order)

    @mcp.tool()
    def ledger_open(symbol: str, instrument: str, side: str, thesis_id: str,
                    exit_condition: str, recommended_qty: float, executed_qty: float,
                    entry_price: float, strike: float | None = None,
                    expiration: str | None = None, entry_date: str | None = None,
                    notes: str | None = None) -> dict:
        """Record an opened position. thesis_id and exit_condition are required."""
        row = ledger.open_position(
            symbol=symbol.upper(), instrument=instrument, side=side, thesis_id=thesis_id,
            exit_condition=exit_condition, recommended_qty=recommended_qty,
            executed_qty=executed_qty, entry_price=entry_price, strike=strike,
            expiration=expiration, notes=notes,
            **({"entry_date": entry_date} if entry_date else {}))
        return asdict(row)

    @mcp.tool()
    def ledger_close(position_id: str, exit_price: float, outcome: str,
                     right_for_reason: str, autopsy: str, exit_date: str | None = None) -> dict:
        """Close a position; outcome, right_for_reason, and autopsy are all required."""
        row = ledger.close_position(position_id, exit_price=exit_price, outcome=outcome,
                                     right_for_reason=right_for_reason, autopsy=autopsy,
                                     exit_date=exit_date)
        return asdict(row)

    @mcp.tool()
    def ledger_review() -> dict:
        """Closed positions without a review, and executed-above-recommended sizes."""
        return book.ledger_review()
