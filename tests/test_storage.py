"""Project-local positions storage: save/get/list/delete in a tmp dir."""

from __future__ import annotations

import pytest

from optionslab import Position
from optionslab.storage import (
    delete_position,
    get_position,
    list_positions,
    positions_path,
    save_position,
)


def test_save_get_list_delete_roundtrip(tmp_path, bull_call_spread):
    """A position saved in a tmp project root must round-trip cleanly."""
    (tmp_path / ".optionslab").mkdir()
    save_position(bull_call_spread, start=tmp_path)
    assert positions_path(tmp_path) == tmp_path / ".optionslab" / "positions.json"
    assert bull_call_spread.name in list_positions(start=tmp_path)

    loaded = get_position(bull_call_spread.name, start=tmp_path)
    assert loaded.legs == bull_call_spread.legs

    assert delete_position(bull_call_spread.name, start=tmp_path) is True
    assert delete_position(bull_call_spread.name, start=tmp_path) is False
    assert bull_call_spread.name not in list_positions(start=tmp_path)


def test_save_requires_name(tmp_path):
    """No name on Position and no name= override → ValueError."""
    pos = Position.from_dicts([
        {"side": "long", "option_type": "call",
         "strike": 100, "premium": 5},
    ])
    (tmp_path / ".optionslab").mkdir()
    with pytest.raises(ValueError):
        save_position(pos, start=tmp_path)
