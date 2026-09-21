"""Interactive shell: type a ticker, pick an expiration, view tables.

Stdlib `input()` only. A thin convenience for browsing chains and
glancing at saved positions; for analysis, use the verb subcommands.
"""

from __future__ import annotations

from ..data.chain import PRINT_COLUMNS, filter_near_money, load_chain
from ..data.quotes import list_expirations, make_ticker
from ..errors import OptionsLabError


def _ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    try:
        raw = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        return ""
    return raw or (default or "")


def _choose_expiration(expirations: list[str]) -> str | None:
    print(f"\n{len(expirations)} expirations:")
    for i, exp in enumerate(expirations):
        print(f"  [{i:2d}] {exp}")
    while True:
        ans = _ask("\nPick by number or date (blank = nearest, q = quit)", "0")
        if ans.lower() == "q":
            return None
        if ans in expirations:
            return ans
        if ans.isdigit() and 0 <= int(ans) < len(expirations):
            return expirations[int(ans)]
        print("  not a valid index or listed date — try again.")


def _print_chain(chain) -> None:
    cols = PRINT_COLUMNS + (["Delta", "Gamma", "Theta", "Vega"]
                            if "Delta" in chain.calls.columns else [])
    print(f"\n{chain.symbol} spot: ${chain.spot:,.2f}  exp {chain.expiration}")
    print(f"\n===== CALLS  {chain.symbol}  {chain.expiration} =====")
    print(chain.calls[cols].to_string(index=False))
    print(f"\n===== PUTS   {chain.symbol}  {chain.expiration} =====")
    print(chain.puts[cols].to_string(index=False))


def run_interactive() -> int:
    """One ticker per loop. Returns a process exit code."""
    print("optionslab interactive — Ctrl-C or blank ticker to exit\n")
    import pandas as pd
    pd.set_option("display.max_rows", None)
    pd.set_option("display.width", 220)
    pd.set_option("display.float_format", lambda v: f"{v:.2f}")

    while True:
        symbol = _ask("Ticker")
        if not symbol:
            print("bye.")
            return 0
        try:
            ticker = make_ticker(symbol)
            expirations = list_expirations(ticker)
            chosen = _choose_expiration(expirations)
            if chosen is None:
                continue
            chain = load_chain(symbol, expiration=chosen)

            n = _ask("Strikes nearest spot (blank = all)")
            if n.isdigit() and int(n) > 0:
                from dataclasses import replace
                chain = replace(
                    chain,
                    calls=filter_near_money(chain.calls, chain.spot, int(n)),
                    puts=filter_near_money(chain.puts, chain.spot, int(n)),
                )
            _print_chain(chain)
        except OptionsLabError as exc:
            print(f"  {exc}\n")
        except KeyboardInterrupt:
            print("\nbye.")
            return 0
        print()
