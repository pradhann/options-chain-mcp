"""Build docs/assets/demo.gif and the README chart gallery from real, offline output.

    OPTIONSLAB_OFFLINE=1 python scripts/make_demo.py

The GIF types each command, shows what the CLI prints, then the chart it
draws. Everything comes from the snapshots in `.optionslab/snapshots/`, so
the demo shows sourced data only and never touches the network.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import matplotlib
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs" / "assets"
FONT_DIR = Path(matplotlib.__file__).parent / "mpl-data" / "fonts" / "ttf"
FONT = ImageFont.truetype(str(FONT_DIR / "DejaVuSansMono.ttf"), 15)
BOLD = ImageFont.truetype(str(FONT_DIR / "DejaVuSansMono-Bold.ttf"), 15)
SIZE = (1280, 800)
BG, FG, DIM, PROMPT, RED, GREEN = "#0f172a", "#e2e8f0", "#94a3b8", "#38bdf8", "#f87171", "#4ade80"
LINE_H = 20

ORDER = {"symbol": "SU", "instrument": "call", "side": "long", "qty": 10, "strike": 70,
         "expiration": "2027-03-19"}
SCENES = [
    (["pretrade", "SU", "2027-03-19", "--order", json.dumps(ORDER)], "sheet"),
    (["feed", "cot", "CL"], "cot"),
    (["plot", "curve", "CL"], "curve"),
]
CHART_ARGS = {"sheet": ["SU", "2027-03-19"], "cot": ["CL"], "curve": ["CL"],
              "chain": ["SU", "2027-03-19"], "rv": ["SU", "2027-03-19"], "cracks": [],
              "order": [], "eia": ["distillate_stocks", "PADD1"]}


def _cli(args: list[str]) -> list[str]:
    env = {**os.environ, "OPTIONSLAB_OFFLINE": "1"}
    out = subprocess.run([sys.executable, "-m", "optionslab", *args], capture_output=True,
                         text=True, env=env, cwd=ROOT, check=False)
    return out.stdout.splitlines()


def _terminal(lines: list[tuple[str, str]]) -> Image.Image:
    img = Image.new("RGB", SIZE, BG)
    draw = ImageDraw.Draw(img)
    for dot, color in enumerate(("#f87171", "#fbbf24", "#4ade80")):
        draw.ellipse((18 + dot * 22, 14, 30 + dot * 22, 26), fill=color)
    y = 44
    for text, color in lines[:(SIZE[1] - 60) // LINE_H]:
        draw.text((20, y), text[:132], font=BOLD if color == PROMPT else FONT, fill=color)
        y += LINE_H
    return img


def _color(line: str) -> str:
    if "✗" in line or "not verified" in line.lower() or "FEED_SUSPECT" in line:
        return RED
    if line.isupper() or line.startswith(("RED FLAGS", "NOT VERIFIED")):
        return FG
    return GREEN if "✓" in line else DIM if line.startswith("  !") else FG


def _chart_frame(path: Path) -> Image.Image:
    chart = Image.open(path).convert("RGB")
    chart.thumbnail((SIZE[0] - 40, SIZE[1] - 40))
    frame = Image.new("RGB", SIZE, "white")
    frame.paste(chart, ((SIZE[0] - chart.width) // 2, (SIZE[1] - chart.height) // 2))
    return frame


def _render_charts() -> dict[str, Path]:
    os.environ["OPTIONSLAB_OFFLINE"] = "1"
    sys.path.insert(0, str(ROOT))
    from optionslab.adapters.charts import render

    paths = {}
    for name, args in CHART_ARGS.items():
        with contextlib.redirect_stderr(io.StringIO()):
            paths[name], _ = render(name, args, order=ORDER,
                                    out=str(ASSETS / f"chart_{name}.png"))
    return paths


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    charts = _render_charts()
    frames, durations = [], []
    for args, chart in SCENES:
        shown = ["optionslab", *args[:3]] + (["--order", "'{...}'"] if "--order" in args else args[3:])
        command = "$ " + " ".join(shown)
        for i in range(4, len(command) + 4, 4):
            frames.append(_terminal([(command[:i], PROMPT)]))
            durations.append(40)
        output = [(line, _color(line)) for raw in _cli(args) for line in raw.split("\n")]
        for n in range(0, len(output) + 1, 6):
            frames.append(_terminal([(command, PROMPT), *output[:n]]))
            durations.append(90)
        durations[-1] = 2600
        frames.append(_chart_frame(charts[chart]))
        durations.append(3400)
    frames[0].save(ASSETS / "demo.gif", save_all=True, append_images=frames[1:],
                   duration=durations, loop=0, optimize=True)
    print(f"wrote {ASSETS / 'demo.gif'} ({len(frames)} frames) and "
          f"{len(charts)} gallery charts")


if __name__ == "__main__":
    main()
