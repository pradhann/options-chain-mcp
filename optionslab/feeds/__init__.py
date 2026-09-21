"""Sourced data feeds. Every public function returns a provenance envelope.

  envelope        the result contract: envelope(), provenance(), unverified()
  http            HTTP with dated raw snapshots; offline replay
  recorded        the same record/replay for yfinance reads
  config          your lists and thresholds (.optionslab/config.json)

  chains          option chains: two-sided mids, IV bands, parity spot, walls
  chain_history   IV, skew and OI history from our own chain snapshots
  order_check     one proposed order: fill, liquidity, hurdle, risk-neutral odds
  bars            daily bars, dividends/splits, the 5x3 realized-vol matrix
  fundamentals    spot quote, ^IRX rate, dividend policy, analyst-target staleness
  futures         month-by-month curves, prompt spreads, cracks, expiries
  etf_roll        USCF fund roll rules, windows and roll yield
  eia             EIA weekly petroleum data vs five-year bands, STEO
  cot             CFTC / ICE managed-money positioning
  edgar           SEC filings, insider trades, the structural sweep
  calendar        dated events, each with a source URL and `verified`
  polymarket      prediction-market reads with resolution text
  book            ledger positions marked to market, delta-notional vs cap
  pretrade        the one-call pre-trade page and its red flags
  jobs            refresh_all (daily snapshots) and weekly_check
"""
