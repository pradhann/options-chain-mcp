"""Position / Leg construction and validation."""

from __future__ import annotations

import pytest

from optionslab import Leg, Position


def test_leg_validation():
    """Each invalid input raises a typed error at construction."""
    with pytest.raises(ValueError, match="side must"):
        Leg(side="bogus", option_type="call", strike=100, premium=1)
    with pytest.raises(ValueError, match="option_type"):
        Leg(side="long", option_type="bogus", strike=100, premium=1)
    with pytest.raises(ValueError, match="strike"):
        Leg(side="long", option_type="call", strike=-1, premium=1)
    with pytest.raises(ValueError, match="premium"):
        Leg(side="long", option_type="call", strike=100, premium=-0.01)
    with pytest.raises(ValueError, match="qty"):
        Leg(side="long", option_type="call", strike=100, premium=1, qty=0)


def test_position_must_have_legs():
    with pytest.raises(ValueError):
        Position(legs=())


def test_position_from_dicts_roundtrip(bull_call_spread):
    """from_dict(to_dict(p)) must recreate the same Position."""
    payload = bull_call_spread.to_dict()
    rebuilt = Position.from_dict(payload)
    assert rebuilt.name == bull_call_spread.name
    assert rebuilt.legs == bull_call_spread.legs


def test_leg_sign_property():
    assert Leg("long", "call", 100, 1).sign == 1
    assert Leg("short", "put", 100, 1).sign == -1
