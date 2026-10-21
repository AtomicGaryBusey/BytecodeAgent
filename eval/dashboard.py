"""Interactive results dashboard (TODO.md, optional item): one self-contained HTML page over the recorded baseline
results, built from the same records and corrections as eval/analytics.py. No model answers are embedded: only
per-cell verdicts, failure classes, the first failure message (trimmed), tokens, time and instruction counts.

Writes reports/dashboard.html (a full document to open locally). With --artifact PATH it also writes the same page
without the document skeleton, for publishing as a claude.ai artifact.
Run from the repo root: .venv/bin/python eval/dashboard.py [--artifact PATH]"""
import argparse
import collections
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
_stdout = sys.stdout
sys.stdout = open(os.devnull, "w")
import analytics as A  # noqa: E402

sys.stdout.close()
sys.stdout = _stdout

GROUPS = [("QBE", [k for k in A.CLASS_ORDER if k.startswith("QBE")]),
          ("Encoding", ["operand not encodable (immediate / addressing)", "invalid operand combination",
                        "invented / malformed instruction", "other assembler / encoder reject"]),
          ("Rules / dialect", ["wrong assembler dialect (NASM)", "program-format rule broken", "label / symbol error"]),
          ("Output format", ["structured: schema violation", "malformed JSON", "prose / markup instead of code"]),
          ("ABI / memory", ["calling-convention violation", "memory fault / crash", "syscall / exit misuse"]),
          ("Logic", ["wrong result / runs forever"]),
          ("Timeout", ["timeout (no complete answer)"]),
          ("Refusal", ["refusal"]),
          ("Other", ["other"])]


def build_data():
    by_fn, by_apps = A.load()
    quality = {}
    if os.path.exists("eval/code_quality.jsonl"):
        per_cell = collections.defaultdict(list)
        for line in open("eval/code_quality.jsonl"):
            r = json.loads(line)
            if r["ok"]:
                per_cell[(r["kind"], r["battery"], r["cell"])].append(r)
        for key, rs in per_cell.items():
            if len(rs) < 3:
                continue
            mi = A.median([r["insns"] for r in rs])
            for r in rs:
                if mi and r["insns"]:
                    quality[(r["model"],) + key] = round(r["insns"] / mi, 4)
    models, tasks, bats, paths, isas, cats, msgs = [], [], [], [], [], [], []
    idx = {}

    def ix(lst, v):
        k = (id(lst), v)
        if k not in idx:
            idx[k] = len(lst)
            lst.append(v)
        return idx[k]

    cls_names = [c for _, cs in GROUPS for c in cs]
    cls_group = [g for g, cs in GROUPS for _ in cs]
    cells = []
    for kind, data in (("fn", by_fn), ("apps", by_apps)):
        for m, cs in data.items():
            mi = ix(models, m)
            for _key, r in cs.items():
                parts = r["cell"].split(".")
                task, path, isa = parts[0], parts[1], parts[2]
                sample = int(parts[3][1:]) if len(parts) > 3 else 0
                cl, msg = -1, -1
                if not r["pass_at_1"]:
                    text = A.first_failure(r)
                    c = A.classify(r["categories"][0] or "", text)
                    cl = cls_names.index(c) if c in cls_names else cls_names.index("other")
                    t = " ".join(text.split())
                    msg = ix(msgs, t[:240] + ("…" if len(t) > 240 else ""))
                atts = A.attempts_of(r)
                toks = [t for t in (A.out_tokens(a) for a in atts) if t is not None]
                wall = sum(a.get("wall_s") or 0 for a in atts)
                q = quality.get((m, kind, r["_bat"], r["cell"]))
                cells.append([mi, ix(bats, r["_bat"]), ix(tasks, task), ix(paths, path), ix(isas, isa), sample,
                              int(bool(r["pass_at_1"])), int(bool(r["pass_final"])), r["attempts"],
                              [ix(cats, x or "pass") for x in r["categories"]], cl, msg,
                              sum(toks), len(toks), round(wall, 1), q])
    full_fn = {m for m, cs in by_fn.items() if len(cs) == A.FULL_FN}
    full_apps = {m for m, cs in by_apps.items() if len(cs) == A.FULL_APPS}
    model_info = [{"id": m, "name": A.name(m), "local": A.is_local(m),
                   "fnPartial": m in by_fn and m not in full_fn,
                   "appsPartial": m in by_apps and m not in full_apps} for m in models]
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    except OSError:
        commit = ""
    return {"models": model_info, "tasks": tasks, "batteries": bats, "paths": paths, "isas": isas, "cats": cats,
            "classes": cls_names, "classGroup": cls_group, "groups": [g for g, _ in GROUPS], "msgs": msgs,
            "cells": cells, "built": time.strftime("%Y-%m-%d"), "commit": commit}


PAGE = r"""<title>Machine-Code Eval Explorer</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;700;800&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
/* Layout: a filter rail on top, then a summary leaderboard, then one panel per question (grid, head-to-head, errors, cost). */
:root {
  --bg: #f5f6f4; --panel: #ffffff; --ink: #1b2422; --muted: #5d6a66; --line: #d9dfdc; --accent: #1d6b62;
  --first: #2f8f5b; --later: #d29a2b; --fail: #c4473a; --none: #e6e9e7; --chip: #e9eeec;
  --display: "Archivo", "Helvetica Neue", Arial, sans-serif;
  --body: "IBM Plex Sans", "Segoe UI", system-ui, sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, "SFMono-Regular", Menlo, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #121816; --panel: #19211f; --ink: #e3ebe8; --muted: #93a39e; --line: #2c3835; --accent: #5cc3b2;
  --first: #4fb57d; --later: #e2b04a; --fail: #e2685a; --none: #26302d; --chip: #23302c; color-scheme: dark } }
:root[data-theme="dark"] {
  --bg: #121816; --panel: #19211f; --ink: #e3ebe8; --muted: #93a39e; --line: #2c3835; --accent: #5cc3b2;
  --first: #4fb57d; --later: #e2b04a; --fail: #e2685a; --none: #26302d; --chip: #23302c; color-scheme: dark }
* { box-sizing: border-box }
body { background: var(--bg); color: var(--ink); font: 14px/1.5 var(--body); margin: 0; }
.wrap { max-width: 1280px; margin: 0 auto; padding-inline: 16px; padding-block: 20px 48px; display: grid; gap: 18px; }
header h1 { font: 800 clamp(22px, 3vw, 30px)/1.15 var(--display); margin: 0; letter-spacing: -0.01em; text-wrap: balance }
header p { margin: 4px 0 0; color: var(--muted); max-width: 72ch }
.meta { font: 12px var(--mono); color: var(--muted) }
section.panel { background: var(--panel); border: 1px solid var(--line); border-radius: 6px; padding: 16px; min-width: 0 }
section.panel h2 { font: 700 16px/1.3 var(--display); margin: 0 0 4px; letter-spacing: 0.01em }
section.panel .lede { color: var(--muted); margin: 0 0 12px; max-width: 80ch }
.filters { display: grid; gap: 10px }
.frow { display: flex; flex-wrap: wrap; gap: 6px; align-items: center }
.frow .lab { font: 600 11px var(--body); text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); width: 74px }
.chip { font: 500 12px var(--body); border: 1px solid var(--line); background: var(--chip); color: var(--ink);
  padding: 3px 10px; border-radius: 999px; cursor: pointer }
.chip[aria-pressed="false"] { background: transparent; color: var(--muted); text-decoration: line-through }
.chip:focus-visible, button:focus-visible, select:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px }
.linkbtn { background: none; border: 0; color: var(--accent); font: 500 12px var(--body); cursor: pointer; padding: 2px 4px }
.scroll { overflow-x: auto }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line); white-space: nowrap }
th { font: 600 11px var(--body); text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); cursor: pointer; user-select: none }
th[aria-sort] { color: var(--ink) }
td.num { text-align: right; font-family: var(--mono); font-size: 12.5px }
td.name { font-weight: 500 }
.tag { font: 500 10.5px var(--body); color: var(--muted); border: 1px solid var(--line); border-radius: 4px; padding: 0 4px; margin-left: 6px }
.bar { position: relative; height: 10px; width: 140px; background: var(--none); border-radius: 2px }
.bar i { position: absolute; top: 0; bottom: 0; background: var(--accent); opacity: 0.25 }
.bar b { position: absolute; top: -2px; bottom: -2px; width: 2px; background: var(--accent) }
.legend { display: flex; flex-wrap: wrap; gap: 14px; font-size: 12px; color: var(--muted); margin-bottom: 8px }
.legend span::before { content: ""; display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; vertical-align: -1px; background: var(--c) }
#grid { display: block; max-width: none }
.gridwrap { overflow: auto; max-height: 560px; border: 1px solid var(--line); border-radius: 4px }
.detail { margin-top: 10px; font-size: 13px; min-height: 40px }
.detail code, .msg { font: 12px/1.5 var(--mono); }
.msg { white-space: pre-wrap; background: var(--chip); padding: 8px; border-radius: 4px; margin-top: 6px; max-width: 100%; overflow-wrap: anywhere }
.h2h { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-bottom: 10px }
select { font: 13px var(--body); padding: 4px 6px; border: 1px solid var(--line); border-radius: 4px; background: var(--panel); color: var(--ink); max-width: 100% }
.stat { display: flex; flex-wrap: wrap; gap: 22px; margin: 6px 0 10px }
.stat div { display: grid } .stat b { font: 700 20px var(--display) } .stat span { font-size: 12px; color: var(--muted) }
.two { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 18px }
svg text { fill: var(--muted); font: 11px var(--body) }
.cats { display: inline-flex; gap: 3px; vertical-align: middle }
.cats i { width: 9px; height: 9px; border-radius: 2px; display: inline-block; background: var(--c) }
footer { color: var(--muted); font-size: 12px; max-width: 90ch }
@media (prefers-reduced-motion: reduce) { * { transition: none !important } }
</style>
<div class="wrap">
<header>
  <h1>Machine-Code Eval Explorer</h1>
  <p>Every recorded baseline cell of the BytecodeAgent live evaluation: language models writing x86-64 and AArch64 machine code with no tools. Filter the cells, then compare models, open any cell's attempt history, and see where the failures come from.</p>
  <div class="meta" id="meta"></div>
</header>

<section class="panel filters" aria-label="Filters">
  <div class="frow"><span class="lab">Battery</span><span id="f-bat"></span></div>
  <div class="frow"><span class="lab">Path</span><span id="f-path"></span></div>
  <div class="frow"><span class="lab">ISA</span><span id="f-isa"></span></div>
  <div class="frow"><span class="lab">Models</span><span id="f-group"></span><button class="linkbtn" id="m-all">all</button><button class="linkbtn" id="m-none">none</button></div>
  <div class="frow" id="f-models"></div>
</section>

<section class="panel" id="lb">
  <h2>Leaderboard for the selected cells</h2>
  <p class="lede">First-attempt and within-four pass rates on the cells that match the filters, with a 95% Wilson interval. Models with partial runs (†) are scored on the cells they have. Click a column to sort.</p>
  <div class="scroll"><table id="lbt"></table></div>
</section>

<section class="panel">
  <h2>Cell grid</h2>
  <p class="lede">One row per cell, one column per model. Click a square to open that model's attempts on that cell.</p>
  <div class="legend"><span style="--c:var(--first)">passed first try</span><span style="--c:var(--later)">passed on attempt 2-4</span><span style="--c:var(--fail)">failed all attempts</span><span style="--c:var(--none)">not run</span></div>
  <div class="gridwrap"><canvas id="grid" tabindex="0" aria-label="Cell grid"></canvas></div>
  <div class="detail" id="detail" aria-live="polite">Select a square to see its attempts.</div>
</section>

<section class="panel">
  <h2>Head to head</h2>
  <p class="lede">Two models on the cells both ran: where they disagree on the first attempt, and an exact McNemar test of whether the difference is more than chance.</p>
  <div class="h2h"><select id="hA" aria-label="Model A"></select><span>vs</span><select id="hB" aria-label="Model B"></select></div>
  <div id="h2hout"></div>
</section>

<div class="two">
  <section class="panel">
    <h2>Where first attempts fail</h2>
    <p class="lede">First-attempt failures on the selected cells, by error group, as a share of each model's cells.</p>
    <div class="scroll"><svg id="errs" role="img" aria-label="Error groups per model"></svg></div>
    <div class="legend" id="errlegend"></div>
  </section>
  <section class="panel">
    <h2>Accuracy against output tokens</h2>
    <p class="lede">Median output tokens per attempt (log scale) against the first-attempt pass rate. Tokenisers differ between vendors, so compare positions loosely.</p>
    <svg id="scatter" role="img" aria-label="Pass rate against output tokens"></svg>
  </section>
</div>

<footer>Built from the committed run data by <code>eval/dashboard.py</code>, with the reviewed false-reject corrections applied. Executed-instruction ratios come from re-running each passing solution (<code>eval/code_quality.py</code>). The full analysis is in <code>reports/live-model-eval-analytics.md</code>.</footer>
</div>

<script id="data" type="application/json">__DATA__</script>
<script>
(function () {
"use strict";
const D = JSON.parse(document.getElementById("data").textContent);
const C = D.cells.map(r => ({m: r[0], b: r[1], t: r[2], p: r[3], i: r[4], s: r[5], p1: r[6], pf: r[7], at: r[8],
  cats: r[9], cl: r[10], msg: r[11], tok: r[12], ntok: r[13], wall: r[14], q: r[15]}));
const PATHN = {stream: "Stream", structured: "Structured", ir: "IR (QBE)", asm: "asm"};
const ISAN = {x86_64: "x86-64", aarch64: "AArch64"};
const BATN = {round1: "Round 1", round2: "Round 2", long: "Long", adversarial: "Adversarial", apps: "Apps"};
const store = {get(k) { try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* storage unavailable */ } }};
const sel = {
  b: new Set(D.batteries.map((_, k) => k)), p: new Set(D.paths.map((_, k) => k)),
  i: new Set(D.isas.map((_, k) => k)), m: new Set(D.models.map((_, k) => k))
};
const saved = store.get("mce-filters");
if (saved) for (const k of Object.keys(sel)) if (Array.isArray(saved[k])) sel[k] = new Set(saved[k]);
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
document.getElementById("meta").textContent =
  `${C.length.toLocaleString()} cells · ${D.models.length} models · built ${D.built}` + (D.commit ? ` from ${D.commit}` : "");

function chips(host, names, key, label) {
  const el = document.getElementById(host); el.innerHTML = "";
  names.forEach((n, k) => {
    const b = document.createElement("button"); b.className = "chip"; b.textContent = label ? label(n, k) : n;
    b.setAttribute("aria-pressed", sel[key].has(k)); b.id = `chip-${key}-${k}`;
    b.onclick = () => { sel[key].has(k) ? sel[key].delete(k) : sel[key].add(k); b.setAttribute("aria-pressed", sel[key].has(k)); render(); };
    el.appendChild(b);
  });
}
const order = D.models.map((_, k) => k).sort((a, b) => (D.models[a].local - D.models[b].local) || D.models[a].name.localeCompare(D.models[b].name));
function modelChips() {
  const el = document.getElementById("f-models"); el.innerHTML = "";
  for (const k of order) {
    const m = D.models[k]; const b = document.createElement("button"); b.className = "chip";
    b.textContent = m.name.replace(" (local)", "") + (m.local ? " ·local" : ""); b.id = `chip-m-${k}`;
    b.setAttribute("aria-pressed", sel.m.has(k));
    b.onclick = () => { sel.m.has(k) ? sel.m.delete(k) : sel.m.add(k); b.setAttribute("aria-pressed", sel.m.has(k)); render(); };
    el.appendChild(b);
  }
}
chips("f-bat", D.batteries, "b", n => BATN[n] || n);
chips("f-path", D.paths, "p", n => PATHN[n] || n);
chips("f-isa", D.isas, "i", n => ISAN[n] || n);
const grp = document.getElementById("f-group");
[["Vendor only", m => !m.local], ["Local only", m => m.local]].forEach(([lab, f]) => {
  const b = document.createElement("button"); b.className = "linkbtn"; b.textContent = lab;
  b.onclick = () => { sel.m = new Set(D.models.map((m, k) => f(m) ? k : -1).filter(k => k >= 0)); modelChips(); render(); };
  grp.appendChild(b);
});
document.getElementById("m-all").onclick = () => { sel.m = new Set(D.models.map((_, k) => k)); modelChips(); render(); };
document.getElementById("m-none").onclick = () => { sel.m = new Set(); modelChips(); render(); };
modelChips();

const inFilter = c => sel.b.has(c.b) && sel.p.has(c.p) && sel.i.has(c.i);
function wilson(k, n) {
  if (!n) return [0, 0]; const z = 1.959964, p = k / n, d = 1 + z * z / n;
  const c = (p + z * z / (2 * n)) / d, h = z * Math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d;
  return [Math.max(0, c - h), Math.min(1, c + h)];
}
function lnC(n, k) { let s = 0; for (let j = 1; j <= k; j++) s += Math.log(n - k + j) - Math.log(j); return s; }
function mcnemar(b, c) {
  const n = b + c; if (!n) return 1; const k = Math.min(b, c); let t = 0;
  for (let j = 0; j <= k; j++) t += Math.exp(lnC(n, j) - n * Math.LN2);
  return Math.min(1, 2 * t);
}
const median = xs => { if (!xs.length) return null; const s = [...xs].sort((a, b) => a - b), h = s.length >> 1; return s.length % 2 ? s[h] : (s[h - 1] + s[h]) / 2; };
const geo = xs => xs.length ? Math.exp(xs.reduce((a, x) => a + Math.log(x), 0) / xs.length) : null;
const pct = x => (100 * x).toFixed(0) + "%";
const fmtP = p => p < 0.001 ? "<0.001" : p.toFixed(3);

function stats() {
  const out = new Map();
  for (const c of C) {
    if (!inFilter(c) || !sel.m.has(c.m)) continue;
    let s = out.get(c.m); if (!s) out.set(c.m, s = {n: 0, p1: 0, pf: 0, at: 0, tok: [], q: []});
    s.n++; s.p1 += c.p1; s.pf += c.pf; s.at += c.at;
    if (c.ntok) s.tok.push(c.tok / c.ntok); if (c.q) s.q.push(c.q);
  }
  return out;
}

let sortKey = store.get("mce-sort") || "p1";
function renderLB(st) {
  const cols = [["name", "Model"], ["n", "Cells"], ["p1", "First attempt"], ["ci", "95% interval"], ["pf", "Within 4"],
    ["at", "Mean attempts"], ["tok", "Tokens / attempt"], ["q", "Instr. ratio"]];
  const rows = [...st.entries()].map(([m, s]) => ({m, name: D.models[m].name, n: s.n, p1: s.p1 / s.n, k: s.p1, pf: s.pf / s.n,
    at: s.at / s.n, tok: median(s.tok), q: geo(s.q), ci: wilson(s.p1, s.n)}));
  const dir = sortKey === "name" ? 1 : -1;
  rows.sort((a, b) => {
    const va = sortKey === "ci" ? a.ci[0] : a[sortKey], vb = sortKey === "ci" ? b.ci[0] : b[sortKey];
    if (sortKey === "name") return a.name.localeCompare(b.name);
    if (sortKey === "at" || sortKey === "tok" || sortKey === "q") return (va ?? 1e18) - (vb ?? 1e18);
    return dir * ((va ?? -1) - (vb ?? -1));
  });
  const t = document.getElementById("lbt");
  t.innerHTML = "<thead><tr>" + cols.map(([k, l]) => `<th data-k="${k}"${k === sortKey ? ' aria-sort="descending"' : ""}>${l}</th>`).join("") + "</tr></thead>";
  const tb = document.createElement("tbody");
  for (const r of rows) {
    const mi = D.models[r.m]; const partial = sel.b.has(D.batteries.indexOf("apps")) ? mi.appsPartial || mi.fnPartial : mi.fnPartial;
    const tr = document.createElement("tr");
    tr.innerHTML = `<td class="name">${mi.name}${partial ? '<span class="tag">† partial</span>' : ""}</td>
      <td class="num">${r.n}</td><td class="num">${r.k}/${r.n} (${pct(r.p1)})</td>
      <td><div class="bar" title="${pct(r.ci[0])}–${pct(r.ci[1])}"><i style="left:${r.ci[0] * 100}%;right:${100 - r.ci[1] * 100}%"></i><b style="left:calc(${r.p1 * 100}% - 1px)"></b></div></td>
      <td class="num">${pct(r.pf)}</td><td class="num">${r.at.toFixed(2)}</td>
      <td class="num">${r.tok == null ? "—" : Math.round(r.tok).toLocaleString()}</td>
      <td class="num">${r.q == null ? "—" : r.q.toFixed(2)}</td>`;
    tb.appendChild(tr);
  }
  t.appendChild(tb);
  if (!rows.length) t.innerHTML += '<tbody><tr><td colspan="8">No cells match. Turn a battery, path, ISA or model back on.</td></tr></tbody>';
  t.querySelectorAll("th").forEach(th => th.onclick = () => { sortKey = th.dataset.k; store.set("mce-sort", sortKey); render(); });
}

// ---- grid
const cv = document.getElementById("grid"), ctx = cv.getContext("2d");
let G = {rows: [], cols: [], map: new Map(), cw: 14, ch: 12, left: 0, top: 0};
function buildGrid(st) {
  const cols = order.filter(m => st.has(m));
  const rowsMap = new Map();
  for (const c of C) {
    if (!inFilter(c) || !sel.m.has(c.m)) continue;
    const key = `${c.b}|${c.t}|${c.p}|${c.i}|${c.s}`;
    if (!rowsMap.has(key)) rowsMap.set(key, {b: c.b, t: c.t, p: c.p, i: c.i, s: c.s, by: new Map()});
    rowsMap.get(key).by.set(c.m, c);
  }
  const rows = [...rowsMap.values()];
  rows.sort((a, b) => D.batteries[a.b].localeCompare(D.batteries[b.b]) || a.b - b.b || D.tasks[a.t].localeCompare(D.tasks[b.t]) || a.p - b.p || a.i - b.i || a.s - b.s);
  // hardest first within the order? keep the battery/task order: it reads like the reports
  G.rows = rows; G.cols = cols;
  const dpr = window.devicePixelRatio || 1;
  G.left = 250; G.top = 150;
  const W = G.left + cols.length * G.cw + 8, H = G.top + rows.length * G.ch + 8;
  cv.width = W * dpr; cv.height = H * dpr; cv.style.width = W + "px"; cv.style.height = H + "px";
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  ctx.font = `11px ${css("--body")}`;
  ctx.fillStyle = css("--muted");
  cols.forEach((m, j) => {
    ctx.save(); ctx.translate(G.left + j * G.cw + G.cw / 2 + 3, G.top - 6); ctx.rotate(-Math.PI / 3);
    ctx.fillText(D.models[m].name.slice(0, 30), 0, 0); ctx.restore();
  });
  const colr = {first: css("--first"), later: css("--later"), fail: css("--fail"), none: css("--none")};
  let lastB = -1;
  rows.forEach((r, k) => {
    const y = G.top + k * G.ch;
    ctx.fillStyle = css("--muted");
    if (r.b !== lastB) { ctx.fillStyle = css("--ink"); ctx.fillText(BATN[D.batteries[r.b]] || D.batteries[r.b], 4, y + 9); lastB = r.b; ctx.fillStyle = css("--muted"); }
    ctx.fillText(`${D.tasks[r.t]}.${D.paths[r.p]}.${D.isas[r.i]}${r.s ? ".s" + r.s : ""}`.slice(0, 34), 84, y + 9);
    cols.forEach((m, j) => {
      const c = r.by.get(m);
      ctx.fillStyle = !c ? colr.none : c.p1 ? colr.first : c.pf ? colr.later : colr.fail;
      ctx.fillRect(G.left + j * G.cw + 1, y + 1, G.cw - 2, G.ch - 2);
    });
  });
}
cv.addEventListener("click", ev => {
  const r = cv.getBoundingClientRect(), x = ev.clientX - r.left, y = ev.clientY - r.top;
  const j = Math.floor((x - G.left) / G.cw), k = Math.floor((y - G.top) / G.ch);
  if (j < 0 || k < 0 || j >= G.cols.length || k >= G.rows.length) return;
  showCell(G.rows[k], G.cols[j]);
});
const catColor = c => c === "pass" ? css("--first") : css("--fail");
function showCell(row, m) {
  const c = row.by.get(m), el = document.getElementById("detail");
  const name = `${D.tasks[row.t]}.${D.paths[row.p]}.${D.isas[row.i]}${row.s ? ".s" + row.s : ""}`;
  if (!c) { el.innerHTML = `<b>${D.models[m].name}</b> did not run <code>${name}</code>.`; return; }
  const seq = c.cats.map(k => `<i title="${D.cats[k]}" style="--c:${catColor(D.cats[k])}"></i>`).join("");
  const verdict = c.p1 ? "passed on the first attempt" : c.pf ? `passed on attempt ${c.at}` : `failed all ${c.at} attempts`;
  el.innerHTML = `<b>${D.models[m].name}</b> · <code>${name}</code> (${BATN[D.batteries[row.b]]}): ${verdict}.
    <div>Attempts: <span class="cats">${seq}</span> ${c.cats.map(k => D.cats[k]).join(" → ")}</div>
    <div class="meta">${c.ntok ? Math.round(c.tok).toLocaleString() + " output tokens over " + c.ntok + " attempt(s) · " : ""}${c.wall.toFixed(0)} s model time${c.q ? " · executed-instruction ratio " + c.q.toFixed(2) + " of the cell median" : ""}</div>
    ${c.cl >= 0 ? `<div>First failure: ${D.classes[c.cl]}</div><div class="msg">${esc(D.msgs[c.msg] || "")}</div>` : ""}`;
}
const esc = s => s.replace(/[&<>]/g, ch => ({"&": "&amp;", "<": "&lt;", ">": "&gt;"}[ch]));

// ---- head to head
const hA = document.getElementById("hA"), hB = document.getElementById("hB");
for (const k of order) { for (const s of [hA, hB]) { const o = document.createElement("option"); o.value = k; o.textContent = D.models[k].name; s.appendChild(o); } }
const byName = n => D.models.findIndex(m => m.name === n);
hA.value = store.get("mce-hA") ?? Math.max(0, byName("Codex GPT-6 Astra"));
hB.value = store.get("mce-hB") ?? Math.max(0, byName("Claude Opus 5.5"));
hA.onchange = hB.onchange = () => { store.set("mce-hA", hA.value); store.set("mce-hB", hB.value); renderH2H(); };
function renderH2H() {
  const a = +hA.value, b = +hB.value, ca = new Map(), cb = new Map();
  for (const c of C) { if (!inFilter(c)) continue; const key = `${c.b}|${c.t}|${c.p}|${c.i}|${c.s}`; if (c.m === a) ca.set(key, c); if (c.m === b) cb.set(key, c); }
  const shared = [...ca.keys()].filter(k => cb.has(k));
  const aOnly = shared.filter(k => ca.get(k).p1 && !cb.get(k).p1), bOnly = shared.filter(k => cb.get(k).p1 && !ca.get(k).p1);
  const p = mcnemar(aOnly.length, bOnly.length);
  const na = D.models[a].name, nb = D.models[b].name;
  const pa = shared.filter(k => ca.get(k).p1).length, pb = shared.filter(k => cb.get(k).p1).length;
  const lbl = k => { const c = ca.get(k); return `${D.tasks[c.t]}.${D.paths[c.p]}.${D.isas[c.i]}${c.s ? ".s" + c.s : ""}`; };
  const list = (ks, who) => ks.length ? ks.map(k => `<code>${lbl(k)}</code>`).join(", ") : `<span class="meta">none</span>`;
  document.getElementById("h2hout").innerHTML = shared.length ? `
    <div class="stat"><div><b>${shared.length}</b><span>shared cells</span></div>
    <div><b>${pa} vs ${pb}</b><span>first-attempt passes</span></div>
    <div><b>${aOnly.length} / ${bOnly.length}</b><span>only ${esc(na)} / only ${esc(nb)}</span></div>
    <div><b>${fmtP(p)}</b><span>McNemar p ${p < 0.05 ? "(a real difference)" : "(not distinguishable)"}</span></div></div>
    <div>Only ${esc(na)} passed first try: ${list(aOnly)}</div><div style="margin-top:6px">Only ${esc(nb)} passed first try: ${list(bOnly)}</div>`
    : `<p class="meta">These two models share no cells under the current filters.</p>`;
}

// ---- error groups (stacked horizontal bars)
const GCOL = ["#6b5bd2", "#c4473a", "#d29a2b", "#2f7fb5", "#8a5a3c", "#2f8f5b", "#7d8a86", "#b04f8f", "#a3a3a3"];
function renderErrs(st) {
  const svg = document.getElementById("errs");
  const ms = [...st.keys()].sort((x, y) => st.get(y).p1 / st.get(y).n - st.get(x).p1 / st.get(x).n);
  const count = new Map(ms.map(m => [m, new Array(D.groups.length).fill(0)]));
  for (const c of C) {
    if (!inFilter(c) || !count.has(c.m) || c.p1 || c.cl < 0) continue;
    count.get(c.m)[D.groups.indexOf(D.classGroup[c.cl])]++;
  }
  const L = 190, W = 560, rh = 16, H = ms.length * rh + 24;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.style.width = "100%"; svg.style.minWidth = "480px";
  let out = "";
  [0, 0.25, 0.5].forEach(v => { const x = L + v * (W - L - 10) / 0.75 * 1;
    out += `<line x1="${L + v / 0.75 * (W - L - 10)}" x2="${L + v / 0.75 * (W - L - 10)}" y1="0" y2="${H - 18}" stroke="${css("--line")}"/>`;
    out += `<text x="${L + v / 0.75 * (W - L - 10)}" y="${H - 4}" text-anchor="middle">${pct(v)}</text>`; });
  ms.forEach((m, k) => {
    const y = k * rh + 2, n = st.get(m).n; let x = L;
    out += `<text x="${L - 6}" y="${y + 11}" text-anchor="end">${esc(D.models[m].name.slice(0, 32))}</text>`;
    count.get(m).forEach((v, g) => { if (!v) return; const w = v / n / 0.75 * (W - L - 10);
      out += `<rect x="${x}" y="${y}" width="${Math.max(0, w)}" height="${rh - 4}" fill="${GCOL[g]}"><title>${esc(D.models[m].name)}: ${D.groups[g]} ${v} of ${n} cells</title></rect>`; x += w; });
  });
  svg.innerHTML = out;
  document.getElementById("errlegend").innerHTML = D.groups.map((g, k) => `<span style="--c:${GCOL[k]}">${g}</span>`).join("");
}

// ---- scatter
function renderScatter(st) {
  const svg = document.getElementById("scatter"), W = 520, H = 340, L = 44, B = 34, T = 10, R = 12;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.style.width = "100%";
  const pts = [...st.entries()].map(([m, s]) => ({m, x: median(s.tok), y: s.p1 / s.n})).filter(p => p.x);
  let out = "";
  const lx = v => L + (Math.log10(v) - 2) / 3 * (W - L - R), ly = v => T + (1 - v) * (H - T - B);
  [100, 1000, 10000, 100000].forEach(v => { out += `<line x1="${lx(v)}" x2="${lx(v)}" y1="${T}" y2="${H - B}" stroke="${css("--line")}"/><text x="${lx(v)}" y="${H - B + 14}" text-anchor="middle">${v >= 1000 ? v / 1000 + "k" : v}</text>`; });
  [0, 0.25, 0.5, 0.75, 1].forEach(v => { out += `<line x1="${L}" x2="${W - R}" y1="${ly(v)}" y2="${ly(v)}" stroke="${css("--line")}"/><text x="${L - 6}" y="${ly(v) + 4}" text-anchor="end">${pct(v)}</text>`; });
  out += `<text x="${(W + L) / 2}" y="${H - 4}" text-anchor="middle">median output tokens per attempt</text>`;
  for (const p of pts) {
    const x = lx(Math.min(Math.max(p.x, 100), 100000)), y = ly(p.y), loc = D.models[p.m].local;
    out += `<circle cx="${x}" cy="${y}" r="5" fill="${loc ? css("--later") : css("--accent")}" fill-opacity="0.8"><title>${esc(D.models[p.m].name)}: ${pct(p.y)} first try, ${Math.round(p.x).toLocaleString()} tokens per attempt</title></circle>`;
  }
  svg.innerHTML = out;
}

function render() {
  store.set("mce-filters", Object.fromEntries(Object.entries(sel).map(([k, v]) => [k, [...v]])));
  const st = stats();
  renderLB(st); buildGrid(st); renderH2H(); renderErrs(st); renderScatter(st);
}
render();
const mq = window.matchMedia("(prefers-color-scheme: dark)");
if (mq.addEventListener) mq.addEventListener("change", render);
new MutationObserver(render).observe(document.documentElement, {attributes: true, attributeFilter: ["data-theme"]});
})();
</script>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact", help="also write the page without the document skeleton (for a claude.ai artifact)")
    args = ap.parse_args()
    data = json.dumps(build_data(), separators=(",", ":")).replace("</", "<\\/")
    body = PAGE.replace("__DATA__", data)
    with open("reports/dashboard.html", "w") as fh:
        fh.write('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
                 '<meta name="viewport" content="width=device-width, initial-scale=1">\n</head>\n<body>\n'
                 + body + "\n</body>\n</html>\n")
    if args.artifact:
        with open(args.artifact, "w") as fh:
            fh.write(body)
    print(f"reports/dashboard.html: {os.path.getsize('reports/dashboard.html') / 1e6:.1f} MB", file=sys.stderr)


if __name__ == "__main__":
    main()
