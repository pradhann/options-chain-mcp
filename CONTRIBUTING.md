See [`docs/contributing.md`](docs/contributing.md) — same content, rendered with the docs.

TL;DR:

```bash
git clone https://github.com/pradhann/options-chain-mcp
cd options-chain-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

If you change math, please add a test that pins the new number.
