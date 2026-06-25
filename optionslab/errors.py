"""Typed errors so library code can fail loudly without exiting the process.

Only the CLI translates these into exit codes; library callers (notebooks,
analysis scripts) catch them or let them propagate.
"""

from __future__ import annotations


class OptionsLabError(Exception):
    """Base class for every expected failure in this package."""


class NoOptionsDataError(OptionsLabError):
    """The provider returned no listed options for the symbol."""


class SpotUnavailableError(OptionsLabError):
    """No usable spot price; intrinsic/extrinsic cannot be computed."""


class ExpirationNotFoundError(OptionsLabError):
    """The requested expiration (by date or index) does not exist."""
