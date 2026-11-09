"""Self-contained HTML report for one chewing-bout experiment.

The page answers, in order: what was measured and how much to trust it,
which data went in, how every candidate compares, where the selected model
fails (by activity, tier and session), what to review, and what to record
next.  Every chart is inline SVG with a hover readout; every value is also in
a table.  No external assets are loaded.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from .dataset import CHEWING, EatingSession
from .evaluate import DecoderConfig, decode_bouts
from .recommendations import collection_recommendations


MODEL_LABELS = {
    "prior": "Prior (no skill)",
    "motion_energy": "Motion energy only",
    "logreg": "Logistic regression",
    "hgb": "Gradient boosting",
    "tcn": "Causal TCN (eating head)",
}
SPLIT_LABELS = {"dev_oof": "Development (out-of-fold)", "test": "Locked test"}


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _fmt(value: Any, digits: int = 3, percent: bool = False) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "—"
    if percent:
        return f"{100 * float(value):.1f}%"
    if isinstance(value, (int, np.integer)):
        return f"{int(value):,}"
    return f"{float(value):.{digits}f}"


def _ci(metrics: Mapping[str, Any] | None, key: str, percent: bool = False) -> str:
    if not metrics:
        return ""
    interval = (metrics.get("ci95") or {}).get(key) or {}
    if interval.get("low") is None:
        return ""
    return f"<span class=ci>95% CI {_fmt(interval['low'], percent=percent)}–{_fmt(interval['high'], percent=percent)}</span>"


CSS = """
.viz-root{color-scheme:light;--surface-1:#fcfcfb;--surface-2:#f3f2ef;--grid:#e4e3df;
--text-primary:#0b0b0b;--text-secondary:#52514e;--text-muted:#7a7974;
--series-1:#2a78d6;--series-2:#eb6834;--series-3:#1baf7a;--neutral-fill:#d9d8d3;
--critical:#d03b3b;--good:#0ca30c;--warning:#fab219}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .viz-root{color-scheme:dark;
--surface-1:#1a1a19;--surface-2:#242423;--grid:#383835;--text-primary:#f0efec;--text-secondary:#c3c2b7;
--text-muted:#94938c;--series-1:#3987e5;--series-2:#d95926;--series-3:#199e70;--neutral-fill:#4a4a46}}
:root[data-theme="dark"] .viz-root{color-scheme:dark;--surface-1:#1a1a19;--surface-2:#242423;--grid:#383835;
--text-primary:#f0efec;--text-secondary:#c3c2b7;--text-muted:#94938c;--series-1:#3987e5;--series-2:#d95926;
--series-3:#199e70;--neutral-fill:#4a4a46}
body{margin:0}
.viz-root{background:var(--surface-1);color:var(--text-primary);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;padding:24px 32px 64px;min-height:100vh}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:36px 0 8px;padding-top:8px;border-top:1px solid var(--grid)}
h3{font-size:14px;margin:18px 0 6px}
p,li{color:var(--text-secondary);max-width:980px}
.banner{background:var(--surface-2);border-left:3px solid var(--warning);padding:10px 14px;margin:14px 0;max-width:980px;color:var(--text-primary)}
.tiles{display:flex;flex-wrap:wrap;gap:12px;margin:12px 0}
.tile{background:var(--surface-2);padding:10px 14px;min-width:150px;border-radius:6px}
.tile .v{font-size:22px;font-weight:600;color:var(--text-primary)}.tile .k{font-size:12px;color:var(--text-secondary)}
.ci{display:block;font-size:11px;color:var(--text-muted)}
table{border-collapse:collapse;margin:8px 0 12px;font-size:12.5px}
th,td{padding:4px 10px;border-bottom:1px solid var(--grid);text-align:right;white-space:nowrap}
th{color:var(--text-secondary);font-weight:600}td:first-child,th:first-child{text-align:left}
td.l,th.l{text-align:left;white-space:normal}
tr.sel td{font-weight:600}
code{background:var(--surface-2);padding:1px 5px;border-radius:4px;font-size:12px}
.legend{display:flex;gap:16px;font-size:12px;color:var(--text-secondary);margin:4px 0}
.legend span{display:inline-flex;align-items:center;gap:6px}
.key-line{display:inline-block;width:16px;height:2px}.key-box{display:inline-block;width:12px;height:10px;border-radius:2px}
svg text{fill:var(--text-secondary);font-size:11px}
svg .axis{stroke:var(--grid);stroke-width:1}
.tip{position:fixed;pointer-events:none;background:var(--surface-1);border:1px solid var(--grid);border-radius:6px;padding:6px 9px;font-size:12px;box-shadow:0 2px 8px rgba(0,0,0,.12);display:none;z-index:10;color:var(--text-primary)}
.tip .row{display:flex;gap:8px;align-items:center}.tip b{font-weight:600}.tip .s{color:var(--text-secondary)}
details{margin:6px 0}summary{cursor:pointer;color:var(--text-primary)}
select,button{font:inherit;background:var(--surface-2);color:var(--text-primary);border:1px solid var(--grid);border-radius:4px;padding:3px 8px}
.warn{color:var(--critical)}.ok{color:var(--good)}
"""

SCRIPT = """
(function(){
const tip=document.createElement('div');tip.className='tip';document.body.appendChild(tip);
function show(rows,x,y){tip.replaceChildren();rows.forEach(r=>{const d=document.createElement('div');d.className='row';
 if(r.c){const k=document.createElement('span');k.className='key-line';k.style.background=r.c;d.appendChild(k);}
 const b=document.createElement('b');b.textContent=r.v;d.appendChild(b);const s=document.createElement('span');s.className='s';s.textContent=r.k;d.appendChild(s);tip.appendChild(d);});
 tip.style.display='block';tip.style.left=(x+14)+'px';tip.style.top=(y+14)+'px';}
function hide(){tip.style.display='none';}
document.querySelectorAll('svg[data-xh]').forEach(svg=>{
 const d=JSON.parse(svg.getAttribute('data-xh'));const line=svg.querySelector('.xh');
 svg.addEventListener('pointermove',ev=>{const r=svg.getBoundingClientRect();const vb=svg.viewBox.baseVal;
  const px=(ev.clientX-r.left)*vb.width/r.width;if(px<d.x0||px>d.x1){hide();if(line)line.setAttribute('visibility','hidden');return;}
  const xv=d.xmin+(px-d.x0)/(d.x1-d.x0)*(d.xmax-d.xmin);let best=0,bd=1e18;
  for(let i=0;i<d.x.length;i++){const dd=Math.abs(d.x[i]-xv);if(dd<bd){bd=dd;best=i;}}
  const sx=d.x0+(d.x[best]-d.xmin)/(d.xmax-d.xmin)*(d.x1-d.x0);
  if(line){line.setAttribute('x1',sx);line.setAttribute('x2',sx);line.setAttribute('visibility','visible');}
  const rows=[{k:d.xl,v:String(d.x[best])}];d.s.forEach(s=>{const v=s.y[best];rows.push({k:s.n,v:(v===null?'—':(typeof v==='number'?v.toFixed(3):String(v))),c:s.c});});
  show(rows,ev.clientX,ev.clientY);});
 svg.addEventListener('pointerleave',()=>{hide();if(line)line.setAttribute('visibility','hidden');});});
document.querySelectorAll('[data-tip]').forEach(el=>{el.addEventListener('pointermove',ev=>show(JSON.parse(el.getAttribute('data-tip')),ev.clientX,ev.clientY));
 el.addEventListener('pointerleave',hide);});
function openHash(){const id=decodeURIComponent(location.hash.slice(1));const el=document.getElementById(id);if(el&&el.tagName==='DETAILS'){el.open=true;}}
window.addEventListener('hashchange',openHash);openHash();
const sel=document.getElementById('session-select');if(sel){sel.addEventListener('change',()=>{if(sel.value)location.hash=sel.value;});}
const tg=document.getElementById('theme');if(tg){tg.addEventListener('click',()=>{const r=document.documentElement;
 const dark=r.getAttribute('data-theme')==='dark'||(!r.getAttribute('data-theme')&&matchMedia('(prefers-color-scheme: dark)').matches);
 r.setAttribute('data-theme',dark?'light':'dark');});}
})();
"""


def _tip(rows: Sequence[tuple[str, str]]) -> str:
    return _e(json.dumps([{"k": k, "v": v} for k, v in rows]))


def _line_chart(
    *,
    series: Sequence[tuple[str, str, Sequence[float], Sequence[float | None]]],
    x_label: str,
    y_label: str,
    y_range: tuple[float, float],
    marker_x: float | None = None,
    marker_label: str = "",
    width: int = 560,
    height: int = 260,
    x_format: str = "{:.2f}",
    x_ticks: Sequence[float] | None = None,
) -> str:
    left, right, top, bottom = 48, 16, 12, 34
    x0, x1, y0, y1 = left, width - right, top, height - bottom
    xs_all = [x for _, _, xs, _ in series for x in xs]
    xmin, xmax = (min(xs_all), max(xs_all)) if xs_all else (0.0, 1.0)
    if xmax <= xmin:
        xmax = xmin + 1
    ymin, ymax = y_range

    def sx(x: float) -> float:
        return x0 + (x - xmin) / (xmax - xmin) * (x1 - x0)

    def sy(y: float) -> float:
        return y1 - (y - ymin) / (ymax - ymin) * (y1 - y0)

    parts = [f'<line class=axis x1={x0} y1={y1} x2={x1} y2={y1} />']
    for tick in np.linspace(ymin, ymax, 5):
        parts.append(f'<line class=axis x1={x0} x2={x1} y1={sy(tick):.1f} y2={sy(tick):.1f} />')
        parts.append(f'<text x={x0 - 6} y={sy(tick) + 4:.1f} text-anchor=end>{tick:.2g}</text>')
    for tick in (x_ticks if x_ticks is not None else np.linspace(xmin, xmax, 6)):
        parts.append(f'<text x={sx(tick):.1f} y={y1 + 16} text-anchor=middle>{_e(x_format.format(tick))}</text>')
    parts.append(f'<text x={(x0 + x1) / 2:.0f} y={height - 2} text-anchor=middle>{_e(x_label)}</text>')
    parts.append(f'<text x=12 y={(y0 + y1) / 2:.0f} transform="rotate(-90 12 {(y0 + y1) / 2:.0f})" text-anchor=middle>{_e(y_label)}</text>')
    if marker_x is not None:
        parts.append(f'<line x1={sx(marker_x):.1f} x2={sx(marker_x):.1f} y1={y0} y2={y1} stroke="var(--text-muted)" stroke-width=1 />')
        parts.append(f'<text x={sx(marker_x) + 4:.1f} y={y0 + 10}>{_e(marker_label)}</text>')
    readout = {"x0": x0, "x1": x1, "xmin": xmin, "xmax": xmax, "xl": x_label, "x": [], "s": []}
    for name, color, xs, ys in series:
        points = [(sx(x), sy(y)) for x, y in zip(xs, ys) if y is not None and np.isfinite(y)]
        if points:
            path = "M" + " L".join(f"{px:.1f},{py:.1f}" for px, py in points)
            parts.append(f'<path d="{path}" fill=none stroke="{color}" stroke-width=2 stroke-linejoin=round stroke-linecap=round />')
            last = points[-1]
            parts.append(f'<circle cx={last[0]:.1f} cy={last[1]:.1f} r=4 fill="{color}" stroke="var(--surface-1)" stroke-width=2 />')
        readout["x"] = [round(float(x), 3) for x in xs]
        readout["s"].append({
            "n": name,
            "c": color,
            "y": [None if y is None or not np.isfinite(y) else round(float(y), 4) for y in ys],
        })
    parts.append(f'<line class=xh x1=0 x2=0 y1={y0} y2={y1} stroke="var(--text-muted)" stroke-width=1 visibility=hidden />')
    legend = "".join(
        f'<span><i class=key-line style="background:{color}"></i>{_e(name)}</span>' for name, color, _, _ in series
    )
    return (
        (f'<div class=legend>{legend}</div>' if len(series) > 1 else "")
        + f'<svg viewBox="0 0 {width} {height}" width={width} height={height} role=img '
        f'aria-label="{_e(y_label)} by {_e(x_label)}" data-xh="{_e(json.dumps(readout))}">'
        + "".join(parts)
        + "</svg>"
    )


def _dot_ci_chart(rows: Sequence[tuple[str, str, float | None, float | None, float | None, str]], *, x_label: str) -> str:
    """Horizontal dot + 95% CI whiskers; rows are (group, series, value, lo, hi, color)."""

    width, row_h, left, right = 620, 26, 190, 24
    height = 24 + row_h * len(rows) + 40
    x0, x1 = left, width - right

    def sx(v: float) -> float:
        return x0 + v * (x1 - x0)

    parts = []
    for tick in (0, 0.25, 0.5, 0.75, 1.0):
        parts.append(f'<line class=axis x1={sx(tick):.1f} x2={sx(tick):.1f} y1=16 y2={height - 38} />')
        parts.append(f'<text x={sx(tick):.1f} y={height - 24} text-anchor=middle>{tick:g}</text>')
    for index, (group, series, value, lo, hi, color) in enumerate(rows):
        y = 26 + index * row_h
        parts.append(f'<text x={left - 8} y={y + 4} text-anchor=end>{_e(group)} · {_e(series)}</text>')
        if value is None:
            parts.append(f'<text x={x0 + 4} y={y + 4}>no data</text>')
            continue
        tip = _tip([(f"{group} · {series}", _fmt(value)), ("95% CI", f"{_fmt(lo)}–{_fmt(hi)}")])
        if lo is not None and hi is not None:
            parts.append(f'<line x1={sx(lo):.1f} x2={sx(hi):.1f} y1={y} y2={y} stroke="{color}" stroke-width=2 />')
        parts.append(f'<circle cx={sx(value):.1f} cy={y} r=5 fill="{color}" stroke="var(--surface-1)" stroke-width=2 />')
        parts.append(f'<text x={sx(value) + 9:.1f} y={y - 6}>{_fmt(value, 2)}</text>')
        parts.append(f'<rect x={x0} y={y - 12} width={x1 - x0} height=24 fill=transparent data-tip="{tip}" />')
    parts.append(f'<text x={(x0 + x1) / 2:.0f} y={height} text-anchor=middle>{_e(x_label)}</text>')
    return f'<svg viewBox="0 0 {width} {height + 4}" width={width} height={height + 4} role=img aria-label="{_e(x_label)}">' + "".join(parts) + "</svg>"


def _timeline(
    session: EatingSession,
    rows: pd.DataFrame,
    *,
    selected: str,
    comparison: str | None,
    threshold: float,
) -> str:
    width = 1120
    left, right = 118, 16
    x0, x1 = left, width - right
    duration = max(session.duration_s, float(rows["t_end"].max()) if len(rows) else 1.0, 1.0)

    def sx(t: float) -> float:
        return x0 + max(0.0, min(t, duration)) / duration * (x1 - x0)

    lanes: list[str] = []
    y = 8
    # Guided instructions: context only, never labels.
    if session.context_spans:
        lanes.append(f'<text x={left - 8} y={y + 11} text-anchor=end>Instruction</text>')
        for start, end, kind in session.context_spans:
            name = kind.split(":", 1)[-1]
            tip = _tip([("instruction (not a label)", name), ("time", f"{start:.1f}–{end:.1f} s")])
            lanes.append(
                f'<rect x={sx(start):.1f} y={y} width={max(sx(end) - sx(start) - 2, 1):.1f} height=14 rx=2 '
                f'fill="var(--surface-2)" data-tip="{tip}" />'
            )
            if sx(end) - sx(start) > 34:
                lanes.append(f'<text x={sx(start) + 4:.1f} y={y + 11}>{_e(name)}</text>')
        y += 22
    lanes.append(f'<text x={left - 8} y={y + 11} text-anchor=end>Human label</text>')
    for interval in session.intervals:
        fill = "var(--series-3)" if interval.label == CHEWING else "var(--neutral-fill)"
        tip = _tip([(interval.label.replace("_", " "), f"{interval.start:.1f}–{interval.end:.1f} s"), ("tier", interval.tier)])
        lanes.append(
            f'<rect x={sx(interval.start):.1f} y={y} width={max(sx(interval.end) - sx(interval.start) - 2, 1):.1f} '
            f'height=14 rx=2 fill="{fill}" data-tip="{tip}" />'
        )
    y += 22
    plot_top, plot_bottom = y + 4, y + 104

    def sp(p: float) -> float:
        return plot_bottom - p * (plot_bottom - plot_top)

    lanes.append(f'<text x={left - 30} y={(plot_top + plot_bottom) / 2 + 4:.0f} text-anchor=end>P(chewing)</text>')
    for tick in (0.0, 0.5, 1.0):
        lanes.append(f'<line class=axis x1={x0} x2={x1} y1={sp(tick):.1f} y2={sp(tick):.1f} />')
        lanes.append(f'<text x={x0 - 4} y={sp(tick) + 4:.1f} text-anchor=end font-size=10>{tick:g}</text>')
    lanes.append(
        f'<line x1={x0} x2={x1} y1={sp(threshold):.1f} y2={sp(threshold):.1f} stroke="var(--text-muted)" stroke-width=1 />'
        f'<text x={x1 - 2} y={sp(threshold) - 3:.1f} text-anchor=end font-size=10>threshold {threshold:.2f}</text>'
    )
    readout_series = []
    for name, color in ((selected, "var(--series-1)"), (comparison, "var(--series-2)")):
        if not name or f"p_{name}" not in rows:
            continue
        values = rows[f"p_{name}"].to_numpy()
        segments, current = [], []
        for t, p in zip(rows["t_end"], values):
            if np.isfinite(p):
                current.append(f"{sx(t):.1f},{sp(p):.1f}")
            elif current:
                segments.append(current)
                current = []
        if current:
            segments.append(current)
        for segment in segments:
            lanes.append(
                f'<path d="M{" L".join(segment)}" fill=none stroke="{color}" '
                f'stroke-width={2 if name == selected else 1.5} stroke-linejoin=round />'
            )
        readout_series.append({"n": MODEL_LABELS.get(name, name), "c": color, "y": [
            None if not np.isfinite(v) else round(float(v), 3) for v in values
        ]})
    y = plot_bottom + 8
    states, bouts = decode_bouts(
        rows["t_end"].to_numpy(), rows[f"p_{selected}"].to_numpy(), DecoderConfig.from_threshold(threshold)
    )
    lanes.append(f'<text x={left - 8} y={y + 10} text-anchor=end>Detected bout</text>')
    for start, end in bouts:
        tip = _tip([("detected bout", f"{start:.1f}–{end:.1f} s")])
        lanes.append(
            f'<rect x={sx(start):.1f} y={y} width={max(sx(end) - sx(start) - 2, 2):.1f} height=12 rx=2 '
            f'fill="var(--series-1)" data-tip="{tip}" />'
        )
    y += 18
    labelled = rows["label"].to_numpy() >= 0
    probability = rows[f"p_{selected}"].to_numpy()
    predicted = np.where(np.isfinite(probability), probability >= threshold, False)
    for lane, wrong in (
        ("False positive", labelled & (rows["label"].to_numpy() == 0) & predicted),
        ("False negative", labelled & (rows["label"].to_numpy() == 1) & ~predicted),
    ):
        lanes.append(f'<text x={left - 8} y={y + 9} text-anchor=end>{lane}</text>')
        step = max((x1 - x0) / max(len(rows), 1), 1.5)
        for t, p in zip(rows["t_end"].to_numpy()[wrong], probability[wrong]):
            tip = _tip([(lane.lower(), f"{t:.1f} s"), ("P(chewing)", _fmt(p))])
            lanes.append(
                f'<rect x={sx(t) - step / 2:.1f} y={y} width={step:.1f} height=10 fill="var(--critical)" data-tip="{tip}" />'
            )
        y += 15
    # AirPods rotation-rate magnitude, 10 Hz envelope.
    trace_top, trace_bottom = y + 6, y + 56
    t = session.motion_t
    magnitude = np.linalg.norm(session.imu[:, 3:6], axis=1)
    keep = (t >= 0) & (t <= duration)
    t, magnitude = t[keep], magnitude[keep]
    if len(t):
        bins = np.floor(t * 10).astype(int)
        frame = pd.DataFrame({"b": bins, "m": magnitude}).groupby("b")["m"].max()
        top = max(float(np.percentile(frame.values, 99)), 1e-3)
        points = " L".join(
            f"{sx(b / 10):.1f},{trace_bottom - min(v / top, 1.0) * (trace_bottom - trace_top):.1f}"
            for b, v in frame.items()
        )
        lanes.append(f'<path d="M{points}" fill=none stroke="var(--text-muted)" stroke-width=1 />')
        lanes.append(f'<text x={left - 30} y={(trace_top + trace_bottom) / 2 + 4:.0f} text-anchor=end>|rotation|</text>')
        lanes.append(f'<text x={left - 30} y={(trace_top + trace_bottom) / 2 + 17:.0f} text-anchor=end font-size=10>rad/s</text>')
        lanes.append(f'<text x={x0 - 4} y={trace_top + 8:.0f} text-anchor=end font-size=10>{top:.2g}</text>')
    y = trace_bottom + 6
    lanes.append(f'<line class=axis x1={x0} x2={x1} y1={y} y2={y} />')
    step = 10 if duration <= 120 else 30 if duration <= 400 else 60
    for tick in np.arange(0, duration + 1e-9, step):
        lanes.append(f'<text x={sx(tick):.1f} y={y + 14} text-anchor=middle>{tick:.0f}s</text>')
    height = y + 20
    readout = {
        "x0": x0, "x1": x1, "xmin": 0.0, "xmax": duration, "xl": "window end (s)",
        "x": [round(float(v), 2) for v in rows["t_end"]],
        "s": readout_series + [{"n": "human label (window)", "c": "var(--series-3)", "y": [
            {1: "chewing", 0: "not chewing"}.get(int(v), "excluded") for v in rows["label"]
        ]}],
    }
    lanes.append(f'<line class=xh x1=0 x2=0 y1={plot_top} y2={plot_bottom} stroke="var(--text-muted)" stroke-width=1 visibility=hidden />')
    return (
        f'<svg viewBox="0 0 {width} {height}" width={width} height={height} role=img '
        f'aria-label="Timeline for {_e(session.session_id)}" data-xh="{_e(json.dumps(readout))}">'
        + "".join(lanes)
        + "</svg>"
    )


def _metric_row(name: str, split: str, entry: Mapping[str, Any], selected: str | None) -> str:
    metrics = entry.get(split)
    if not metrics:
        return f"<tr><td>{_e(MODEL_LABELS.get(name, name))}</td><td colspan=11>no data</td></tr>"
    bouts = metrics["bouts"]
    cls = " class=sel" if name == selected else ""
    return (
        f"<tr{cls}><td>{_e(MODEL_LABELS.get(name, name))}</td>"
        f"<td>{_fmt(metrics['auc_pr'])}</td><td>{_fmt(metrics['auc_roc'])}</td>"
        f"<td>{_fmt(entry['threshold'], 2)}</td>"
        f"<td>{_fmt(metrics['precision'])}</td><td>{_fmt(metrics['recall'])}</td><td>{_fmt(metrics['f1'])}</td>"
        f"<td>{_fmt(metrics['false_positive_rate'], percent=True)}</td><td>{_fmt(metrics['false_negative_rate'], percent=True)}</td>"
        f"<td>{bouts['detected_bouts']}/{bouts['reference_bouts']}</td>"
        f"<td>{bouts['false_bouts']} ({_fmt(bouts['false_bouts_per_negative_hour'], 1)}/h)</td>"
        f"<td>{_fmt(bouts['onset_latency_median_s'], 1)}</td>"
        f"<td>{_fmt(entry['inference_latency_ms'], 2)}</td></tr>"
    )


def write_report(
    run_dir: Path,
    *,
    summary: Mapping[str, Any],
    manifest: Mapping[str, Any],
    frame: pd.DataFrame,
    sessions: Mapping[str, EatingSession],
    index: Mapping[str, Any],
) -> Path:
    results = summary["results"]
    selected = summary.get("selected_model")
    comparison = "tcn" if selected != "tcn" and "tcn" in results else None
    config = summary["config"]
    counts = summary["counts"]
    out: list[str] = []
    w = out.append
    w("<!DOCTYPE html><html lang=en><head><meta charset=utf-8>")
    w(f"<title>ChewSense chewing-bout experiment {_e(summary['run_id'])}</title>")
    w(f"<meta name=viewport content='width=device-width,initial-scale=1'><style>{CSS}</style></head>")
    w("<body><div class=viz-root>")
    w("<div style='display:flex;justify-content:space-between;align-items:baseline'>")
    w(f"<h1>Chewing-bout detection from AirPods motion · {_e(summary['run_id'])}</h1><button id=theme>Light / dark</button></div>")
    git = summary.get("git") or {}
    w(f"<p>Finished {_e(summary['finished_at'][:19])} UTC · code {_e((git.get('commit') or 'unknown')[:10])}"
      f"{' (uncommitted changes)' if git.get('dirty') else ''} · dataset {_e(summary['dataset_fingerprint'][:12])}"
      f" · window {config['window_s']} s, stride {config['stride_s']} s, label guard ±{config['guard_s']} s</p>")
    w("<div class=banner><b>Experimental, single wearer.</b> Labels are human <i>coarse chewing spans</i>, not "
      "individual chews or bites. All recordings come from one person, so nothing here measures how the model works "
      "for anyone else. No model from this run was registered, promoted, exported or deployed. "
      "Models consume AirPods motion only; video, guided instructions and labels are never inputs.</div>")

    if selected and results[selected].get("test"):
        test = results[selected]["test"]
        dev = results[selected]["dev_oof"]
        w(f"<h2>Headline: {_e(MODEL_LABELS.get(selected, selected))} on the locked test sessions</h2>")
        w(f"<p>Selected by development out-of-fold AUC-PR. Test sessions were never used for fitting, "
          f"threshold choice or model selection. Threshold {results[selected]['threshold']:.2f} was chosen on development predictions. "
          f"Test: {test['sessions']} sessions, {test['positives']:,} chewing and {test['negatives']:,} non-chewing windows "
          f"(windows overlap; the effective sample is the session count, hence the wide intervals).</p>")
        w("<div class=tiles>")
        for label, key, percent in (("F1", "f1", False), ("Recall", "recall", False), ("False-positive rate", "false_positive_rate", True), ("AUC-PR", "auc_pr", False)):
            w(f"<div class=tile><div class=v>{_fmt(test[key], percent=percent)}</div><div class=k>{label} (test)</div>{_ci(test, key, percent)}</div>")
        b = test["bouts"]
        w(f"<div class=tile><div class=v>{b['detected_bouts']}/{b['reference_bouts']}</div><div class=k>chewing bouts detected (test)</div></div>")
        w(f"<div class=tile><div class=v>{b['false_bouts']}</div><div class=k>false bouts in {b['negative_hours'] * 60:.0f} min of non-chewing (test)</div></div>")
        w(f"<div class=tile><div class=v>{_fmt(b['onset_latency_median_s'], 1)} s</div><div class=k>median onset latency (test)</div></div>")
        w(f"<div class=tile><div class=v>{_fmt(dev['f1'])}</div><div class=k>F1 (development out-of-fold)</div>{_ci(dev, 'f1')}</div>")
        w("</div>")

    elif selected and not config.get("evaluate_test", True):
        dev = results[selected]["dev_oof"]
        w(f"<h2>Exploratory run · {_e(MODEL_LABELS.get(selected, selected))}</h2>")
        w("<p>Development cross-validation only; the locked test recordings were not consulted.</p><div class=tiles>")
        for label, key, percent in (("F1", "f1", False), ("Recall", "recall", False), ("False-positive rate", "false_positive_rate", True), ("AUC-PR", "auc_pr", False)):
            w(f"<div class=tile><div class=v>{_fmt(dev[key], percent=percent)}</div><div class=k>{label} (development)</div>{_ci(dev, key, percent)}</div>")
        w("</div>")

    # ------------------------------------------------------------------ data
    w("<h2>Data used</h2>")
    w(f"<p>{counts['sessions']} recordings ({counts['dev_sessions']} development, {counts['test_sessions']} locked test), "
      f"1 participant, {counts['motion_samples']:,} motion samples, {counts['chewing_bouts']} human-marked chewing spans, "
      f"{counts['labelled_windows']:,} labelled windows of which {counts['chewing_windows']:,} chewing.</p>")
    w("<table><tr><th>Label tier</th><th>Labelled windows</th><th class=l>Meaning</th></tr>")
    meanings = {
        "human_coarse": "wearer reviewed the video and marked chewing spans (shared clock)",
        "wearer_no_food_attestation": "wearer attested nothing was eaten (food-free guided take)",
        "guided_instruction": "guided take labelled from its instructions, assuming the wearer followed them",
        "legacy_human_coarse": "2025 in-app spans; clock origin = first motion row (unverified)",
        "human_marked_segment": "span explicitly marked in a 2026 field-test review; unmarked time is unknown",
        "session_declared_negative": "whole recording captured as a declared non-eating scenario",
    }
    for tier, n in sorted(manifest["windows"]["labelled_by_tier"].items(), key=lambda kv: -kv[1]):
        w(f"<tr><td>{_e(tier)}</td><td>{n:,}</td><td class=l>{_e(meanings.get(tier, ''))}</td></tr>")
    w("</table>")
    excluded = manifest["windows"]["excluded"]
    w("<p>Excluded windows: " + ", ".join(f"{_e(k.replace('_', ' '))} {v:,}" for k, v in sorted(excluded.items())) +
      ". Label-boundary windows fall within the guard band of a transition; they are replayed on timelines but never scored.</p>")
    if manifest["rejected"]:
        w("<details><summary>Rejected recordings (" + str(len(manifest["rejected"])) + ")</summary><table><tr><th>Recording</th><th class=l>Reason</th></tr>")
        for item in manifest["rejected"]:
            w(f"<tr><td>{_e(item['session_id'])}</td><td class=l>{_e(item['reason'])}</td></tr>")
        w("</table></details>")

    # ------------------------------------------------------------- compare
    w("<h2>Model comparison</h2>")
    w("<p>Simplest candidates first. If <i>motion energy only</i> matched the richer models, they would be detecting "
      "'moving more' rather than chewing. Bout columns replay every window through Live's EMA + hysteresis detector.</p>")
    header = ("<tr><th>Model</th><th>AUC-PR</th><th>AUC-ROC</th><th>Thr.</th><th>Precision</th><th>Recall</th><th>F1</th>"
              "<th>FPR</th><th>FNR</th><th>Bouts found</th><th>False bouts</th><th>Onset s</th><th>ms/window</th></tr>")
    for split in ("dev_oof", "test"):
        w(f"<h3>{SPLIT_LABELS[split]}</h3><table>{header}")
        for name in results:
            w(_metric_row(name, split, results[name], selected))
        w("</table>")
    rows = []
    for name in results:
        if name == "prior":
            continue
        for split, color in (("dev_oof", "var(--series-1)"), ("test", "var(--series-2)")):
            metrics = results[name].get(split)
            ci = (metrics or {}).get("ci95", {}).get("f1", {})
            rows.append((MODEL_LABELS.get(name, name), "dev" if split == "dev_oof" else "test",
                         (metrics or {}).get("f1"), ci.get("low"), ci.get("high"), color))
    w("<div class=legend><span><i class=key-line style='background:var(--series-1)'></i>development out-of-fold</span>"
      "<span><i class=key-line style='background:var(--series-2)'></i>locked test</span></div>")
    w(_dot_ci_chart(rows, x_label="F1 with 95% session-bootstrap interval"))

    if selected:
        sweep = results[selected]["dev_oof"]["threshold_sweep"]
        xs = [r["threshold"] for r in sweep]
        w(f"<h3>Threshold trade-off · {_e(MODEL_LABELS.get(selected, selected))} · development out-of-fold</h3>")
        w(_line_chart(
            series=[
                ("Precision", "var(--series-1)", xs, [r["precision"] for r in sweep]),
                ("Recall", "var(--series-2)", xs, [r["recall"] for r in sweep]),
                ("F1", "var(--series-3)", xs, [r["f1"] for r in sweep]),
            ],
            x_label="decision threshold", y_label="score", y_range=(0.0, 1.0),
            marker_x=results[selected]["threshold"], marker_label="chosen",
            x_ticks=(0.1, 0.3, 0.5, 0.7, 0.9), x_format="{:.1f}",
        ))
        w("<h3>Confusion matrices (windows)</h3><div style='display:flex;gap:32px;flex-wrap:wrap'>")
        for split in ("dev_oof", "test"):
            m = results[selected].get(split)
            if not m:
                continue
            w(f"<table><tr><th>{SPLIT_LABELS[split]}</th><th>Predicted chewing</th><th>Predicted not</th></tr>"
              f"<tr><td>Labelled chewing</td><td>{m['tp']:,}</td><td>{m['fn']:,}</td></tr>"
              f"<tr><td>Labelled not chewing</td><td>{m['fp']:,}</td><td>{m['tn']:,}</td></tr></table>")
        w("</div>")

        # -------------------------------------------------------- where it fails
        w("<h2>Where the selected model fails</h2>")
        for split in ("dev_oof", "test"):
            m = results[selected].get(split)
            if not m:
                continue
            w(f"<h3>By activity context · {SPLIT_LABELS[split]}</h3>")
            w("<p>Context comes from guided timing, field-test scenario or recording type and is used only for this breakdown.</p>")
            w("<table><tr><th>Context</th><th>Windows</th><th>Chewing</th><th>Recall</th><th>False-positive rate</th></tr>")
            for context, v in sorted(m["by_context"].items(), key=lambda kv: -(kv[1]["false_positive_rate"] or 0)):
                fpr = v["false_positive_rate"]
                flag = " class=warn" if fpr is not None and fpr > 0.10 else ""
                w(f"<tr><td>{_e(context)}</td><td>{v['windows']:,}</td><td>{v['positives']:,}</td><td>{_fmt(v['recall'])}</td>"
                  f"<td{flag}>{_fmt(fpr, percent=True)}</td></tr>")
            w("</table>")
            w(f"<h3>By label tier · {SPLIT_LABELS[split]}</h3><table><tr><th>Tier</th><th>Windows</th><th>Chewing</th><th>Recall</th><th>FPR</th></tr>")
            for tier, v in m["by_tier"].items():
                w(f"<tr><td>{_e(tier)}</td><td>{v['windows']:,}</td><td>{v['positives']:,}</td><td>{_fmt(v['recall'])}</td><td>{_fmt(v['false_positive_rate'], percent=True)}</td></tr>")
            w("</table>")

        recommendations = summary.get("recommendations") or collection_recommendations(summary)
        w("<h2>What to record next</h2>")
        w("<p>Derived from the error breakdown and coverage gaps above; each item names an existing guided experiment "
          "where one exists. These are suggestions from a single-wearer pilot, not statistical requirements.</p><ol>")
        for item in recommendations:
            w(f"<li><b>{_e(item['title'])}</b> — {_e(item['reason'])}"
              + (f" <code>{_e(item['experiment_id'])}</code>" if item.get("experiment_id") else "") + "</li>")
        w("</ol>")

        # --------------------------------------------------------- review queue
        w("<h2>Labels worth a second look</h2>")
        w("<p>Spans where the selected model's out-of-fold or test probability contradicts the human label by ≥0.8. "
          "Either the label or the model is wrong; neither is assumed. Prioritized by duration × disagreement. "
          "Reviewing these is the highest-value manual labeling.</p>")
        w("<table><tr><th>Recording</th><th>Split</th><th>Span (s)</th><th>Human label</th><th>Mean P(chewing)</th><th class=l>Tier · context</th></tr>")
        for span in summary.get("review_queue", [])[:25]:
            w(f"<tr><td><a href='#s-{_e(span['session_id'])}'>{_e(span['session_id'])}</a></td><td>{_e(span['split'])}</td>"
              f"<td>{span['start'] - config['window_s']:.1f}–{span['end']:.1f}</td>"
              f"<td>{'chewing' if span['label'] == 1 else 'not chewing'}</td><td>{_fmt(span['mean_probability'], 2)}</td>"
              f"<td class=l>{_e(span['tier'])} · {_e(span['context'])}</td></tr>")
        w("</table>")

        # ------------------------------------------------------------ sessions
        w("<h2>Every recording</h2>")
        w("<table><tr><th>Recording</th><th>Split</th><th class=l>Context</th><th>Clock</th><th>Labelled</th><th>Chewing</th>"
          "<th>Recall</th><th>FPR</th><th>Bouts found</th><th>False bouts</th></tr>")
        for row in manifest["sessions"]:
            sid = row["session_id"]
            split = "dev_oof" if row["split"] == "dev" else "test"
            m = (results[selected].get(split) or {})
            sm = m.get("by_session", {}).get(sid, {})
            bm = m.get("bouts_by_session", {}).get(sid, {})
            w(f"<tr><td><a href='#s-{_e(sid)}'>{_e(sid)}</a></td><td>{_e(row['split'])}</td><td class=l>{_e(row['activity'])}</td>"
              f"<td>{'shared' if row['clock'] == 'shared_host_clock' else 'unverified'}</td><td>{row['windows_labelled']:,}</td>"
              f"<td>{row['windows_chewing']:,}</td><td>{_fmt(sm.get('recall'))}</td><td>{_fmt(sm.get('false_positive_rate'), percent=True)}</td>"
              f"<td>{bm.get('detected_bouts', '—')}/{bm.get('reference_bouts', '—')}</td><td>{bm.get('false_bouts', '—')}</td></tr>")
        w("</table>")

        w("<h2>Session timelines</h2>")
        w("<p>Choose a recording: <select id=session-select><option value=''>—</option>"
          + "".join(f"<option value='s-{_e(r['session_id'])}'>{_e(r['session_id'])} ({_e(r['split'])})</option>" for r in manifest["sessions"])
          + "</select></p>")
        legend = (f"<span><i class=key-line style='background:var(--series-1)'></i>{_e(MODEL_LABELS.get(selected, selected))} probability</span>")
        if comparison:
            legend += f"<span><i class=key-line style='background:var(--series-2)'></i>{_e(MODEL_LABELS[comparison])} probability</span>"
        legend += ("<span><i class=key-box style='background:var(--series-3)'></i>human chewing span</span>"
                   "<span><i class=key-box style='background:var(--neutral-fill)'></i>human not-chewing span</span>"
                   "<span><i class=key-box style='background:var(--critical)'></i>window error</span>")
        w(f"<div class=legend>{legend}</div>")
        w("<p>Development recordings show out-of-fold predictions (the model never trained on that recording); test recordings show the "
          "final model. Unlabelled time has no label bar. Guided instructions are context only.</p>")
        threshold = results[selected]["threshold"]
        for row in manifest["sessions"]:
            sid = row["session_id"]
            session_rows = frame[frame["session_id"] == sid].sort_values("t_end")
            if not len(session_rows):
                continue
            w(f"<details id='s-{_e(sid)}'><summary>{_e(sid)} · {_e(row['split'])} · {_e(row['activity'])}</summary>")
            if row["notes"]:
                w("<p>" + " · ".join(_e(n) for n in row["notes"]) + "</p>")
            w(_timeline(sessions[sid], session_rows, selected=selected, comparison=comparison, threshold=threshold))
            w("</details>")

    # -------------------------------------------------------- training curves
    tcn_history = summary.get("training_history", {}).get("tcn") or []
    if tcn_history:
        w("<h2>TCN training curves</h2><p>Held-out loss is logged for diagnosis only; training length is fixed and never tuned on it.</p>")
        w("<div style='display:flex;flex-wrap:wrap;gap:12px'>")
        for item in tcn_history:
            history = item["history"]
            if not history:
                continue
            epochs = [h["epoch"] for h in history]
            series = [("train loss", "var(--series-1)", epochs, [h["train_loss"] for h in history])]
            if "heldout_loss" in history[0]:
                series.append(("held-out loss", "var(--series-2)", epochs, [h.get("heldout_loss") for h in history]))
            top = max(max(v for v in s[3] if v is not None) for s in series)
            w(f"<div><h3>fold {_e(item['fold'])}</h3>" + _line_chart(
                series=series, x_label="epoch", y_label="loss", y_range=(0.0, float(top) * 1.05),
                width=340, height=200, x_format="{:.0f}",
                x_ticks=sorted({epochs[0], epochs[len(epochs) // 2], epochs[-1]})) + "</div>")
        w("</div>")

    explanations = summary.get("explanations", {})
    if explanations.get("logreg"):
        w("<h2>What the linear model relies on</h2><p>Standardized logistic-regression coefficients (positive = more chewing-like).</p>")
        w("<table><tr><th>Feature</th><th>Coefficient</th></tr>")
        for item in explanations["logreg"]:
            w(f"<tr><td>{_e(item['feature'])}</td><td>{item['coefficient']:+.2f}</td></tr>")
        w("</table>")

    # ------------------------------------------------------------- history
    runs = index.get("runs", [])
    if runs:
        w("<h2>Run history</h2>")
        consulted = sum(1 for run in runs if run.get("test_evaluated", True))
        w(f"<p>The locked test set has been consulted by {consulted} of {len(runs)} run(s). Choosing between runs by their test score "
          "erodes its independence; compare development scores and treat test as a confirmation.</p>")
        w("<table><tr><th>Run</th><th>Dataset</th><th>Window/guard</th><th>Selected</th><th>Dev F1</th><th>Test F1</th><th class=l>Note</th></tr>")
        for run in runs[-20:]:
            model = run.get("selected_model")
            scores = (run.get("models") or {}).get(model) or {}
            w(f"<tr><td>{_e(run['run_id'])}</td><td>{_e(run['dataset_fingerprint'][:10])}</td>"
              f"<td>{run['config']['window_s']}s/±{run['config']['guard_s']}s</td><td>{_e(model)}</td>"
              f"<td>{_fmt((scores.get('dev_oof') or {}).get('f1'))}</td>"
              f"<td>{_fmt((scores.get('test') or {}).get('f1')) if run.get('test_evaluated', True) else 'not consulted'}</td>"
              f"<td class=l>{_e(run.get('note', ''))}</td></tr>")
        w("</table>")

    # ------------------------------------------------------------- methods
    w("<h2>Method and limits</h2><ul>")
    for line in (
        "Target: whether a causal window lies inside a span the wearer marked as chewing. A chewing span is not a bite and "
        "not a count of chews; cadence and bite boundaries are not evaluated here.",
        f"Windows: {config['window_s']} s of AirPods six-axis motion resampled to 50 Hz, every {config['stride_s']} s. A window is "
        f"labelled only if it and ±{config['guard_s']} s around it sit inside one human span, absorbing clock and marking error. "
        f"Windows with under {config['min_valid_fraction']:.0%} measured samples are excluded.",
        "Splits: whole recordings. The locked test recordings were fixed when the split file was created (newest quarter of each "
        "stratum by capture time; loop evaluation-role recordings always test) and never move. Development uses "
        f"{config['folds']}-fold grouped cross-validation, so every development prediction comes from a model that never saw that recording.",
        "Development scores use a threshold tuned on those same out-of-fold predictions and are therefore slightly optimistic; "
        "test scores use that development threshold unchanged.",
        "Leakage controls: inputs are motion only; features are computed per window; guided instructions, vision proposals, "
        "label provenance and neighbouring windows are never inputs; the same recording never appears in two splits; "
        "duplicate recordings are detected by motion hash.",
        "Uncertainty: 95% intervals resample whole recordings. Windows overlap, so window counts overstate the evidence.",
        "Participants: one. Device, fit and cross-person generalization are unmeasured.",
        "Legacy (2025) and field-test labels have unverified clocks; the guard band covers sub-second error only.",
    ):
        w(f"<li>{_e(line)}</li>")
    w("</ul><h3>Reproduce</h3><p><code>.venv/bin/chewsense-motion eating-experiment</code> from the repository root "
      f"(this run: window {config['window_s']}, stride {config['stride_s']}, guard {config['guard_s']}, seed {config['seed']}).</p>")
    w(f"<script>{SCRIPT}</script></div></body></html>")
    path = run_dir / "report.html"
    path.write_text("".join(out))
    return path
