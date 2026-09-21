"""Every sourced-data chart renders offline, and draws 'not verified' when data is missing."""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import pytest  # noqa: E402

from optionslab.adapters.charts import CHARTS, render  # noqa: E402
from optionslab.feeds.envelope import unsourced  # noqa: E402
from optionslab.plotting import market  # noqa: E402
from tests import market_fixtures as mf  # noqa: E402

ORDER = {"symbol": "TEST", "instrument": "call", "side": "long", "qty": 5,
         "strike": 110.0, "expiration": mf.EXPIRY}


@pytest.fixture
def seeded(offline_home, monkeypatch):
    monkeypatch.setenv("OPTIONSLAB_ASOF", mf.DAY.isoformat())
    mf.seed_chain()
    mf.seed_info()
    mf.seed_ohlc()
    return offline_home


@pytest.mark.parametrize("name,args", [
    ("chain", ["TEST", mf.EXPIRY]), ("rv", ["TEST", mf.EXPIRY]), ("order", []),
    ("sheet", ["TEST", mf.EXPIRY]), ("curve", ["CL"]), ("cracks", []), ("cot", ["CL"]),
])
def test_every_chart_renders_offline(seeded, tmp_path, name, args):
    path, png = render(name, args, order=ORDER, out=str(tmp_path / f"{name}.png"))
    assert path.exists() and png[:4] == b"\x89PNG"
    assert name in CHARTS


def test_missing_data_draws_the_reason_not_a_chart():
    env = unsourced("CL curve", "no priced unexpired contract", source="test")
    fig = market.plot_curve_overlay(env)
    texts = [t.get_text() for t in fig.axes[0].texts]
    assert any("not verified" in t and "no priced unexpired contract" in t for t in texts)
