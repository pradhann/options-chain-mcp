"""Enables `python -m optionslab ...` — routes to the CLI adapter."""

from __future__ import annotations

from .adapters.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
