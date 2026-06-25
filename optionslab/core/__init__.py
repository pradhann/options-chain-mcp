"""Core types: Leg, Position, MarketContext, and typed result dataclasses.

This package defines the data model and depends on nothing in
`optionslab` except `pricing` (indirectly through analysis methods).
Everything downstream (analysis, plotting, adapters) builds on this.
"""

from .leg import Leg, parse_legs
from .market import MarketContext
from .position import Position
from .results import (
    GreeksResult,
    MetricsResult,
    PayoffResult,
    ScenarioResult,
    ValueResult,
)

__all__ = [
    "Leg",
    "Position",
    "MarketContext",
    "PayoffResult",
    "ValueResult",
    "GreeksResult",
    "MetricsResult",
    "ScenarioResult",
    "parse_legs",
]
