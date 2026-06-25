"""Stable MCP entrypoint referenced by Claude configs.

The implementation lives in `optionslab.adapters.mcp`. This module exists
purely so external configs (claude_desktop_config.json, .mcp.json)
keep pointing at `python -m optionslab.mcp_server` and never have to
change when internals move.
"""

from __future__ import annotations

from .adapters.mcp import main

if __name__ == "__main__":
    main()
