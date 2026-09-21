"""User configuration in `.optionslab/config.json`.

Only thresholds the user chose and lists the user maintains live here.
Nothing in this file is market data.

    {
      "watchlist": ["SU", "VLO", "BNO"],
      "book_value": 200000,
      "delta_notional_cap_pct": 25,
      "usability": {"max_median_spread_pct": 10, "min_share_oi_500": 0.1},
      "prompt_spread_thresholds": {"healthy": 3.0, "thinning": 2.0},
      "cot_stale_days": 10,
      "polymarket": [{"slug": "...", "label": "Hormuz reopening", "alert_above": 0.35}],
      "ir_calendar_urls": {"VLO": "https://investor.valero.com/..."}
    }
"""

from __future__ import annotations

import json
from typing import Any

from ..storage.snapshots import home

CONFIG_FILE = "config.json"

DEFAULTS: dict[str, Any] = {
    "watchlist": [],
    "book_value": None,
    "delta_notional_cap_pct": 25.0,
    # Chain usability verdict (near-the-money band). Defaults; set your own.
    "usability": {"max_median_spread_pct": 10.0, "min_share_oi_500": 0.10},
    "prompt_spread_thresholds": {"healthy": 3.0, "thinning": 2.0},
    "cot_stale_days": 10,
    "polymarket": [],
    "ir_calendar_urls": {},
    # The physical context `pretrade` shows beside every name. Your choice, not inferred.
    "pretrade_context": {
        "curve_roots": ["CL", "BZ", "HO", "RB"],
        "roll_funds": [],
        "eia_series": [{"series": "distillate_stocks", "region": "PADD1"}],
        "cot_roots": ["CL", "HO"],
    },
}


def load() -> dict:
    """Config merged over DEFAULTS. A missing file is the defaults."""
    path = home() / CONFIG_FILE
    user = json.loads(path.read_text()) if path.exists() else {}
    return {**DEFAULTS, **user}


def save(cfg: dict) -> None:
    """Write `cfg` as the whole config file."""
    (home() / CONFIG_FILE).write_text(json.dumps(cfg, indent=2) + "\n")


def get(key: str) -> Any:
    return load()[key]
