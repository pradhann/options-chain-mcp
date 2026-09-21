"""Charts of the sourced market data. Each takes a feed envelope and draws it.

  plot_chain          IV smile (mid with bid/ask band) over OI by strike,
                      with feed spot, parity spot, and the walls marked
  plot_oi_walls       OI by strike alone (one panel, for the pre-trade sheet)
  plot_rv_matrix      the 5x3 realized-vol heatmap; worst cells outlined,
                      each cell read against the expiry's ATM IV when given
  plot_curve_overlay  futures curve now vs 5 and 21 sessions earlier
  plot_crack_history  crack spreads over the recorded sessions
  plot_cot_history    managed-money net as % of OI, with the 1y range shaded
  plot_eia_band       an EIA weekly series against its five-year seasonal band
  plot_order          a proposed option's P&L at expiry over the market's own
                      risk-neutral distribution, breakeven and implied move

A panel whose envelope has no data draws the not_verified reason instead
of an empty frame, so a chart never implies a number that was not sourced.
Every function accepts `ax` (or axes) to draw into, and returns the Figure.
"""

from __future__ import annotations

import math
import textwrap
from datetime import date

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from scipy.stats import norm

from .style import (
    BAND_COLOR,
    CALL_COLOR,
    HIGHLIGHT,
    INK,
    LOSS_COLOR,
    MUTED,
    PARITY_COLOR,
    PROFIT_COLOR,
    PUT_COLOR,
    SERIES,
    apply_style,
    date_ticks,
    dollar_axis,
    money,
    percent_axis,
    stamp,
    title_block,
    vline,
)


def _axes(ax: Axes | None, **fig_kw) -> tuple[Figure, Axes]:
    if ax is not None:
        return ax.figure, ax
    fig, ax = plt.subplots(**fig_kw)
    return fig, ax


def unavailable(ax: Axes, env: dict, title: str) -> None:
    """Draw 'not verified' with the reason in place of a chart."""
    ax.set_axis_off()
    title_block(ax, title)
    reason = env["not_verified"][0]["reason"] if env["not_verified"] else env["status"]
    body = "\n".join(["not verified", *textwrap.wrap(str(reason), 60)[:4]])
    ax.text(0.5, 0.5, body, ha="center", va="center", fontsize=10, color=MUTED,
            transform=ax.transAxes).set_in_layout(False)


# ---------- chains ----------

def _side(rows: list[dict], side: str) -> list[dict]:
    return sorted((r for r in rows if r["type"] == side), key=lambda r: r["strike"])


def _spot_lines(ax: Axes, check: dict, label: bool) -> None:
    vline(ax, check["feed_spot"], f"feed {check['feed_spot']:.2f}" if label else "")
    if check.get("parity_spot"):
        vline(ax, check["parity_spot"], f"parity {check['parity_spot']:.2f}" if label else "",
              color=PARITY_COLOR, style="--", row=1)


def _draw_oi(ax: Axes, rows: list[dict], walls: dict) -> None:
    for side, sign, color in (("call", 1, CALL_COLOR), ("put", -1, PUT_COLOR)):
        pts = [r for r in _side(rows, side) if r["oi"]]
        ks = [r["strike"] for r in pts]
        width = min(np.diff(sorted(set(ks)))) * 0.8 if len(set(ks)) > 1 else 1.0
        ax.bar(ks, [sign * r["oi"] for r in pts], width=width, color=color, alpha=0.75,
               label=f"{side} OI")
        for w in walls[side]:
            ax.annotate(f"{w['strike']:g}\n{w['pct_of_side']:.0f}%", (w["strike"], sign * w["oi"]),
                        xytext=(0, 4 * sign), textcoords="offset points", ha="center",
                        va="bottom" if sign > 0 else "top", fontsize=8, color=color)
    ax.axhline(0, color=MUTED, linewidth=0.8)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _p: f"{abs(v):,.0f}"))
    ax.set_ylabel("open interest  (calls ↑  puts ↓)")


def plot_chain(env: dict) -> Figure:
    """IV smile with bid/ask bands over mirrored OI, for one expiry (needs rows)."""
    with apply_style():
        fig, (ax_iv, ax_oi) = plt.subplots(2, 1, sharex=True, figsize=(11, 7.5),
                                           gridspec_kw={"height_ratios": [3, 2]})
        if env["data"] is None or not env["data"].get("rows"):
            unavailable(ax_iv, env, "Option chain")
            ax_oi.set_axis_off()
            return fig
        d = env["data"]
        u, check = d["usability"], d["spot_check"]
        lo, hi = 0.6 * check["feed_spot"], 1.5 * check["feed_spot"]
        rows = [r for r in d["rows"] if lo <= r["strike"] <= hi]
        for side, color in (("call", CALL_COLOR), ("put", PUT_COLOR)):
            pts = [r for r in _side(rows, side) if r["iv_mid_pct"] is not None]
            ks = [r["strike"] for r in pts]
            ax_iv.plot(ks, [r["iv_mid_pct"] for r in pts], "o-", color=color, markersize=3.5,
                       label=f"{side} mid-IV")
            band = [(r["strike"], r["iv_bid_pct"], r["iv_ask_pct"]) for r in pts
                    if r["iv_bid_pct"] is not None and r["iv_ask_pct"] is not None]
            if band:
                k, b, a = zip(*band, strict=True)
                ax_iv.fill_between(k, b, a, color=color, alpha=0.12, linewidth=0,
                                   label=f"{side} bid–ask IV")
        percent_axis(ax_iv)
        ax_iv.set_ylabel("implied vol")
        _spot_lines(ax_iv, check, label=True)
        ax_iv.legend(loc="upper right", ncols=2)
        _draw_oi(ax_oi, rows, d["walls"])
        _spot_lines(ax_oi, check, label=False)
        dollar_axis(ax_oi, decimals=0)
        ax_oi.set_xlabel("strike")
        flag = f" · {check['flag']}" if check["flag"] else ""
        title_block(ax_iv, f"{d['symbol']} {d['expiration']} chain — {u['verdict']}",
                    f"ATM IV {d['atm_iv_pct']}% · median spread {u['median_spread_pct']}% · "
                    f"strikes with OI≥500 {u['share_strikes_oi_ge_500']:.0%} · feed "
                    f"{check['feed_spot']:.2f} vs parity {check['parity_spot'] or float('nan'):.2f}"
                    f"{flag}")
        fig.tight_layout(rect=(0, 0.02, 1, 1))
        stamp(fig, env["provenance"])
        return fig


def plot_oi_walls(env: dict, ax: Axes | None = None) -> Figure:
    """OI by strike with walls and spot lines, one panel (needs rows)."""
    with apply_style():
        fig, ax = _axes(ax)
        if env["data"] is None or not env["data"].get("rows"):
            unavailable(ax, env, "Open interest")
            return fig
        d = env["data"]
        spot = d["spot_check"]["feed_spot"]
        rows = [r for r in d["rows"] if 0.6 * spot <= r["strike"] <= 1.5 * spot]
        _draw_oi(ax, rows, d["walls"])
        _spot_lines(ax, d["spot_check"], label=True)
        dollar_axis(ax)
        title_block(ax, f"OI walls {d['expiration']}",
                    f"chain {d['usability']['verdict']} · median spread "
                    f"{d['usability']['median_spread_pct']}%")
        return fig


# ---------- realized vol ----------

def plot_rv_matrix(env: dict, *, iv_pct: float | None = None, ax: Axes | None = None) -> Figure:
    """Heatmap of the fifteen RV cells; worst-for-long and worst-for-short outlined."""
    with apply_style({"axes.grid": False}):
        fig, ax = _axes(ax, figsize=(8.5, 5.2))
        if env["data"] is None:
            unavailable(ax, env, "Realized vol")
            return fig
        d = env["data"]
        ests, wins = list(d["matrix"]), d["windows"]
        grid = np.array([[d["matrix"][e][str(w)] if str(w) in d["matrix"][e]
                          else d["matrix"][e][w] for w in wins] for e in ests], dtype=float)
        ax.grid(False)
        ax.imshow(grid, cmap="Blues", aspect="auto",
                  vmin=np.nanmin(grid) * 0.9, vmax=np.nanmax(grid) * 1.05)
        for i in range(len(ests)):
            for j in range(len(wins)):
                v = grid[i, j]
                gap = "" if iv_pct is None else f"\nIV {iv_pct - v:+.1f}".replace("-", "−")
                text = f"{v:.1f}%{gap}"
                ax.text(j, i, text, ha="center", va="center", fontsize=9,
                        color="white" if v > np.nanmean(grid) else INK)
        for key, color in (("worst_for_long", LOSS_COLOR), ("worst_for_short", HIGHLIGHT)):
            cell = d[key]
            i, j = ests.index(cell["estimator"]), wins.index(cell["window"])
            ax.add_patch(Rectangle((j - 0.5, i - 0.5), 1, 1, fill=False, edgecolor=color,
                                   linewidth=2.5))
        ax.set_xticks(range(len(wins)), [f"{w}d" for w in wins])
        ax.set_xlabel("window, trading sessions")
        ax.set_yticks(range(len(ests)), [e.replace("_", " ") for e in ests])
        gap = d["gap_signature"]
        sub = (f"red box = worst for a long ({d['worst_for_long']['rv_pct']}%), amber = worst "
               f"for a short ({d['worst_for_short']['rv_pct']}%)")
        if iv_pct is not None:
            sub += f" · cells show IV {iv_pct}% minus RV"
        if gap["flag"]:
            sub += f" · {gap['flag']}: close-to-close exceeds Parkinson by >{gap['threshold_pts']:g} pts"
        title_block(ax, "Realized vol, five estimators × three windows", sub, width=80)
        if ax.figure is fig and fig.axes == [ax]:
            fig.tight_layout(rect=(0, 0.03, 1, 1))
            stamp(fig, env["provenance"])
        return fig


# ---------- futures ----------

def plot_curve_overlay(env: dict, ax: Axes | None = None) -> Figure:
    """The same contracts' settles now and n sessions earlier."""
    with apply_style():
        fig, ax = _axes(ax)
        if env["data"] is None:
            unavailable(ax, env, "Futures curve")
            return fig
        d = env["data"]
        x = np.arange(len(d["contracts"]))
        for c, color, style in zip(d["curves"], (INK, CALL_COLOR, BAND_COLOR),
                                   ("-", "--", ":"), strict=False):
            ys = np.array([np.nan if v is None else v for v in c["settles"]], dtype=float)
            ax.plot(x, ys, style, color=color, marker="o", markersize=3,
                    label=f"{c['label']} ({c['date']})")
        ax.set_xticks(x, d["contracts"], rotation=45, ha="right")
        ax.set_ylabel(d["unit"])
        ax.legend(loc="best")
        first = d["curves"][0]["settles"]
        m1m2 = first[0] - first[1] if first[0] is not None and first[1] is not None else None
        title_block(ax, f"{d['root']} curve, now vs earlier sessions",
                    f"{d['name']} · M1−M2 {m1m2:+.2f} {d['unit']}" if m1m2 is not None
                    else d["name"])
        if fig.axes == [ax]:
            fig.tight_layout(rect=(0, 0.03, 1, 1))
            stamp(fig, env["provenance"])
        return fig


def plot_crack_history(env: dict, ax: Axes | None = None) -> Figure:
    """Each crack over the recorded sessions, on its matching delivery month."""
    with apply_style():
        fig, ax = _axes(ax)
        if env["data"] is None or not env["data"]["cracks"]:
            unavailable(ax, env, "Crack spreads")
            return fig
        for c, color in zip(env["data"]["cracks"], SERIES, strict=False):
            days = [date.fromisoformat(p["date"]) for p in c["points"]]
            ax.plot(days, [p["value"] for p in c["points"]], color=color,
                    label=f"{c['name'].replace('_', ' ')} ({c['delivery_month']})")
            ax.annotate(f"{c['points'][-1]['value']:.1f}", (days[-1], c["points"][-1]["value"]),
                        xytext=(4, 0), textcoords="offset points", va="center",
                        fontsize=8.5, color=color)
        dollar_axis(ax, axis="y")
        ax.set_ylabel("$/bbl")
        ax.legend(loc="upper left")
        date_ticks(ax)
        first = env["data"]["cracks"][0]
        chg = first["points"][-1]["value"] - first["points"][0]["value"]
        title_block(ax, "Crack spreads", f"{first['name'].replace('_', ' ')} "
                    f"{first['points'][-1]['value']:.1f} $/bbl, {chg:+.1f} over "
                    f"{len(first['points']) - 1} sessions")
        if fig.axes == [ax]:
            fig.tight_layout(rect=(0, 0.03, 1, 1))
            stamp(fig, env["provenance"])
        return fig


# ---------- positioning ----------

def plot_cot_history(env: dict, ax: Axes | None = None) -> Figure:
    """Managed-money net as % of OI, the trailing-year range shaded."""
    with apply_style():
        fig, ax = _axes(ax)
        if env["data"] is None:
            unavailable(ax, env, "COT managed money")
            return fig
        prints = [p for p in env["data"]["prints"] if p["net_pct_oi"] is not None]
        days = [date.fromisoformat(p["report_date"]) for p in prints]
        vals = np.array([p["net_pct_oi"] for p in prints])
        year = vals[-52:]
        ax.fill_between(days[-52:], year.min(), year.max(), color=BAND_COLOR, alpha=0.45,
                        linewidth=0, label="trailing 52-week range")
        ax.plot(days, vals, color=CALL_COLOR, label="managed money net % of OI")
        ax.axhline(0, color=MUTED, linewidth=0.8)
        ax.scatter([days[-1]], [vals[-1]], color=HIGHLIGHT, zorder=3)
        pct = float((year <= vals[-1]).mean() * 100)
        percent_axis(ax, decimals=0)
        date_ticks(ax)
        ax.legend(loc="upper left")
        title_block(ax, f"{env['data']['root']} managed money — {env['data']['report_type']}",
                    f"latest {vals[-1]:+.1f}% of OI on {days[-1]} · "
                    f"net % of OI at the {pct:.0f}th percentile of its trailing year")
        if fig.axes == [ax]:
            fig.tight_layout(rect=(0, 0.03, 1, 1))
            stamp(fig, env["provenance"])
        return fig


# ---------- physical ----------

def plot_eia_band(env: dict, ax: Axes | None = None) -> Figure:
    """This year and last year against the prior five years' range by ISO week."""
    with apply_style():
        fig, ax = _axes(ax)
        if env["data"] is None:
            unavailable(ax, env, "EIA weekly")
            return fig
        d = env["data"]
        band = d["band"]
        weeks = [b["week"] for b in band]
        ax.fill_between(weeks, [b["min"] for b in band], [b["max"] for b in band],
                        color=BAND_COLOR, alpha=0.5, linewidth=0, label="prior 5-year range")
        ax.plot(weeks, [b["mean"] for b in band], "--", color=MUTED, linewidth=1.2,
                label="prior 5-year mean")
        years = sorted(d["years"], key=lambda y: y["year"])
        for year, color, width in ((years[-2], CALL_COLOR, 1.3), (years[-1], INK, 2.4)):
            pts = year["points"]
            ax.plot([p["week"] for p in pts], [p["value"] for p in pts], color=color,
                    linewidth=width, alpha=0.6 if width < 2 else 1, label=str(year["year"]))
        last = years[-1]["points"][-1]
        ax.scatter([last["week"]], [last["value"]], color=HIGHLIGHT, zorder=3)
        ax.annotate(f"{last['value']:,.0f}\n{last['week_ending']}", (last["week"], last["value"]),
                    xytext=(6, -4), textcoords="offset points", fontsize=8.5, color=HIGHLIGHT)
        ax.set_xlabel("ISO week")
        ax.set_ylabel(d["unit"])
        ax.set_xlim(1, 53)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _p: f"{v:,.0f}"))
        ax.legend(loc="best", ncols=2)
        week_band = next((b for b in band if b["week"] == last["week"]), None)
        where = ("" if week_band is None else
                 " — below the 5-year range" if last["value"] < week_band["min"] else
                 " — above the 5-year range" if last["value"] > week_band["max"] else
                 " — inside the 5-year range")
        title_block(ax, f"{d['series'].replace('_', ' ')} · {d['region']}",
                    f"week ending {last['week_ending']}: {last['value']:,.0f} {d['unit']}{where}")
        if fig.axes == [ax]:
            fig.tight_layout(rect=(0, 0.03, 1, 1))
            stamp(fig, env["provenance"])
        return fig


# ---------- a proposed order ----------

def plot_order(env: dict, ax: Axes | None = None) -> Figure:
    """P&L at expiry over the risk-neutral terminal distribution the price implies."""
    with apply_style():
        fig, ax = _axes(ax)
        d = env["data"]
        if d is None or not d.get("odds"):
            unavailable(ax, env, "Proposed order")
            return fig
        o, p, h, inp = d["odds"], d["price"], d["hurdle"], d["inputs"]
        kind, side = d["order"]["instrument"], d["order"]["side"]
        strike, qty, spot = float(d["order"]["strike"]), float(d["order"]["qty"]), p["spot"]
        sigma, T = inp["iv_mid_pct"] / 100, inp["T_years"]
        fwd = spot * math.exp((inp["r"] - inp["q"]) * T)
        mu, sd = math.log(fwd) - 0.5 * sigma * sigma * T, sigma * math.sqrt(T)
        s = np.linspace(math.exp(mu - 3.2 * sd), math.exp(mu + 3.2 * sd), 400)
        intrinsic = np.maximum(s - strike, 0) if kind == "call" else np.maximum(strike - s, 0)
        pnl = (1 if side == "long" else -1) * (intrinsic - p["fill_assumed"]) * 100 * qty
        density = norm.pdf((np.log(s) - mu) / sd) / (s * sd)

        ax.fill_between(s, 0, pnl, where=pnl >= 0, color=PROFIT_COLOR, alpha=0.15, linewidth=0)
        ax.fill_between(s, 0, pnl, where=pnl < 0, color=LOSS_COLOR, alpha=0.12, linewidth=0)
        ax.plot(s, pnl, color=INK, label="P&L at expiry")
        ax.axhline(0, color=MUTED, linewidth=0.8)
        dollar_axis(ax, axis="y")
        dollar_axis(ax, axis="x", decimals=0)
        ax.set_xlabel("underlying at expiry")
        ax.set_ylabel(f"P&L, {qty:g} contracts")

        dens = ax.twinx()
        dens.fill_between(s, 0, density, color=BAND_COLOR, alpha=0.55, linewidth=0,
                          label="risk-neutral distribution at expiry")
        dens.set_ylim(0, density.max() * 3)
        dens.set_axis_off()
        ax.set_zorder(dens.get_zorder() + 1)
        ax.patch.set_visible(False)
        vline(ax, spot, f"spot {spot:.2f}")
        vline(ax, o["median_terminal_price"], f"median {o['median_terminal_price']:.2f}",
              color=PARITY_COLOR, style=":", row=1)
        vline(ax, o["breakeven"], f"breakeven {o['breakeven']:.2f}", color=HIGHLIGHT,
              style="--", row=2)
        if h["implied_move_pct"]:
            m = h["implied_move_pct"] / 100
            ax.axvspan(spot * (1 - m), spot * (1 + m), color=BAND_COLOR, alpha=0.18,
                       label=f"implied move ±{h['implied_move_pct']}%")
        handles = ax.get_legend_handles_labels()[0] + dens.get_legend_handles_labels()[0]
        ax.legend(handles=handles, loc="upper left")
        loss_word = (f"P(total loss) {o['p_total_loss']:.0%}" if "p_total_loss" in o
                     else f"P(assigned) {o['p_assigned']:.0%}")
        title_block(ax, f"{side} {qty:g} × {d['order']['symbol']} {strike:g} {kind} "
                        f"{inp['expiration']} @ {p['fill_assumed']}",
                    f"median P&L {money(o['median_pnl_per_contract'] * qty)} · {loss_word} · "
                    f"P(profit) {o['p_profit']:.0%} · breakeven needs {o['move_to_breakeven_pct']:+.1f}% "
                    f"vs implied ±{h['implied_move_pct']}% · risk-neutral at IV {inp['iv_mid_pct']}%")
        if fig.axes[0] is ax:
            fig.tight_layout(rect=(0, 0.03, 1, 1))
            stamp(fig, env["provenance"])
        return fig
