"""Named-Position storage in a project-local JSON file.

File location: `.optionslab/positions.json`, searched UP from the current
working directory. The first ancestor that has a `.optionslab/` directory
wins (the same convention as `.git`). When no project root exists, the
file is created in cwd on first save.

Schema:
    {
      "version": 1,
      "positions": {
        "<name>": <Position.to_dict() output>,
        ...
      }
    }
"""

from __future__ import annotations

import json
from pathlib import Path

from ..core.position import Position

PROJECT_DIR = ".optionslab"
POSITIONS_FILE = "positions.json"
SCHEMA_VERSION = 1


def _find_project_root(start: Path | None = None) -> Path | None:
    """Search up from `start` (default cwd) for an existing .optionslab dir."""
    here = (start or Path.cwd()).resolve()
    for parent in [here, *here.parents]:
        if (parent / PROJECT_DIR).is_dir():
            return parent
    return None


def positions_path(start: Path | None = None) -> Path:
    """Return the path the positions file would (or does) live at.

    If a project root exists, use it; otherwise default to cwd.
    """
    root = _find_project_root(start) or (start or Path.cwd())
    return Path(root) / PROJECT_DIR / POSITIONS_FILE


def _read(path: Path) -> dict:
    if not path.exists():
        return {"version": SCHEMA_VERSION, "positions": {}}
    with path.open() as f:
        data = json.load(f)
    if data.get("version") != SCHEMA_VERSION:
        raise ValueError(
            f"positions file {path} has schema version "
            f"{data.get('version')}, expected {SCHEMA_VERSION}"
        )
    return data


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def list_positions(*, start: Path | None = None) -> list[str]:
    """Names of every saved position."""
    return sorted(_read(positions_path(start))["positions"].keys())


def get_position(name: str, *, start: Path | None = None) -> Position:
    """Load one saved position by name."""
    data = _read(positions_path(start))
    if name not in data["positions"]:
        raise KeyError(f"position {name!r} not found in {positions_path(start)}")
    return Position.from_dict(data["positions"][name])


def save_position(position: Position, *,
                  name: str | None = None,
                  start: Path | None = None) -> Path:
    """Save a position (overwriting any existing entry).

    `name` overrides position.name; one of them must be set.
    """
    key = name or position.name
    if not key:
        raise ValueError("position must have a name (set on Position or pass name=)")
    path = positions_path(start)
    data = _read(path)
    payload = position.to_dict()
    payload["name"] = key
    data["positions"][key] = payload
    _write(path, data)
    return path


def delete_position(name: str, *, start: Path | None = None) -> bool:
    """Remove a saved position by name. Returns True if it existed."""
    path = positions_path(start)
    data = _read(path)
    if name not in data["positions"]:
        return False
    del data["positions"][name]
    _write(path, data)
    return True


def resolve_position_spec(*,
                          name: str | None = None,
                          json_str: str | None = None,
                          start: Path | None = None) -> Position:
    """Build a Position from one of: stored `name`, inline JSON.

    Exactly one source must be provided. Used by adapters so every CLI
    verb / MCP tool accepts positions the same way.
    """
    sources = sum(x is not None for x in (name, json_str))
    if sources != 1:
        raise ValueError(
            "exactly one of name= or json_str= must be provided"
        )
    if name:
        return get_position(name, start=start)
    parsed = json.loads(json_str)  # type: ignore[arg-type]
    if isinstance(parsed, dict):
        return Position.from_dict(parsed)
    if isinstance(parsed, list):
        return Position.from_dicts(parsed)
    raise ValueError("json_str must decode to a dict (Position) or list (legs)")
