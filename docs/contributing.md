# Contributing

Issues and PRs welcome. Small changes don't need a discussion first; for anything substantial please open an issue.

## Dev setup

```bash
git clone https://github.com/pradhann/options-chain-mcp
cd options-chain-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Running tests

```bash
pytest                # 50 tests, ~1 second
```

The principle is **"breaking the math should fail a test before it ships"**. If you fix a math bug or add a new analytic, please add a test that pins the number — `pytest.approx` is fine.

## Lint

```bash
ruff check optionslab tests
```

## Building the docs

```bash
pip install mkdocs-material
mkdocs serve            # http://127.0.0.1:8000
```

`mkdocs gh-deploy` ships them to GitHub Pages; CI does this automatically on `main`.

## Architecture invariants

The package layers strictly:

```
pricing  ←  core  ←  analysis  ←  plotting  ←  adapters (cli, mcp)
                  ↑                            ↑
                  data ── (siblings, shared)
                  storage
```

`pricing` and `core` import nothing from above them. `adapters` are thin shells — no logic in CLI/MCP that isn't also reachable from the library. Keep it that way and the package stays easy to reason about.

Every public analysis function takes a `Position` (and a `MarketContext` if it needs market state) and returns a typed dataclass with `.to_dict()`. The dict shape is what crosses MCP / CLI; the dataclass is what notebooks use.
