"""Minimalist visual renderer for the finance channel.

Design brief: dark mode, one accent colour, generous whitespace, no chrome.
Charts draw themselves on rather than appearing, which is the channel's
signature motion and costs far less render time than animating every frame.

Every figure on screen comes from core.fincalc via a `compute` block. The
script model chooses WHAT to show; this module decides what the number IS.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

from core import fincalc as fc
from core.config import ffmpeg_bin
from core.theme import Theme, pick_font

# Draw-on lasts this long; the rest of the beat holds the finished frame.
REVEAL_SECONDS = 1.4
# Reveal frames are generated at this rate and resampled up to the project fps.
# Every frame is a full matplotlib redraw, which dominates render time, and
# fades/draw-ons carry no detail that 30fps would preserve over 15.
REVEAL_FPS = 15


# --------------------------------------------------------------------------
# compute: script declares fn + args, fincalc produces the numbers
# --------------------------------------------------------------------------

def compute(spec: dict) -> dict:
    c = dict(spec["compute"])
    fn = c.pop("fn")

    if fn == "compound_vs_invested":
        monthly = c.get("monthly", 5000)
        rate = c.get("annual_rate", 12)
        years = c.get("years", 20)
        grown = fc.compound_series(monthly=monthly, annual_rate=rate, years=years)
        put_in = fc.invested_series(monthly=monthly, years=years)
        return {
            "kind": "series",
            "series": [
                {"label": "What you put in", "points": put_in, "color": "muted"},
                {"label": "What it becomes", "points": grown, "color": "accent"},
            ],
            "headline": grown[-1][1],
            "headline_label": f"after {years:g} years",
            "years": years,
            "invested": put_in[-1][1],
            "gain": grown[-1][1] - put_in[-1][1],
        }

    if fn == "compound_vs_simple":
        monthly = c.get("monthly", 5000)
        rate = c.get("annual_rate", 12)
        years = c.get("years", 20)
        comp = fc.compound_series(monthly=monthly, annual_rate=rate, years=years)
        simp = fc.simple_interest_series(monthly=monthly, annual_rate=rate, years=years)
        return {
            "kind": "series",
            "series": [
                {"label": "Simple interest", "points": simp, "color": "accent_alt"},
                {"label": "Compounding", "points": comp, "color": "accent"},
            ],
            "headline": comp[-1][1] - simp[-1][1],
            "headline_label": "the difference",
            "years": years,
        }

    if fn == "sip_value":
        monthly = c.get("monthly", 5000)
        rate = c.get("annual_rate", 12)
        years = c.get("years", 20)
        value = fc.sip_future_value(monthly, rate, years)
        invested = monthly * round(years * 12)
        return {
            "kind": "pair",
            "bars": [
                {"label": "You invested", "value": invested, "color": "muted"},
                {"label": "It became", "value": value, "color": "accent"},
            ],
            "headline": value,
            "headline_label": f"{fc.format_inr(monthly)}/month for {years:g} years",
        }

    if fn == "rule_of_72":
        rate = c.get("annual_rate", 12)
        return {
            "kind": "scalar",
            "headline": fc.rule_of_72(rate),
            "headline_label": f"years to double at {rate:g}%",
            "unit": "years",
            "exact": fc.exact_doubling_years(rate),
        }

    if fn == "emi_split":
        sched = fc.emi_schedule(c.get("principal", 5_000_000), c.get("annual_rate", 8.5), c.get("years", 20))
        return {
            "kind": "stacked",
            "principal_by_year": sched.principal_by_year,
            "interest_by_year": sched.interest_by_year,
            "headline": sched.total_interest,
            "headline_label": "paid as interest",
            "emi": sched.emi,
        }

    if fn == "fee_drag":
        d = fc.fee_drag(c.get("monthly", 5000), c.get("annual_rate", 12), c.get("years", 20), c.get("fee_pct", 1.0))
        return {
            "kind": "pair",
            "bars": [
                {"label": "Without the fee", "value": d["gross"], "color": "accent"},
                {"label": "After the fee", "value": d["net"], "color": "accent_alt"},
            ],
            "headline": d["cost"],
            "headline_label": f"lost to a {c.get('fee_pct', 1.0):g}% yearly fee",
        }

    if fn == "cost_of_waiting":
        d = fc.cost_of_waiting(
            c.get("monthly", 5000), c.get("annual_rate", 12), c.get("years", 25), c.get("delay_years", 5)
        )
        delay = c.get("delay_years", 5)
        return {
            "kind": "pair",
            "bars": [
                {"label": "Start now", "value": d["on_time"], "color": "accent"},
                {"label": f"Start in {delay:g} years", "value": d["delayed"], "color": "accent_alt"},
            ],
            "headline": d["gap"],
            "headline_label": f"cost of waiting {delay:g} years",
        }

    if fn == "inflation_erosion":
        amount = c.get("amount", 1_000_000)
        infl = c.get("inflation_pct", 6)
        years = c.get("years", 20)
        worth = fc.purchasing_power(amount, infl, years)
        return {
            "kind": "pair",
            "bars": [
                {"label": "Today", "value": amount, "color": "accent"},
                {"label": f"In {years:g} years", "value": worth, "color": "accent_alt"},
            ],
            "headline": worth,
            "headline_label": f"what {fc.format_inr(amount)} would buy",
        }

    if fn == "real_return":
        nominal = c.get("nominal_pct", 12)
        infl = c.get("inflation_pct", 6)
        real = fc.real_return(nominal, infl)
        return {
            "kind": "scalar",
            "headline": real,
            "headline_label": f"real return after {infl:g}% inflation",
            "unit": "percent",
            "nominal": nominal,
        }

    raise ValueError(f"unknown compute fn: {fn}")


def to_bars(data: dict) -> list[dict]:
    """Coerce any compute result into bars.

    The script model picks the visual type independently of the compute
    function, so a time-series function can legitimately arrive at a bar
    renderer. Collapsing a series to its endpoints is the right reading of
    that pairing ("what you put in versus what it became"), so adapt rather
    than reject.
    """
    kind = data["kind"]
    if kind == "pair":
        return data["bars"]
    if kind == "series":
        return [
            {"label": s["label"], "value": s["points"][-1][1], "color": s["color"]}
            for s in data["series"]
        ]
    if kind == "scalar":
        return [{"label": data.get("headline_label", ""), "value": data["headline"],
                 "color": "accent"}]
    raise ValueError(f"cannot render {kind} as bars")


def to_series(data: dict) -> list[dict] | None:
    """Series for a line chart, or None if this data has no time dimension."""
    if data["kind"] == "series":
        return data["series"]
    return None


def _fmt_headline(data: dict) -> str:
    unit = data.get("unit")
    v = data["headline"]
    if unit == "years":
        return f"{v:.1f}".rstrip("0").rstrip(".") + " yrs"
    if unit == "percent":
        return f"{v:.2f}%"
    return fc.format_inr(v)


# --------------------------------------------------------------------------
# renderers: each returns a list of frames (matplotlib figures saved to PNG)
# --------------------------------------------------------------------------

def _base_axes(fig, th: Theme, width: float = 0.80):
    ax = fig.add_axes([0.10, 0.16, width, 0.62])
    ax.set_facecolor(th.bg)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(th.grid)
        ax.spines[side].set_linewidth(1.2)
    ax.tick_params(colors=th.muted, labelsize=17, length=0, pad=12)
    ax.grid(True, color=th.grid, linewidth=1.0, alpha=0.55)
    ax.set_axisbelow(True)
    return ax


def _title(fig, th: Theme, text: str, sub: str | None = None):
    fig.text(0.10, 0.875, text, color=th.text, fontsize=52, fontfamily=th.font,
             fontweight="semibold", va="center")
    if sub:
        fig.text(0.10, 0.815, sub, color=th.muted, fontsize=26, fontfamily=th.font, va="center")


def _color(th: Theme, name: str) -> str:
    return {"accent": th.accent, "accent_alt": th.accent_alt,
            "muted": th.muted, "text": th.text}.get(name, th.accent)


def render_text_card(spec: dict, th: Theme, n_frames: int) -> list:
    figs = []
    headline = spec.get("headline", "")
    sub = spec.get("sub")
    for i in range(n_frames):
        p = min(1.0, (i + 1) / max(1, n_frames))
        fig = th.figure()
        fig.text(0.5, 0.54 if sub else 0.5, headline, color=th.text, fontsize=76,
                 fontfamily=th.font, fontweight="semibold", ha="center", va="center",
                 alpha=min(1.0, p * 1.6))
        if sub:
            fig.text(0.5, 0.42, sub, color=th.muted, fontsize=32, fontfamily=th.font,
                     ha="center", va="center", alpha=max(0.0, min(1.0, (p - 0.25) * 2)))
        # accent rule under the headline, drawing outward from centre
        w = 0.06 * min(1.0, p * 1.3)
        fig.add_artist(plt.Line2D([0.5 - w, 0.5 + w], [0.335 if sub else 0.40, 0.335 if sub else 0.40],
                                  color=th.accent, linewidth=4, solid_capstyle="round"))
        figs.append(fig)
    return figs


def render_stat(spec: dict, th: Theme, n_frames: int) -> list:
    data = compute(spec)
    target = data["headline"]
    label = spec.get("label") or data.get("headline_label", "")
    figs = []
    for i in range(n_frames):
        p = min(1.0, (i + 1) / max(1, n_frames))
        eased = 1 - (1 - p) ** 3  # ease-out: fast then settles
        shown = dict(data)
        shown["headline"] = target * eased
        fig = th.figure()
        fig.text(0.5, 0.55, _fmt_headline(shown), color=th.accent, fontsize=150,
                 fontfamily=th.font, fontweight="bold", ha="center", va="center")
        fig.text(0.5, 0.38, label, color=th.muted, fontsize=34, fontfamily=th.font,
                 ha="center", va="center")
        figs.append(fig)
    return figs


def render_line_chart(spec: dict, th: Theme, n_frames: int) -> list:
    data = compute(spec)
    series = to_series(data)
    if series is None:
        # No time dimension in this compute result - bars are the honest reading.
        return render_bar_chart(spec, th, n_frames, _data=data)
    years = data.get("years", 20)
    figs = []
    for i in range(n_frames):
        p = min(1.0, (i + 1) / max(1, n_frames))
        fig = th.figure()
        _title(fig, th, spec.get("caption", ""), None)
        # Narrower axes than default: the end-of-line value labels live in the
        # margin on the right, and would otherwise be clipped off the frame.
        ax = _base_axes(fig, th, width=0.68)
        for s in series:
            pts = s["points"]
            cut = max(2, int(len(pts) * p))
            xs = [m / 12 for m, _ in pts[:cut]]
            ys = [v for _, v in pts[:cut]]
            col = _color(th, s["color"])
            ax.plot(xs, ys, color=col, linewidth=4.5, solid_capstyle="round",
                    label=s["label"])
            if cut > 2:
                # `_nolegend_` keeps these markers out of the legend, which
                # otherwise pairs labels with the wrong handles.
                ax.scatter([xs[-1]], [ys[-1]], color=col, s=90, zorder=5,
                           label="_nolegend_")
                ax.annotate(f"  {fc.format_inr(ys[-1])}", (xs[-1], ys[-1]), color=col,
                            fontsize=26, fontfamily=th.font, fontweight="semibold",
                            va="center", ha="left", annotation_clip=False)
        ax.set_xlim(0, years)
        top = max(v for s in series for _, v in s["points"]) * 1.20
        ax.set_ylim(0, top)
        ax.set_xlabel("years", color=th.muted, fontsize=22, fontfamily=th.font, labelpad=14)
        ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True, nbins=8))
        ax.yaxis.set_major_formatter(lambda v, _: fc.format_inr(v) if v else "0")
        leg = ax.legend(loc="upper left", frameon=False, fontsize=24, labelcolor=th.muted)
        for t in leg.get_texts():
            t.set_fontfamily(th.font)
        figs.append(fig)
    return figs


def render_bar_chart(spec: dict, th: Theme, n_frames: int, _data: dict | None = None) -> list:
    data = _data if _data is not None else compute(spec)
    figs = []

    if data["kind"] == "stacked":
        p_year = data["principal_by_year"]
        i_year = data["interest_by_year"]
        xs = list(range(1, len(p_year) + 1))
        for i in range(n_frames):
            p = min(1.0, (i + 1) / max(1, n_frames))
            fig = th.figure()
            _title(fig, th, spec.get("caption", ""), None)
            ax = _base_axes(fig, th)
            cut = max(1, int(len(xs) * p))
            ax.bar(xs[:cut], p_year[:cut], color=th.accent, label="Principal", width=0.72)
            ax.bar(xs[:cut], i_year[:cut], bottom=p_year[:cut], color=th.accent_alt,
                   label="Interest", width=0.72)
            ax.set_xlim(0.3, len(xs) + 0.7)
            ax.set_ylim(0, max(a + b for a, b in zip(p_year, i_year)) * 1.15)
            ax.set_xlabel("year", color=th.muted, fontsize=22, fontfamily=th.font, labelpad=14)
            ax.yaxis.set_major_formatter(lambda v, _: fc.format_inr(v) if v else "0")
            leg = ax.legend(loc="upper right", frameon=False, fontsize=24, labelcolor=th.muted)
            for t in leg.get_texts():
                t.set_fontfamily(th.font)
            figs.append(fig)
        return figs

    bars = to_bars(data)
    top = max(b["value"] for b in bars) * 1.28
    for i in range(n_frames):
        p = min(1.0, (i + 1) / max(1, n_frames))
        eased = 1 - (1 - p) ** 3
        fig = th.figure()
        _title(fig, th, spec.get("caption", ""), None)
        ax = _base_axes(fig, th)
        xs = list(range(len(bars)))
        vals = [b["value"] * eased for b in bars]
        ax.bar(xs, vals, color=[_color(th, b["color"]) for b in bars], width=0.34)
        ax.set_xlim(-0.72, len(bars) - 0.28)
        for x, b, v in zip(xs, bars, vals):
            ax.text(x, v + top * 0.035, fc.format_inr(v), color=_color(th, b["color"]),
                    fontsize=34, fontfamily=th.font, fontweight="bold", ha="center")
        ax.set_xticks(xs)
        ax.set_xticklabels([b["label"] for b in bars], fontsize=26, fontfamily=th.font)
        ax.tick_params(axis="x", colors=th.text, labelsize=26)
        ax.set_ylim(0, top)
        ax.set_yticks([])
        ax.grid(False)
        ax.spines["left"].set_visible(False)
        figs.append(fig)
    return figs


def render_split_compare(spec: dict, th: Theme, n_frames: int) -> list:
    left, right = spec["left"], spec["right"]
    figs = []
    for i in range(n_frames):
        p = min(1.0, (i + 1) / max(1, n_frames))
        fig = th.figure()
        fig.add_artist(plt.Line2D([0.5, 0.5], [0.22, 0.74], color=th.grid, linewidth=2))
        for col, x, accent in ((left, 0.28, th.accent), (right, 0.72, th.accent_alt)):
            fig.text(x, 0.70, col["title"], color=accent, fontsize=44, fontfamily=th.font,
                     fontweight="semibold", ha="center", va="center",
                     alpha=min(1.0, p * 2))
            for j, item in enumerate(col.get("items", [])[:5]):
                appear = 0.25 + j * 0.16
                a = max(0.0, min(1.0, (p - appear) * 4))
                fig.text(x, 0.575 - j * 0.095, item, color=th.text, fontsize=29,
                         fontfamily=th.font, ha="center", va="center", alpha=a)
        figs.append(fig)
    return figs


def render_list_reveal(spec: dict, th: Theme, n_frames: int) -> list:
    items = spec.get("items", [])[:5]
    title = spec.get("title", "")
    figs = []
    for i in range(n_frames):
        p = min(1.0, (i + 1) / max(1, n_frames))
        fig = th.figure()
        fig.text(0.14, 0.775, title, color=th.text, fontsize=52, fontfamily=th.font,
                 fontweight="semibold", va="center", alpha=min(1.0, p * 2.5))
        for j, item in enumerate(items):
            appear = 0.20 + j * 0.17
            a = max(0.0, min(1.0, (p - appear) * 4.5))
            y = 0.615 - j * 0.105
            fig.add_artist(plt.Line2D([0.145, 0.163], [y, y], color=th.accent,
                                      linewidth=4, solid_capstyle="round", alpha=a))
            fig.text(0.185, y, item, color=th.text, fontsize=33, fontfamily=th.font,
                     va="center", alpha=a)
        figs.append(fig)
    return figs


RENDERERS = {
    "text_card": render_text_card,
    "stat": render_stat,
    "line_chart": render_line_chart,
    "bar_chart": render_bar_chart,
    "split_compare": render_split_compare,
    "list_reveal": render_list_reveal,
}


# --------------------------------------------------------------------------
# beat -> clip
# --------------------------------------------------------------------------

def _clip_key(beat: dict, duration: float, th: Theme) -> str:
    payload = json.dumps(
        {"visual": beat["visual"], "duration": round(duration, 3),
         "size": [th.width, th.height], "fps": th.fps},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def render_beat(beat: dict, duration: float, out_path: Path, th: Theme) -> Path:
    """Render one beat to an mp4 of exactly `duration` seconds.

    Cached on the visual spec plus duration, so re-running a job after a later
    stage fails does not redo every frame.
    """
    key = _clip_key(beat, duration, th)
    sidecar = out_path.with_suffix(".key")
    if out_path.exists() and sidecar.exists() and sidecar.read_text(encoding="utf-8") == key:
        return out_path

    visual = beat["visual"]
    renderer = RENDERERS[visual["type"]]
    reveal = min(REVEAL_SECONDS, max(0.4, duration * 0.55))
    n_frames = max(2, int(reveal * REVEAL_FPS))

    frame_dir = out_path.parent / f"{out_path.stem}_frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    for old in frame_dir.glob("*.png"):
        old.unlink()

    figs = renderer(visual["spec"], th, n_frames)
    for idx, fig in enumerate(figs):
        fig.savefig(frame_dir / f"f{idx:05d}.png", facecolor=th.bg, dpi=100)
        plt.close(fig)

    # Play the reveal, then hold the last frame for the remainder of the beat.
    hold = max(0.0, duration - n_frames / REVEAL_FPS)
    last = frame_dir / f"f{n_frames - 1:05d}.png"
    cmd = [
        ffmpeg_bin(), "-y", "-loglevel", "error",
        "-framerate", str(REVEAL_FPS), "-i", str(frame_dir / "f%05d.png"),
        "-loop", "1", "-t", f"{hold:.3f}", "-i", str(last),
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p[v]",
        "-map", "[v]", "-r", str(th.fps),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    sidecar.write_text(key, encoding="utf-8")
    return out_path


if __name__ == "__main__":
    import argparse

    from core.config import PATHS, load_channel

    ap = argparse.ArgumentParser(description="render finance visuals from a script")
    ap.add_argument("--script", required=True)
    ap.add_argument("--channel", default="finance")
    ap.add_argument("--beat", type=int, help="render only this beat index")
    ap.add_argument("--still", action="store_true", help="save final PNG only, no mp4")
    args = ap.parse_args()

    th = Theme.from_channel(load_channel(args.channel))
    script = json.loads(Path(args.script).read_text(encoding="utf-8"))
    beats = [b for s in script["sections"] for b in s["beats"]]
    out_dir = Path(args.script).parent / "visuals"
    out_dir.mkdir(parents=True, exist_ok=True)

    targets = [args.beat] if args.beat is not None else range(len(beats))
    for i in targets:
        beat = beats[i]
        vt = beat["visual"]["type"]
        if args.still:
            figs = RENDERERS[vt](beat["visual"]["spec"], th, 1)
            dest = out_dir / f"beat_{i:02d}_{vt}.png"
            figs[-1].savefig(dest, facecolor=th.bg, dpi=100)
            plt.close(figs[-1])
        else:
            dest = out_dir / f"beat_{i:02d}.mp4"
            render_beat(beat, 4.0, dest, th)
        print(f"beat {i:02d} [{vt}] -> {dest.name}")


# --------------------------------------------------------------------------
# script plugin contract: prompt + validation for this format
# --------------------------------------------------------------------------

VISUAL_CONTRACT = """
Allowed "visual" objects. Use `compute` for ANY figure - never write numbers you
calculated yourself into a chart.

1. {"type":"text_card","spec":{"headline":"<=8 words","sub":"<=14 words or null"}}
2. {"type":"stat","spec":{"label":"<=8 words","compute":{...}}}
3. {"type":"line_chart","spec":{"compute":{...},"caption":"<=10 words"}}
4. {"type":"bar_chart","spec":{"compute":{...},"caption":"<=10 words"}}
5. {"type":"split_compare","spec":{"left":{"title":"<=4 words","items":["<=7 words"]},
                                   "right":{"title":"<=4 words","items":["<=7 words"]}}}
6. {"type":"list_reveal","spec":{"title":"<=6 words","items":["3-5 items, <=8 words each"]}}

Allowed `compute` blocks (fn + args; all rates are percent per annum):
  {"fn":"compound_vs_invested","monthly":5000,"annual_rate":12,"years":20}
  {"fn":"compound_vs_simple","monthly":5000,"annual_rate":12,"years":20}
  {"fn":"sip_value","monthly":5000,"annual_rate":12,"years":20}
  {"fn":"rule_of_72","annual_rate":12}
  {"fn":"emi_split","principal":5000000,"annual_rate":8.5,"years":20}
  {"fn":"fee_drag","monthly":5000,"annual_rate":12,"years":20,"fee_pct":1.0}
  {"fn":"cost_of_waiting","monthly":5000,"annual_rate":12,"years":25,"delay_years":5}
  {"fn":"inflation_erosion","amount":1000000,"inflation_pct":6,"years":20}
  {"fn":"real_return","nominal_pct":12,"inflation_pct":6}
"""

PROMPT = """You are an expert YouTube scriptwriter for a minimalist, elegant, faceless
finance channel called "{channel_name}".

Write a {dur_lo}-{dur_hi} second script about: {topic_title}

TONE: {tone}
AUDIENCE: {audience}
Short sentences. Natural pauses. No complex jargon. Calm, smart, easy for beginners.

STRUCTURE (must follow exactly):
  Hook      0:00-0:15  - {hook_intent}
  Lesson    0:15-3:00  - teach ONE clear concept using a simple real-life example or story
  Takeaway  3:00-3:30  - one golden rule, then a soft reminder to subscribe for more simple money tips

LENGTH: the combined `vo` text across all beats must total {w_lo}-{w_hi} words.
That is a hard requirement - count them.

HARD COMPLIANCE RULES (the script is auto-rejected if broken):
  - This is financial EDUCATION, never advice.
  - Never name a stock, mutual fund, scheme, AMC, broker or app.
  - No buy/sell calls, price targets, stop losses, or return guarantees.
  - No predictions about any price or index level.
  - Never imply the viewer should invest in anything specific.
  - Use illustrative rates framed as assumptions ("if it grew at 12 percent a year"),
    never as expected or promised returns.
  - Rupee amounts spoken in `vo` must be round and illustrative.

{visual_contract}

Break the script into 16-26 beats. Each beat = 1-3 spoken sentences plus ONE visual.
Vary visual types; do not use the same type more than twice in a row.

Return ONLY this JSON:
{{
  "topic_id": "{topic_id}",
  "title": "YouTube title, <=70 chars, curiosity-driven, no clickbait lies, no emoji",
  "golden_rule": "the single rule the viewer should remember, <=16 words",
  "description": "3-5 sentences for the YouTube description, plain text",
  "tags": ["10-14 lowercase youtube tags"],
  "sections": [
    {{"id":"hook","beats":[{{"vo":"...","visual":{{"type":"...","spec":{{}}}}}}]}},
    {{"id":"lesson","beats":[]}},
    {{"id":"takeaway","beats":[]}}
  ]
}}
"""

ALLOWED_VISUALS = {"text_card", "stat", "line_chart", "bar_chart", "split_compare", "list_reveal"}
ALLOWED_FNS = {
    "compound_vs_invested", "compound_vs_simple", "sip_value", "rule_of_72",
    "emi_split", "fee_drag", "cost_of_waiting", "inflation_erosion", "real_return",
}
SECTION_IDS = ["hook", "lesson", "takeaway"]
COMPUTE_REQUIRED = {"line_chart", "bar_chart", "stat"}


def build_prompt(topic: dict, channel: dict) -> str:
    sc = channel["script"]
    w_lo, w_hi = sc["target_words"]
    dur_lo, dur_hi = sc["duration_range"]
    hook_intent = next(s["intent"] for s in sc["structure"] if s["id"] == "hook")
    return PROMPT.format(
        channel_name=channel["name"],
        topic_title=topic["title"],
        topic_id=topic["id"],
        tone=sc["tone"],
        audience=sc["audience"],
        hook_intent=hook_intent,
        dur_lo=dur_lo, dur_hi=dur_hi,
        w_lo=w_lo, w_hi=w_hi,
        visual_contract=VISUAL_CONTRACT,
    )


def validate_script(data: object, channel: dict) -> None:
    import re

    from core import compliance

    w_lo, w_hi = channel["script"]["target_words"]

    if not isinstance(data, dict):
        raise TypeError("top level must be a JSON object")
    for key in ("title", "golden_rule", "description", "tags", "sections"):
        if key not in data:
            raise KeyError(f"missing key: {key}")
    if len(data["title"]) > 70:
        raise ValueError(f"title is {len(data['title'])} chars, max 70")
    if not isinstance(data["tags"], list) or not 8 <= len(data["tags"]) <= 16:
        raise ValueError("tags must be a list of 8-16 items")

    ids = [s.get("id") for s in data["sections"]]
    if ids != SECTION_IDS:
        raise ValueError(f"sections must be exactly {SECTION_IDS}, got {ids}")

    beats = [b for s in data["sections"] for b in s.get("beats", [])]
    if not 12 <= len(beats) <= 30:
        raise ValueError(f"{len(beats)} beats; need 16-26")

    for i, beat in enumerate(beats):
        if not beat.get("vo", "").strip():
            raise ValueError(f"beat {i} has empty vo")
        visual = beat.get("visual") or {}
        vtype = visual.get("type")
        if vtype not in ALLOWED_VISUALS:
            raise ValueError(f"beat {i}: visual type {vtype!r} not allowed")
        spec = visual.get("spec")
        if not isinstance(spec, dict):
            raise ValueError(f"beat {i}: visual.spec must be an object")
        compute_block = spec.get("compute")
        if compute_block is not None:
            fn = compute_block.get("fn")
            if fn not in ALLOWED_FNS:
                raise ValueError(f"beat {i}: compute.fn {fn!r} not allowed")
        elif vtype in COMPUTE_REQUIRED:
            raise ValueError(f"beat {i}: {vtype} requires a compute block")

    spoken = " ".join(b["vo"] for b in beats)
    words = len(re.findall(r"\b[\w']+\b", spoken))
    if not w_lo - 40 <= words <= w_hi + 60:
        raise ValueError(f"script is {words} words; need {w_lo}-{w_hi}")

    # Compliance is part of validation so a bad script is retried, not rendered.
    compliance.lint_script(spoken).raise_if_bad()
