"""Week 3 charts.

  plot_rv_estimators       (Day 1) RV estimators side-by-side over time.
  plot_iv_smile            (Day 2) IV vs strike / log-moneyness / delta (3 panel).
  plot_vrp                 (Day 3) VRP time series + histogram.
  plot_term_structure      (Day 4) VIX-family curve + VIX3M/VIX ratio history.
  plot_skew_curve          (Day 5) IV(K) for calls and puts with 25Δ markers.
  plot_vix_strip_overlay   (Day 6) replicated vs published VIX over wing-boost.
  plot_dashboard_panel     (Day 7) compact dashboard visualization.

Every function returns the Matplotlib `Axes` or `Figure`; the caller
displays or saves. None calls plt.show().
"""

from __future__ import annotations

from collections.abc import Sequence

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.axes import Axes
from matplotlib.figure import Figure

from .style import (
    ACCENT,
    CALL_COLOR,
    LOSS_COLOR,
    PROFIT_COLOR,
    PUT_COLOR,
    SPOT_COLOR,
    apply_style,
    dollar_axis,
    save_if_requested,
)

# ---------- D1: RV estimator zoo ----------

def plot_rv_estimators(
    rv_table: pd.DataFrame,
    *,
    title: str | None = None,
    ax: Axes | None = None,
    save_path: str | None = None,
) -> Axes:
    """All RV estimators on one chart so divergences are visible."""
    with apply_style():
        if ax is None:
            _, ax = plt.subplots()
        for col in rv_table.columns:
            ax.plot(rv_table.index, rv_table[col], linewidth=1.2,
                    label=col.replace("_", " "))
        ax.set_xlabel("Date")
        ax.set_ylabel("Annualized RV (%)")
        ax.set_title(title or "Realized volatility — estimator comparison", pad=10)
        ax.legend(loc="best", fontsize=9, ncol=2)
        ax.figure.tight_layout()
        save_if_requested(ax.figure, save_path)
    return ax


# ---------- D2: IV smile, 3-panel ----------

def plot_iv_smile(
    chain,
    *,
    save_path: str | None = None,
) -> Figure:
    """IV vs strike, vs log-moneyness, vs delta — for one expiration's chain."""
    with apply_style({"figure.figsize": (15, 5)}):
        fig, axes = plt.subplots(1, 3)
        spot = chain.spot

        for kind, ax in zip(("strike", "logmoney", "delta"), axes, strict=True):
            for df, color, label in (
                (chain.calls, CALL_COLOR, "Calls"),
                (chain.puts, PUT_COLOR, "Puts"),
            ):
                v = df[df["IV %"] > 0]
                if v.empty:
                    continue
                if kind == "strike":
                    ax.plot(v["Strike"], v["IV %"], marker="o", markersize=3,
                            linewidth=1.2, label=label, color=color)
                    ax.set_xlabel("Strike ($)")
                elif kind == "logmoney":
                    k = np.log(v["Strike"].to_numpy(dtype=float) / spot)
                    ax.plot(k, v["IV %"], marker="o", markersize=3,
                            linewidth=1.2, label=label, color=color)
                    ax.set_xlabel("log(K / S)")
                else:
                    if "Delta" not in v.columns:
                        ax.set_visible(False)
                        continue
                    ax.plot(v["Delta"].abs(), v["IV %"],
                            marker="o", markersize=3, linewidth=1.2,
                            label=label, color=color)
                    ax.set_xlabel("|Δ|")
            if kind != "delta":
                ax.axvline(0 if kind == "logmoney" else spot,
                           color=SPOT_COLOR, linestyle="--", linewidth=1,
                           alpha=0.6,
                           label=("k=0" if kind == "logmoney" else f"Spot ${spot:.0f}"))
            ax.set_ylabel("IV (%)")
            ax.legend(loc="best", fontsize=8)

        fig.suptitle(
            f"{chain.symbol} · {chain.expiration} · IV smile",
            fontsize=15, fontweight="bold",
        )
        fig.tight_layout(rect=(0, 0, 1, 0.96))
        save_if_requested(fig, save_path)
    return fig


# ---------- D3: VRP time series + histogram ----------

def plot_vrp(
    series: Sequence[dict],
    *,
    marks: dict[str, str] | None = None,
    save_path: str | None = None,
) -> Figure:
    """VRP time series (top) + histogram (bottom).

    `series` is the list from VrpHistoryResult.series. `marks` is a dict
    of {label: 'YYYY-MM-DD'} to annotate notable dates (Volmageddon,
    Mar 2020, etc.).
    """
    df = pd.DataFrame(series)
    df["date"] = pd.to_datetime(df["date"])
    with apply_style({"figure.figsize": (12, 7)}):
        fig, (ax1, ax2) = plt.subplots(
            2, 1, gridspec_kw={"height_ratios": [2.2, 1]},
        )
        ax1.plot(df["date"], df["VRP"], color=CALL_COLOR, linewidth=1)
        ax1.axhline(0, color="gray", linewidth=0.7, alpha=0.7)
        ax1.fill_between(df["date"], df["VRP"], 0,
                         where=df["VRP"] > 0, color=PROFIT_COLOR, alpha=0.12)
        ax1.fill_between(df["date"], df["VRP"], 0,
                         where=df["VRP"] < 0, color=LOSS_COLOR, alpha=0.12)
        if marks:
            for label, date_str in marks.items():
                try:
                    d = pd.Timestamp(date_str)
                    row = df.loc[df["date"] == d]
                    if not row.empty:
                        y = float(row["VRP"].iloc[0])
                        ax1.scatter([d], [y], color="black", s=30, zorder=5)
                        ax1.annotate(label, xy=(d, y),
                                     xytext=(6, 8),
                                     textcoords="offset points",
                                     fontsize=9, color=ACCENT)
                except Exception:
                    continue
        ax1.set_ylabel("VRP (vol pts)")
        ax1.set_title("Variance risk premium  ·  VIX − trailing-21d RV",
                      pad=10)

        ax2.hist(df["VRP"], bins=60, color=CALL_COLOR, alpha=0.7,
                 edgecolor="white")
        ax2.axvline(0, color="gray", linewidth=0.7)
        ax2.axvline(df["VRP"].median(), color=SPOT_COLOR,
                    linestyle="--", linewidth=1.2,
                    label=f"median {df['VRP'].median():+.2f}")
        ax2.set_xlabel("VRP (vol pts)")
        ax2.set_ylabel("Days")
        ax2.legend(loc="upper left", fontsize=9)

        fig.tight_layout()
        save_if_requested(fig, save_path)
    return fig


# ---------- D4: term structure ----------

def plot_term_structure(
    hist: pd.DataFrame,
    *,
    save_path: str | None = None,
) -> Figure:
    """VIX-family levels (top) + VIX3M/VIX ratio (bottom)."""
    with apply_style({"figure.figsize": (12, 7)}):
        fig, (ax1, ax2) = plt.subplots(
            2, 1, gridspec_kw={"height_ratios": [2, 1]},
        )
        palette = {"VIX9D": "#888888", "VIX": CALL_COLOR,
                   "VIX3M": PROFIT_COLOR, "VIX6M": ACCENT}
        for col in hist.columns:
            if col in palette:
                ax1.plot(hist.index, hist[col], linewidth=1.2,
                         label=col, color=palette[col])
        ax1.set_ylabel("VIX-family level")
        ax1.set_title("VIX term structure  ·  history", pad=10)
        ax1.legend(loc="best", fontsize=9)

        if "VIX3M/VIX" in hist.columns:
            ax2.plot(hist.index, hist["VIX3M/VIX"],
                     color=PROFIT_COLOR, linewidth=1)
            ax2.axhline(1.0, color="gray", linewidth=0.7)
            ax2.fill_between(hist.index, hist["VIX3M/VIX"], 1,
                             where=hist["VIX3M/VIX"] >= 1,
                             color=PROFIT_COLOR, alpha=0.12)
            ax2.fill_between(hist.index, hist["VIX3M/VIX"], 1,
                             where=hist["VIX3M/VIX"] < 1,
                             color=LOSS_COLOR, alpha=0.18)
            ax2.set_ylabel("VIX3M / VIX")
            ax2.set_xlabel("Date")
        else:
            ax2.set_visible(False)

        fig.tight_layout()
        save_if_requested(fig, save_path)
    return fig


# ---------- D5: skew snapshot ----------

def plot_skew_curve(
    chain,
    *,
    save_path: str | None = None,
) -> Figure:
    """IV(K) for both calls and puts, with 25Δ vertical markers."""
    with apply_style():
        fig, ax = plt.subplots()
        for df, color, label in (
            (chain.calls, CALL_COLOR, "Calls"),
            (chain.puts, PUT_COLOR, "Puts"),
        ):
            v = df[df["IV %"] > 0]
            if v.empty:
                continue
            ax.plot(v["Strike"], v["IV %"], marker="o", markersize=3,
                    linewidth=1.2, label=label, color=color)
            # 25Δ markers (if Delta column is present).
            if "Delta" in v.columns:
                tgt = 0.25 if label == "Calls" else -0.25
                # closest by |Δ−target|
                row = v.iloc[(v["Delta"] - tgt).abs().argsort()[:1]]
                if not row.empty:
                    ax.axvline(float(row["Strike"].iloc[0]), color=color,
                               linestyle=":", linewidth=1.2, alpha=0.6,
                               label=f"25Δ {label.lower()[:-1]}")
        ax.axvline(chain.spot, color=SPOT_COLOR, linestyle="--",
                   linewidth=1.2, alpha=0.8, label=f"Spot ${chain.spot:,.2f}")
        ax.set_xlabel("Strike ($)")
        ax.set_ylabel("IV (%)")
        ax.set_title(
            f"{chain.symbol} · {chain.expiration} · skew & 25Δ markers",
            pad=10,
        )
        dollar_axis(ax, axis="x", decimals=0)
        ax.legend(loc="best", fontsize=8)
        ax.figure.tight_layout()
        save_if_requested(ax.figure, save_path)
    return fig


# ---------- D6: VIX strip overlay ----------

def plot_vix_strip_overlay(
    result,
    *,
    save_path: str | None = None,
) -> Figure:
    """Bar chart: replicated VIX, published VIX, and wing-boost replication."""
    labels = ["Replicated", "Published", f"+ {result.wing_boost_pct:.0f}% wings"]
    values = [
        result.replicated_vix,
        result.published_vix if result.published_vix is not None else float("nan"),
        result.replicated_vix_with_wing_boost,
    ]
    colors = [CALL_COLOR, SPOT_COLOR, LOSS_COLOR]
    with apply_style({"figure.figsize": (9, 5)}):
        fig, ax = plt.subplots()
        x = np.arange(len(labels))
        bars = ax.bar(x, values, color=colors, alpha=0.85)
        ax.set_xticks(x)
        ax.set_xticklabels(labels)
        ax.set_ylabel("VIX level")
        ax.set_title(
            f"Model-free VIX strip  ·  {result.symbol}  ·  ≈{result.days_target}d",
            pad=10,
        )
        for b, v in zip(bars, values, strict=True):
            if not np.isnan(v):
                ax.text(b.get_x() + b.get_width() / 2,
                        v + max(values) * 0.01,
                        f"{v:.2f}", ha="center", fontsize=10, color=ACCENT)
        ax.figure.tight_layout()
        save_if_requested(ax.figure, save_path)
    return fig


# ---------- D7: dashboard panel ----------

def plot_dashboard_panel(
    dashboard,
    *,
    save_path: str | None = None,
) -> Figure:
    """A compact one-figure summary: five numbered tiles with percentile bars."""
    fields = [
        ("30d ATM IV (VIX)",
         dashboard.atm_iv_pct,
         dashboard.atm_iv_percentile_2y),
        ("VRP",
         dashboard.vrp.vrp if dashboard.vrp else None,
         dashboard.vrp.percentile_2y if dashboard.vrp else None),
        ("VIX3M / VIX",
         dashboard.term.ratio_vix3m_over_vix if dashboard.term else None,
         dashboard.term.percentile_2y if dashboard.term else None),
        ("25Δ RR",
         dashboard.skew.risk_reversal_pct if dashboard.skew else None,
         dashboard.skew.rr_percentile if dashboard.skew else None),
        ("25Δ BF",
         dashboard.skew.butterfly_pct if dashboard.skew else None,
         dashboard.skew.bf_percentile if dashboard.skew else None),
    ]
    with apply_style({"figure.figsize": (14, 4)}):
        fig, axes = plt.subplots(1, 5)
        for ax, (label, value, pct) in zip(axes, fields, strict=True):
            ax.set_xlim(0, 1)
            ax.set_ylim(0, 1)
            ax.axis("off")
            ax.text(0.5, 0.85, label, ha="center", fontsize=11,
                    color=ACCENT, fontweight="bold")
            ax.text(0.5, 0.55,
                    f"{value:+.2f}" if value is not None else "n/a",
                    ha="center", fontsize=20)
            if pct and pct.percentile is not None:
                p = pct.percentile
                ax.text(0.5, 0.30,
                        f"{p:.1f}p  ·  n={pct.samples}",
                        ha="center", fontsize=9, color=ACCENT)
                bar_color = (PROFIT_COLOR if 25 <= p <= 75
                             else LOSS_COLOR if (p > 75 or p < 25)
                             else CALL_COLOR)
                ax.barh(0.10, p / 100.0, height=0.04, left=0.0,
                        color=bar_color, alpha=0.7)
                ax.plot([p / 100.0, p / 100.0], [0.08, 0.16],
                        color="black", linewidth=1)
            else:
                ax.text(0.5, 0.30, "no history", ha="center",
                        fontsize=9, color="gray")
        fig.suptitle(f"Vol Dashboard  ·  {dashboard.asof}",
                     fontsize=14, fontweight="bold")
        fig.tight_layout(rect=(0, 0, 1, 0.94))
        save_if_requested(fig, save_path)
    return fig
