"""Append-only trade ledger at `.optionslab/ledger.csv`.

Every change is a new row; nothing is edited in place. A position's
current state is the last row for its `id` in file order. The CSV
opens in any spreadsheet, and the history of every edit is the file.

Columns are fixed (LEDGER_COLUMNS). Money is per share for options (the
contract multiplier is applied by the marking code), per share for stock.
The three review columns — outcome, right_for_reason, autopsy — must be
filled when a position is closed; `blank_reviews()` lists the ones that
are not.
"""

from __future__ import annotations

import csv
import uuid
from dataclasses import asdict, dataclass, fields, replace
from datetime import UTC, date, datetime

from .snapshots import home

LEDGER_FILE = "ledger.csv"
INSTRUMENTS = ("stock", "call", "put")
SIDES = ("long", "short")
REVIEW_FIELDS = ("outcome", "right_for_reason", "autopsy")


@dataclass(frozen=True)
class LedgerRow:
    id: str
    recorded_at: str
    symbol: str
    instrument: str               # stock | call | put
    side: str                     # long | short
    thesis_id: str
    exit_condition: str
    recommended_qty: float        # shares or contracts
    executed_qty: float
    entry_date: str
    entry_price: float            # per share
    strike: float | None = None
    expiration: str | None = None
    exit_date: str | None = None
    exit_price: float | None = None
    outcome: str | None = None
    right_for_reason: str | None = None
    autopsy: str | None = None
    notes: str | None = None

    @property
    def is_open(self) -> bool:
        return self.exit_date is None

    def validate(self) -> None:
        if self.instrument not in INSTRUMENTS:
            raise ValueError(f"instrument must be one of {INSTRUMENTS}")
        if self.side not in SIDES:
            raise ValueError(f"side must be one of {SIDES}")
        if self.instrument != "stock" and (self.strike is None or not self.expiration):
            raise ValueError("options need strike and expiration")
        if not self.thesis_id or not self.exit_condition:
            raise ValueError("thesis_id and exit_condition are required at entry")
        if self.executed_qty <= 0 or self.recommended_qty <= 0:
            raise ValueError("quantities must be positive; direction is `side`")


LEDGER_COLUMNS = [f.name for f in fields(LedgerRow)]
_FLOATS = {"recommended_qty", "executed_qty", "entry_price", "strike", "exit_price"}


def ledger_path():
    return home() / LEDGER_FILE


def _parse(raw: dict) -> LedgerRow:
    vals = {}
    for k in LEDGER_COLUMNS:
        v = raw.get(k) or None
        vals[k] = float(v) if v is not None and k in _FLOATS else v
    return LedgerRow(**vals)


def _append(row: LedgerRow) -> LedgerRow:
    path = ledger_path()
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_COLUMNS)
        if new:
            w.writeheader()
        w.writerow({k: ("" if v is None else v) for k, v in asdict(row).items()})
    return row


def all_rows() -> list[LedgerRow]:
    path = ledger_path()
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return [_parse(r) for r in csv.DictReader(f)]


def current() -> dict[str, LedgerRow]:
    """Latest row per id (the file is append-only, so file order is time order)."""
    return {row.id: row for row in all_rows()}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def open_position(**kwargs) -> LedgerRow:
    """Record a new position. Requires thesis_id and exit_condition."""
    kwargs.setdefault("entry_date", date.today().isoformat())
    row = LedgerRow(id=uuid.uuid4().hex[:8], recorded_at=_now(), **kwargs)
    row.validate()
    return _append(row)


def update(position_id: str, **changes) -> LedgerRow:
    """Append a new state for `position_id` with `changes` applied."""
    state = current().get(position_id)
    if state is None:
        raise KeyError(f"no ledger position {position_id!r}")
    row = replace(state, recorded_at=_now(), **changes)
    row.validate()
    return _append(row)


def close_position(position_id: str, *, exit_price: float, outcome: str,
                   right_for_reason: str, autopsy: str,
                   exit_date: str | None = None) -> LedgerRow:
    """Close with the three review fields; none may be blank."""
    for name, v in (("outcome", outcome), ("right_for_reason", right_for_reason),
                    ("autopsy", autopsy)):
        if not v or not v.strip():
            raise ValueError(f"{name} is required to close a position")
    return update(position_id, exit_price=exit_price,
                  exit_date=exit_date or date.today().isoformat(),
                  outcome=outcome, right_for_reason=right_for_reason, autopsy=autopsy)


def blank_reviews() -> list[LedgerRow]:
    """Closed positions missing any of outcome / right_for_reason / autopsy."""
    return [r for r in current().values()
            if not r.is_open and any(not getattr(r, f) for f in REVIEW_FIELDS)]
