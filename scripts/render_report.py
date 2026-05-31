"""Render the benchmark HTML report.

Reads scored.csv, summary.json, run trace.jsonl files, and bank_list.json. Produces a
single self-contained HTML file at results/report.html using the house style
(Overused Grotesk + IBM Plex Mono, purple/blue/pink/orange palette).
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from scripts.datasets import DEFAULT_SLUG, all_slugs, get_dataset


REPO_ROOT = Path(__file__).resolve().parent.parent


def sanitize_paths(text: str) -> str:
    """Strip local absolute paths from embedded trace text so reports stay portable.

    Replaces the repo root with a relative path and any other /Users/<name>/ or
    /home/<name>/ home-directory prefix with ~/, so generated HTML never leaks the
    machine it was rendered on.
    """
    text = text.replace(str(REPO_ROOT) + "/", "").replace(str(REPO_ROOT), ".")
    text = re.sub(r"/(?:Users|home)/[^/\s]+/", "~/", text)
    return text


STATUS_COLOR = {
    "correct": "var(--success)",
    "wrong": "var(--danger)",
    "missing": "var(--missing)",
    "format_error": "var(--danger)",
    "na": "var(--neutral)",
}


def fmt_money(x: float | None) -> str:
    if x is None:
        return "—"
    return f"${x:,.2f}"


def fmt_pct(x: float | None) -> str:
    if x is None:
        return "—"
    return f"{x * 100:.1f}%"


def fmt_seconds(ms: float | None) -> str:
    if ms is None:
        return "—"
    return f"{ms / 1000:.1f}s"


def signed(delta: float, fmt: str) -> str:
    if delta is None:
        return "—"
    sign = "+" if delta > 0 else ""
    if fmt == "pct":
        return f"{sign}{delta * 100:.1f}pp"
    if fmt == "money":
        return f"{sign}${delta:,.2f}"
    if fmt == "seconds":
        return f"{sign}{delta / 1000:.1f}s"
    return f"{sign}{delta}"


def load_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open() as f:
        return list(csv.DictReader(f))


def load_trace_excerpts(run_dir: Path, n: int = 3) -> list[dict[str, Any]]:
    """Return first n + last n tool_use events from a trace.jsonl."""
    trace = run_dir / "trace.jsonl"
    if not trace.exists():
        return []
    events: list[dict[str, Any]] = []
    with trace.open() as f:
        for line in f:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") != "assistant":
                continue
            msg = ev.get("message") or {}
            for block in msg.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    events.append({"name": block.get("name"), "input": block.get("input")})
    if len(events) <= 2 * n:
        return events
    return events[:n] + [{"name": "...", "input": f"({len(events) - 2*n} more)"}] + events[-n:]


def render_excerpt_block(events: list[dict[str, Any]]) -> str:
    if not events:
        return "<em>No tool_use events recorded.</em>"
    rows = []
    for ev in events:
        inp = ev.get("input")
        if isinstance(inp, dict):
            inp_str = ", ".join(f"{k}={str(v)[:80]}" for k, v in list(inp.items())[:3])
        else:
            inp_str = str(inp)
        rows.append(
            f"<div class='tool-event'><code class='tool-name'>{html.escape(str(ev.get('name')))}</code>"
            f"<span class='tool-input'>{html.escape(sanitize_paths(inp_str)[:160])}</span></div>"
        )
    return "\n".join(rows)


def per_filing_heatmap(rows: list[dict[str, Any]], schema_fields: list[str]) -> str:
    """Render a heatmap: rows=fields, cols=condition."""
    by_field = defaultdict(dict)
    for r in rows:
        by_field[r["field"]][r["condition"]] = r["status"]
    out = ["<table class='heatmap'>"]
    out.append("<thead><tr><th>field</th><th>with skill</th><th>no skill</th></tr></thead>")
    out.append("<tbody>")
    for f in schema_fields:
        ws = by_field.get(f, {}).get("with_skill", "na")
        ns = by_field.get(f, {}).get("no_skill", "na")
        out.append(
            f"<tr><td class='field-name'>{html.escape(f)}</td>"
            f"<td class='cell cell-{ws}' title='{ws}'></td>"
            f"<td class='cell cell-{ns}' title='{ns}'></td></tr>"
        )
    out.append("</tbody></table>")
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=all_slugs(), default=DEFAULT_SLUG)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--summary", type=Path, default=None)
    parser.add_argument("--summary-v1", type=Path, default=None,
                        help="v1 baseline summary for the comparison row.")
    parser.add_argument("--invocation-stats", type=Path, default=None,
                        help="Per-version invocation/CLI/cost stats.")
    parser.add_argument("--scored-csv", type=Path, default=None)
    parser.add_argument("--summary-batch", type=Path, default=None,
                        help="Batch-mode summary; if absent, mode-comparison panel is skipped.")
    parser.add_argument("--runs-dir", type=Path, default=None)
    parser.add_argument("--runs-batch-dir", type=Path, default=None)
    parser.add_argument("--results-dir", type=Path, default=None,
                        help="Override results dir for BOTH reading summaries/stats and "
                             "writing report.html (e.g. results/<slug>_rerun_2026-05-30).")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    ds = get_dataset(args.dataset)
    rdir = args.results_dir or ds.results_dir()
    manifest_path = args.manifest or ds.manifest_path()
    summary_path = args.summary or (rdir / "summary.json")
    summary_v1_path = args.summary_v1 or (rdir / "summary-baseline-v1.json")
    invocation_path = args.invocation_stats or (rdir / "invocation_stats.json")
    scored_csv_path = args.scored_csv or (rdir / "scored.csv")
    summary_batch_path = args.summary_batch or (rdir / "summary_batch.json")
    runs_dir = args.runs_dir or ds.runs_dir()
    runs_batch_dir = args.runs_batch_dir or ds.runs_batch_dir()
    output_path = args.output or (rdir / "report.html")

    banks = json.loads(manifest_path.read_text())
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    summary_v1 = json.loads(summary_v1_path.read_text()) if summary_v1_path.exists() else {}
    summary_batch = json.loads(summary_batch_path.read_text()) if summary_batch_path.exists() else {}
    invocation_stats = json.loads(invocation_path.read_text()) if invocation_path.exists() else {}
    latency_cw_path = rdir / "latency_cold_warm.json"
    latency_cw = json.loads(latency_cw_path.read_text()) if latency_cw_path.exists() else {}
    # Optional per-cache-state scored summaries (from run_latency_cold_warm.py). When
    # present, the aggregate table splits with_skill into (cold) and (warm) rows.
    sb_cold_path = rdir / "summary_batch_cold.json"
    sb_warm_path = rdir / "summary_batch_warm.json"
    summary_cold = json.loads(sb_cold_path.read_text()) if sb_cold_path.exists() else {}
    summary_warm = json.loads(sb_warm_path.read_text()) if sb_warm_path.exists() else {}

    # Per-file scoring rows live in scored.csv; batch-only datasets only have
    # scored_batch.csv. Fall back so the per-document heatmaps still render.
    rows = load_csv(scored_csv_path)
    is_batch_only = not rows
    if is_batch_only:
        rows = load_csv(rdir / "scored_batch.csv")
    # If we have no per-file summary, treat the batch summary as the primary one
    # for the aggregate table (new datasets are batch-only).
    if not summary and summary_batch:
        summary = summary_batch
    schema_fields = []
    seen = set()
    for r in rows:
        if r["field"] not in seen:
            schema_fields.append(r["field"])
            seen.add(r["field"])

    with_s = summary.get("with_skill", {}) or {}
    no_s = summary.get("no_skill", {}) or {}
    acc_delta = (
        with_s.get("accuracy") - no_s.get("accuracy")
        if with_s.get("accuracy") is not None and no_s.get("accuracy") is not None
        else None
    )
    cost_delta = with_s.get("total_cost_usd", 0) - no_s.get("total_cost_usd", 0)
    dur_delta = (
        (with_s.get("mean_duration_ms") or 0) - (no_s.get("mean_duration_ms") or 0)
    )

    # Group scoring rows by document key
    rows_by_doc = defaultdict(list)
    for r in rows:
        rows_by_doc[r["doc_key"]].append(r)

    # Per-document summary
    per_filing_rows = []
    for bank in banks:
        doc_key = ds.doc_key_fn(bank)
        bank_rows = rows_by_doc.get(doc_key, [])
        ws_correct = sum(1 for r in bank_rows if r["condition"] == "with_skill" and r["status"] == "correct")
        ns_correct = sum(1 for r in bank_rows if r["condition"] == "no_skill" and r["status"] == "correct")

        if is_batch_only:
            ws_run = runs_batch_dir / f"{doc_key}_with_skill"
            ns_run = runs_batch_dir / f"{doc_key}_no_skill"
        else:
            ws_run = runs_dir / f"{doc_key}_with_skill"
            ns_run = runs_dir / f"{doc_key}_no_skill"

        def load_res(p: Path) -> dict[str, Any]:
            f = p / "result.json"
            return json.loads(f.read_text()) if f.exists() else {}
        ws_res = load_res(ws_run)
        ns_res = load_res(ns_run)
        per_filing_rows.append({
            "bank": bank,
            "doc_key": doc_key,
            "ws_correct": ws_correct,
            "ns_correct": ns_correct,
            "ws_cost": ws_res.get("total_cost_usd"),
            "ns_cost": ns_res.get("total_cost_usd"),
            "ws_dur": ws_res.get("duration_ms"),
            "ns_dur": ns_res.get("duration_ms"),
            "ws_excerpts": load_trace_excerpts(ws_run),
            "ns_excerpts": load_trace_excerpts(ns_run),
            "rows": bank_rows,
        })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_html(
        dataset=ds,
        banks=banks,
        summary=summary,
        with_s=with_s,
        no_s=no_s,
        with_s_v1=summary_v1.get("with_skill", {}) if summary_v1 else {},
        invocation_stats=invocation_stats,
        summary_batch=summary_batch,
        acc_delta=acc_delta,
        cost_delta=cost_delta,
        dur_delta=dur_delta,
        per_filing=per_filing_rows,
        schema_fields=schema_fields,
        latency_cw=latency_cw,
        ws_cold=(summary_cold.get("with_skill") or {}),
        ws_warm=(summary_warm.get("with_skill") or {}),
    ))
    print(f"Wrote {output_path}")


def render_mode_comparison(summary, summary_batch, invocation_stats) -> str:
    """Render the per-file vs batch overlay panel. Empty string if no batch data."""
    if not summary_batch:
        return ""
    pf_ws = summary.get("with_skill", {}) or {}
    pf_ns = summary.get("no_skill", {}) or {}
    b_ws = summary_batch.get("with_skill", {}) or {}
    b_ns = summary_batch.get("no_skill", {}) or {}
    batch_inv = (invocation_stats.get("batch") or {}) if invocation_stats else {}
    binv_ws = batch_inv.get("with_skill", {}) or {}
    binv_ns = batch_inv.get("no_skill", {}) or {}

    def pct(x):
        return fmt_pct(x) if x is not None else "—"

    def delta_money(pf_val, b_val):
        if pf_val is None or b_val is None:
            return "—"
        d = pf_val - b_val
        cls = "delta-pos" if d > 0 else "delta-neg"
        return f"<span class='{cls}'>{signed(d, 'money')}</span>"

    def delta_secs(pf_ms, b_ms):
        if pf_ms is None or b_ms is None:
            return "—"
        d = pf_ms - b_ms
        cls = "delta-pos" if d > 0 else "delta-neg"
        return f"<span class='{cls}'>{signed(d, 'seconds')}</span>"

    py_writes_warn_ws = binv_ws.get("py_writes", 0)
    py_writes_warn_ns = binv_ns.get("py_writes", 0)
    warn_html = ""
    if py_writes_warn_ws > 0 or py_writes_warn_ns > 0:
        warn_html = (
            f"<div class='warn-callout'>⚠ Batch session wrote {py_writes_warn_ws + py_writes_warn_ns} "
            f".py file(s) — the agent transcribed a wrapper instead of looping the CLI. Inspect "
            f"<code>runs_batch/&lt;condition&gt;/trace.jsonl</code>.</div>"
        )

    # Discipline summary text
    if binv_ws:
        n_extract = binv_ws.get("extract_calls", 0)
        n_par = binv_ws.get("parallel_turns", 0)
        max_par = binv_ws.get("max_parallel_in_turn", 0)
        task_starts = binv_ws.get("task_starts", 0)
        if task_starts >= n_extract and n_extract > 0:
            discipline_ws = (f"<b>Background-parallel via Task tool</b>: launched <b>{task_starts}</b> "
                             f"Task subagents (one per extract.py call), running concurrently in the "
                             f"background while the main agent's turns themselves stayed sequential. "
                             f"This is where the wall-time win comes from.")
        elif n_par > 0 and max_par > 1:
            discipline_ws = f"<b>In-turn parallel</b>: emitted up to <b>{max_par}</b> tool calls in a single turn ({n_par} parallel turns)."
        else:
            discipline_ws = f"<b>Serial</b>: {n_extract} extract.py calls one-per-turn (no parallel tool batching, no Task tool)."
    else:
        discipline_ws = "—"

    return f"""
  <div class="section-label">Per-file vs batch session · same 15 PDFs, v2 skill</div>
  {warn_html}
  <table class="cmp-table">
    <thead><tr><th></th><th>per-file (15 sessions)</th><th>batch (1 session)</th><th>delta (per-file − batch)</th></tr></thead>
    <tbody>
      <tr>
        <td class="cond-with">with_skill · total cost</td>
        <td>{fmt_money(pf_ws.get('total_cost_usd'))}</td>
        <td>{fmt_money(b_ws.get('total_cost_usd'))}</td>
        <td>{delta_money(pf_ws.get('total_cost_usd'), b_ws.get('total_cost_usd'))}</td>
      </tr>
      <tr>
        <td class="cond-with">with_skill · wall time</td>
        <td>{fmt_seconds(pf_ws.get('total_duration_ms'))}</td>
        <td>{fmt_seconds(b_ws.get('total_duration_ms') or b_ws.get('session_duration_ms'))}</td>
        <td>{delta_secs(pf_ws.get('total_duration_ms'), b_ws.get('total_duration_ms') or b_ws.get('session_duration_ms'))}</td>
      </tr>
      <tr>
        <td class="cond-no">no_skill · total cost</td>
        <td>{fmt_money(pf_ns.get('total_cost_usd'))}</td>
        <td>{fmt_money(b_ns.get('total_cost_usd'))}</td>
        <td>{delta_money(pf_ns.get('total_cost_usd'), b_ns.get('total_cost_usd'))}</td>
      </tr>
      <tr>
        <td class="cond-no">no_skill · wall time</td>
        <td>{fmt_seconds(pf_ns.get('total_duration_ms'))}</td>
        <td>{fmt_seconds(b_ns.get('total_duration_ms') or b_ns.get('session_duration_ms'))}</td>
        <td>{delta_secs(pf_ns.get('total_duration_ms'), b_ns.get('total_duration_ms') or b_ns.get('session_duration_ms'))}</td>
      </tr>
    </tbody>
  </table>

  <table class="cmp-table">
    <thead><tr><th>condition · mode</th><th>Skill fires</th><th>extract.py calls</th><th>Task subagents</th><th>py writes</th><th>parallel turns</th><th>max parallel in turn</th></tr></thead>
    <tbody>
      <tr>
        <td class="cond-with">with_skill · batch</td>
        <td>{binv_ws.get('n_invoked_skill', '—')}/1</td>
        <td>{binv_ws.get('extract_calls', '—')}</td>
        <td><b>{binv_ws.get('task_starts', '—')}</b></td>
        <td>{binv_ws.get('py_writes', '—')}</td>
        <td>{binv_ws.get('parallel_turns', '—')}</td>
        <td>{binv_ws.get('max_parallel_in_turn', '—')}</td>
      </tr>
      <tr>
        <td class="cond-no">no_skill · batch</td>
        <td>—</td>
        <td>—</td>
        <td>{binv_ns.get('task_starts', '—')}</td>
        <td>{binv_ns.get('py_writes', '—')}</td>
        <td>{binv_ns.get('parallel_turns', '—')}</td>
        <td>{binv_ns.get('max_parallel_in_turn', '—')}</td>
      </tr>
    </tbody>
  </table>
  <p class="discipline-note">Batch with_skill invocation pattern: {discipline_ws}</p>
  <p class="caveat">Batch wall time is end-to-end session duration; per-file wall time is the sum of 15 independent session durations. Both measure "time to extract the corpus."</p>
"""


def _filing_name(b: dict, doc_key: str) -> str:
    return b.get("name") or b.get("label") or b.get("legal_name") or doc_key


def _filing_meta(b: dict, doc_key: str) -> str:
    """Best-effort one-line metadata that works across dataset manifest shapes."""
    parts: list[str] = []
    if b.get("rssd"):
        parts.append(f"RSSD {b['rssd']}")
        if b.get("asset_bucket"):
            parts.append(b["asset_bucket"])
        if b.get("expected_form_version"):
            parts.append(f"expected form {b['expected_form_version']}")
    elif b.get("nct_id"):
        parts.append(b["nct_id"])
        if b.get("label"):
            parts.append(b["label"])
    elif b.get("ein"):
        parts.append(f"EIN {b['ein']}")
        if b.get("tax_year"):
            parts.append(f"TY {b['tax_year']}")
    elif b.get("cik"):
        parts.append(f"CIK {b['cik']}")
        if b.get("ticker"):
            parts.append(b["ticker"])
        if b.get("period"):
            parts.append(b["period"])
    else:
        parts.append(doc_key)
    return " · ".join(str(p) for p in parts)


def render_latency_panel(latency_cw: dict) -> str:
    """Cold-vs-warm parse-cache latency panel. Empty string when no experiment data."""
    if not latency_cw:
        return ""
    b = latency_cw.get("batch", {}) or {}
    cold, warm = b.get("cold", {}) or {}, b.get("warm", {}) or {}
    cw, ww = cold.get("wall_s"), warm.get("wall_s")
    speed = b.get("speedup")
    pages = latency_cw.get("total_pages")
    ndocs = latency_cw.get("n_docs")
    credit = latency_cw.get("credit_cost_usd_per_pass")

    # Per-file pilot table (isolated extract-job latency, no Claude orchestration).
    pf = latency_cw.get("per_file_pilot", {}) or {}
    pc, pw = pf.get("cold", {}) or {}, pf.get("warm", {}) or {}
    pf_html = ""
    if pc.get("rows") and pw.get("rows"):
        wmap = {r["doc_key"]: r for r in pw["rows"]}
        trs = []
        for r in pc["rows"]:
            w = wmap.get(r["doc_key"], {})
            cs, ws = r.get("wall_s"), w.get("wall_s")
            sp = f"{cs/ws:.1f}×" if (cs and ws) else "—"
            trs.append(
                f"<tr><td class='field-name'>{html.escape(str(r.get('doc_key')))}</td>"
                f"<td>{r.get('pages')}</td><td>{cs}s</td><td>{ws}s</td>"
                f"<td><span class='delta-pos'>{sp}</span></td></tr>")
        pf_html = f"""
  <p class="caveat">Per-file probe (isolated extract-job wall, no Claude orchestration; pilot subset):</p>
  <table class="cmp-table">
    <thead><tr><th>document</th><th>pages</th><th>cold</th><th>warm</th><th>speedup</th></tr></thead>
    <tbody>{''.join(trs)}</tbody>
  </table>"""

    speed_str = f"{speed:.1f}×" if isinstance(speed, (int, float)) else "—"
    cold_turns = cold.get("num_turns")
    warm_turns = warm.get("num_turns")
    turns_note = ""
    if cold_turns is not None and warm_turns is not None:
        turns_note = (f" Batch wall also mixes in Claude's orchestration, which varies run to run "
                      f"(this pair: cold {cold_turns} turns vs warm {warm_turns} turns) and LlamaCloud "
                      f"server load — so the per-file probe below is the cleaner measure of the cache effect.")
    return f"""
  <div class="section-label">Parse cache · cold vs warm latency</div>
  <p class="cost-note" style="margin:0 0 1rem">The with_skill wall time is dominated by the LlamaCloud
  <b>parse</b> step, which is cached by document content hash. The same corpus runs faster once the
  parse cache is warm. Credit cost is <b>identical</b> per pass (billed per page) — only latency changes.
  There is no API toggle to disable the cache on the extract path (the documented <code>parse_config_id</code>
  hook 404s), and any change to parse options or tier busts the cache.</p>
  <table class="cmp-table">
    <thead><tr><th>cache state</th><th>batch wall ({ndocs} docs · {pages} pg)</th><th>credit cost</th></tr></thead>
    <tbody>
      <tr><td class="cond-no">cold (first parse)</td><td>{fmt_seconds((cw or 0)*1000) if cw else '—'}</td><td>{fmt_money(credit)}</td></tr>
      <tr><td class="cond-with">warm (cache hit)</td><td>{fmt_seconds((ww or 0)*1000) if ww else '—'}</td><td>{fmt_money(credit)}</td></tr>
      <tr><td><b>batch speedup</b></td><td><b><span class="delta-pos">{speed_str}</span></b></td><td class="dim">same</td></tr>
    </tbody>
  </table>
  <p class="caveat">The batch speedup ({speed_str}) is smaller than the per-file speedup because the batch
  parallelizes parses across Claude Task subagents and carries fixed orchestration overhead the cache
  can't shrink.{turns_note}</p>{pf_html}
"""


def render_html(*, dataset, banks, summary, with_s, no_s, with_s_v1, invocation_stats,
                summary_batch, acc_delta, cost_delta, dur_delta, per_filing, schema_fields,
                latency_cw=None, ws_cold=None, ws_warm=None) -> str:
    n_banks = len(banks)
    n_fields = len(schema_fields)
    has_v1 = bool(with_s_v1)
    # Invocation-rate stats per version
    v1_inv = (invocation_stats.get("baseline_v1", {}).get("with_skill", {}) if invocation_stats else {})
    v2_inv = (invocation_stats.get("v2", {}).get("with_skill", {}) if invocation_stats else {})
    batch_inv = (invocation_stats.get("batch", {}).get("with_skill", {}) if invocation_stats else {})
    v1_inv_n = v1_inv.get("n_invoked_skill", 0)
    v2_inv_n = v2_inv.get("n_invoked_skill", 0)
    v1_inv_rate = v1_inv.get("skill_invocation_rate", 0.0)
    v2_inv_rate = v2_inv.get("skill_invocation_rate", 0.0)
    inv_delta_pp = (v2_inv_rate - v1_inv_rate) * 100
    # First stat tile: v1→v2 invocation when a baseline exists, else batch skill firing.
    if has_v1:
        inv_stat_val = f"{v1_inv_n}/{v1_inv.get('n_runs', n_banks)} &rarr; {v2_inv_n}/{v2_inv.get('n_runs', n_banks)}"
        inv_stat_lbl = "skill invocation rate (v1 → v2)"
    else:
        inv_stat_val = f"{batch_inv.get('n_invoked_skill', 0)}/1"
        inv_stat_lbl = "skill invoked (batch with_skill)"

    rows_html = []
    for pf in per_filing:
        b = pf["bank"]
        rows_html.append(f"""
<details class='filing-card'>
  <summary>
    <span class='filing-name'>{html.escape(_filing_name(b, pf['doc_key']))}</span>
    <span class='filing-meta'>{html.escape(_filing_meta(b, pf['doc_key']))}</span>
    <span class='filing-scores'>
      <span class='score-pair'><span class='label'>with</span> <span class='val'>{pf['ws_correct']}/{len(schema_fields)}</span></span>
      <span class='score-pair'><span class='label'>no</span> <span class='val'>{pf['ns_correct']}/{len(schema_fields)}</span></span>
      <span class='score-pair'><span class='label'>cost</span> <span class='val'>{fmt_money(pf['ws_cost'])} vs {fmt_money(pf['ns_cost'])}</span></span>
    </span>
  </summary>
  <div class='filing-body'>
    <div class='heatmap-wrap'>
      <h4>Field-level results</h4>
      {per_filing_heatmap(pf['rows'], schema_fields)}
    </div>
    <div class='excerpts'>
      <div class='excerpts-col'>
        <h4>with_skill tool_use excerpts</h4>
        {render_excerpt_block(pf['ws_excerpts'])}
      </div>
      <div class='excerpts-col'>
        <h4>no_skill tool_use excerpts</h4>
        {render_excerpt_block(pf['ns_excerpts'])}
      </div>
    </div>
  </div>
</details>""")

    body_rows = "\n".join(rows_html)

    # v1-baseline row only when a baseline summary exists (FFIEC only).
    if has_v1:
        v1_row = (
            f'<tr><td class="cond-with-v1">with_skill (v1 baseline)</td>'
            f'<td>{fmt_pct(with_s_v1.get("accuracy"))}</td>'
            f'<td>{with_s_v1.get("n_correct","—")}</td><td>{with_s_v1.get("n_wrong","—")}</td>'
            f'<td>{with_s_v1.get("n_missing","—")}</td>'
            f'<td>{fmt_money(with_s_v1.get("token_cost_usd"))}</td>'
            f'<td>{fmt_money(with_s_v1.get("credit_cost_usd"))}</td>'
            f'<td>{fmt_money(with_s_v1.get("total_cost_usd"))}</td>'
            f'<td>{fmt_seconds(with_s_v1.get("mean_duration_ms"))}</td></tr>'
        )
    else:
        v1_row = ""

    # Cost note: explain the three-way split, surface billable pages + tier, and warn
    # on any API-vs-local page-count divergence. Batch-sourced (with_skill) only.
    n_pages = with_s.get("n_pages")
    et = with_s.get("extract_tier") or "agentic"
    pt = with_s.get("parse_tier") or "agentic"
    mismatches = with_s.get("page_count_mismatches") or []
    cost_note_parts = [
        "<p class='cost-note'>Total cost = <span class='cond-with'>Claude tokens</span> + "
        "LlamaCloud <b>credits</b> (parse + extract, billed per page at $0.00125/credit). "
        "<span class='cond-no'>no_skill</span> never calls LlamaCloud, so its credit cost is $0."
    ]
    if n_pages:
        cost_note_parts.append(
            f" with_skill billed <b>{n_pages:,}</b> pages "
            f"(extract={html.escape(str(et))}, parse={html.escape(str(pt))}).")
    cost_note_parts.append("</p>")
    cost_note_html = "".join(cost_note_parts)
    if mismatches:
        items = ", ".join(
            f"{html.escape(str(m.get('doc_key')))} (API {m.get('api_pages')} vs local {m.get('local_pages')})"
            for m in mismatches[:10])
        cost_note_html += (
            f"<div class='warn-callout'>⚠ {len(mismatches)} document(s) where the LlamaExtract "
            f"API page count diverged from the local PDF page count: {items}.</div>")

    # Mode-comparison panel only when we have genuine per-file data distinct from
    # the batch summary (FFIEC). Batch-only datasets pass the same object for both.
    mode_cmp_html = (
        render_mode_comparison(summary, summary_batch, invocation_stats)
        if (summary is not summary_batch and summary_batch) else ""
    )
    latency_panel = render_latency_panel(latency_cw or {})

    # Aggregate-table with_skill row(s). When cold/warm scored summaries are present,
    # split with_skill into two rows so the parse-cache latency shows inline.
    def _ws_row(label: str, d: dict) -> str:
        dur = d.get("total_duration_ms") or d.get("session_duration_ms") or d.get("mean_duration_ms")
        return (f'<tr><td class="cond-with">{label}</td>'
                f'<td>{fmt_pct(d.get("accuracy"))}</td>'
                f'<td>{d.get("n_correct","—")}</td><td>{d.get("n_wrong","—")}</td>'
                f'<td>{d.get("n_missing","—")}</td>'
                f'<td>{fmt_money(d.get("token_cost_usd"))}</td>'
                f'<td>{fmt_money(d.get("credit_cost_usd"))}</td>'
                f'<td><b>{fmt_money(d.get("total_cost_usd"))}</b></td>'
                f'<td>{fmt_seconds(dur)}</td></tr>')
    if ws_cold and ws_warm:
        with_skill_rows = (_ws_row("with_skill (cold)", ws_cold) + "\n      "
                           + _ws_row("with_skill (warm)", ws_warm))
        cache_note = ('<p class="caveat">with_skill split by LlamaCloud parse-cache state '
                      '(cold = first parse / new content hash; warm = cache hit). Accuracy and credit '
                      'cost are cache-invariant — same parsed content, same per-page billing; only '
                      'latency (and Claude token cost) vary.</p>')
    else:
        with_skill_rows = _ws_row("with_skill", with_s)
        cache_note = ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{html.escape(dataset.display_name)} Extraction — Claude Code vs Claude Code + LlamaExtract Skill</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/fontsource/fonts/overused-grotesk@latest/latin.css" rel="stylesheet">
<style>
:root {{
  --bg: #FFFFFF; --bg-alt: #F5F5F5; --surface: #FFFFFF; --surface2: #F5F5F5;
  --border: #E7E7E7; --text: #000000; --text-dim: #737373;
  --purple: #3E18F9; --blue: #37D7FA; --pink: #FF8DF2; --orange: #FF8705;
  --gradient-stroke: linear-gradient(180deg, #37D7FA 0%, #4B72FE 40%, #FF8DF2 68%, #FF8705 100%);
  --success: #1FA853; --danger: #E0344C; --missing: #FFBD74; --neutral: #C8C8C8;
}}
* {{ margin:0; padding:0; box-sizing: border-box; }}
body {{ font-family: 'Overused Grotesk', -apple-system, sans-serif; background: var(--bg); color: var(--text); line-height: 1.55; }}
.hero {{ text-align:center; padding: 2.5rem 2rem 2rem;
  background: linear-gradient(180deg, rgba(150,231,249,0.08), rgba(146,174,255,0.06) 30%, rgba(255,191,248,0.04) 60%, transparent 85%);
  border-bottom: 1px solid var(--border); position: relative;
}}
.hero::after {{ content:''; position:absolute; bottom:0; left:0; right:0; height:2px; background: var(--gradient-stroke); }}
.hero h1 {{ font-size: 2.1rem; font-weight: 500; letter-spacing: -0.03em; line-height: 1.1; margin-bottom: 0.4rem; }}
.hero h1 .accent {{ background: var(--gradient-stroke); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
.hero .subtitle {{ color: var(--text-dim); font-size: 0.95rem; max-width: 720px; margin: 0.3rem auto 0; }}
.stats-bar {{ display:grid; grid-template-columns: repeat(4, 1fr); max-width: 800px; margin: 1.5rem auto 0;
  gap: 1px; background: var(--border); border-radius: 12px; overflow: hidden; position: relative; }}
.stats-bar::before {{ content:''; position:absolute; top:0; left:0; right:0; height:2px; background: var(--gradient-stroke); z-index:1; }}
.stat {{ background: var(--bg); padding: 0.9rem 0.6rem; text-align: center; }}
.stat .val {{ font-size: 1.35rem; font-weight: 500; font-variant-numeric: tabular-nums; letter-spacing: -0.03em; }}
.stat .lbl {{ font-family: 'IBM Plex Mono', monospace; font-size: 0.65rem; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.04em; margin-top: 0.15rem; }}
.delta-pos {{ color: var(--success); }}
.delta-neg {{ color: var(--danger); }}
.container {{ max-width: 1100px; margin: 0 auto; padding: 2rem 1.5rem 4rem; }}
.section-label {{ font-family: 'IBM Plex Mono', monospace; font-size: 0.7rem; font-weight: 500; color: var(--text-dim);
  text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 0.9rem;
  padding-bottom: 0.4rem; border-bottom: 2px solid transparent; border-image: var(--gradient-stroke) 1; }}
.cmp-table {{ width:100%; border-collapse: collapse; font-variant-numeric: tabular-nums; margin-bottom: 2.5rem; }}
.cmp-table th, .cmp-table td {{ padding: 0.55rem 0.7rem; text-align: left; border-bottom: 1px solid var(--border); }}
.cmp-table th {{ font-family: 'IBM Plex Mono', monospace; font-weight: 500; font-size: 0.75rem; text-transform: uppercase; color: var(--text-dim); }}
.cmp-table tbody tr:hover {{ background: var(--bg-alt); }}
.cond-with {{ color: var(--purple); font-weight: 500; }}
.cond-with-v1 {{ color: var(--text-dim); font-weight: 400; font-style: italic; }}
.cond-no {{ color: var(--orange); font-weight: 500; }}
.filing-card {{ background: var(--bg); border: 1px solid var(--border); border-radius: 10px; margin-bottom: 0.75rem; overflow: hidden; }}
.filing-card[open] {{ box-shadow: 0 2px 12px rgba(0,0,0,0.04); }}
.filing-card summary {{ list-style: none; padding: 0.85rem 1rem; cursor: pointer; display: grid;
  grid-template-columns: minmax(180px, 0.3fr) minmax(200px, 0.3fr) minmax(200px, 0.5fr); align-items: center; gap: 1rem; }}
.filing-card summary::-webkit-details-marker {{ display: none; }}
.filing-name {{ font-weight: 500; font-size: 1rem; }}
.filing-meta {{ font-family: 'IBM Plex Mono', monospace; font-size: 0.7rem; color: var(--text-dim); }}
.filing-scores {{ display: flex; gap: 1.1rem; justify-content: flex-end; flex-wrap: wrap; font-variant-numeric: tabular-nums; }}
.score-pair .label {{ font-family: 'IBM Plex Mono', monospace; font-size: 0.65rem; color: var(--text-dim); text-transform: uppercase; }}
.score-pair .val {{ font-weight: 500; font-size: 0.9rem; margin-left: 0.2rem; }}
.filing-body {{ padding: 1rem 1.4rem 1.4rem; border-top: 1px solid var(--border); display: grid; grid-template-columns: 1fr 1.2fr; gap: 1.5rem; }}
.heatmap-wrap h4, .excerpts-col h4 {{ font-family: 'IBM Plex Mono', monospace; font-size: 0.75rem; text-transform: uppercase;
  color: var(--text-dim); letter-spacing: 0.04em; margin-bottom: 0.5rem; font-weight: 500; }}
.heatmap {{ width: 100%; border-collapse: collapse; font-size: 0.8rem; }}
.heatmap th {{ font-family: 'IBM Plex Mono', monospace; font-size: 0.65rem; color: var(--text-dim); text-align: left; padding: 0.2rem 0.4rem;
  font-weight: 500; text-transform: uppercase; }}
.heatmap td {{ padding: 0.18rem 0.4rem; vertical-align: middle; }}
.field-name {{ font-family: 'IBM Plex Mono', monospace; font-size: 0.72rem; color: var(--text); }}
.cell {{ width: 14px; height: 14px; border-radius: 3px; display: inline-block; }}
.cell-correct {{ background: var(--success); }}
.cell-wrong {{ background: var(--danger); }}
.cell-missing {{ background: var(--missing); }}
.cell-format_error {{ background: var(--danger); border: 1px dashed #000; }}
.cell-na {{ background: var(--neutral); }}
.excerpts {{ display: flex; flex-direction: column; gap: 1rem; }}
.excerpts-col {{ background: var(--bg-alt); border-radius: 8px; padding: 0.7rem 0.9rem; }}
.tool-event {{ font-family: 'IBM Plex Mono', monospace; font-size: 0.7rem; padding: 0.2rem 0; border-bottom: 1px dashed var(--border); }}
.tool-event:last-child {{ border-bottom: none; }}
.tool-name {{ background: var(--purple); color: white; padding: 0.05rem 0.35rem; border-radius: 3px; font-size: 0.65rem; font-weight: 500; }}
.tool-input {{ color: var(--text-dim); margin-left: 0.5rem; }}
.warn-callout {{ background: rgba(255, 135, 5, 0.08); border-left: 3px solid var(--orange); padding: 0.6rem 0.9rem; border-radius: 4px;
  font-size: 0.85rem; margin-bottom: 1rem; }}
.warn-callout code {{ background: var(--bg-alt); padding: 0.05rem 0.3rem; border-radius: 3px; font-family: 'IBM Plex Mono', monospace; font-size: 0.75rem; }}
.discipline-note {{ font-size: 0.85rem; color: var(--text); margin: 0.5rem 0 0.3rem; }}
.cost-note {{ font-size: 0.83rem; color: var(--text-dim); margin: -1.8rem 0 2.2rem; }}
.cost-note .cond-with {{ color: var(--purple); }} .cost-note .cond-no {{ color: var(--orange); }}
.cost-col {{ color: var(--text-dim); }}
.caveat {{ font-size: 0.75rem; color: var(--text-dim); font-style: italic; margin-bottom: 2.5rem; }}
footer {{ text-align: center; padding: 2rem 1rem; color: var(--text-dim); font-family: 'IBM Plex Mono', monospace; font-size: 0.7rem; }}
</style>
</head>
<body>
<div class="hero">
  <h1>Claude Code <span class="accent">vs</span> Claude Code + LlamaExtract Skill</h1>
  <div class="subtitle">{html.escape(dataset.display_name)} structured extraction — {n_banks} documents, ~{n_fields} fields each.</div>
  <div class="stats-bar">
    <div class="stat"><div class="val delta-pos">{inv_stat_val}</div><div class="lbl">{inv_stat_lbl}</div></div>
    <div class="stat"><div class="val">{signed(acc_delta, 'pct') if acc_delta is not None else '—'}</div><div class="lbl">accuracy Δ (with−no)</div></div>
    <div class="stat"><div class="val">{signed(cost_delta, 'money')}</div><div class="lbl">total cost Δ (with−no)</div></div>
    <div class="stat"><div class="val">{n_banks}</div><div class="lbl">documents</div></div>
  </div>
</div>

<div class="container">

  <div class="section-label">Aggregate comparison · with_skill vs no_skill</div>
  <table class="cmp-table">
    <thead><tr><th>condition</th><th>accuracy</th><th>correct</th><th>wrong</th><th>missing</th><th class="cost-col">token cost</th><th class="cost-col">credit cost</th><th class="cost-col">total cost</th><th>total/session duration</th></tr></thead>
    <tbody>
      {v1_row}
      {with_skill_rows}
      <tr><td class="cond-no">no_skill</td><td>{fmt_pct(no_s.get('accuracy'))}</td><td>{no_s.get('n_correct','—')}</td><td>{no_s.get('n_wrong','—')}</td><td>{no_s.get('n_missing','—')}</td><td>{fmt_money(no_s.get('token_cost_usd'))}</td><td>{fmt_money(no_s.get('credit_cost_usd'))}</td><td><b>{fmt_money(no_s.get('total_cost_usd'))}</b></td><td>{fmt_seconds(no_s.get('total_duration_ms') or no_s.get('session_duration_ms') or no_s.get('mean_duration_ms'))}</td></tr>
    </tbody>
  </table>
  {cache_note}
  {cost_note_html}
  {latency_panel}

  {mode_cmp_html}

  <div class="section-label">Per-document comparison · click to expand</div>
  {body_rows}

</div>

<footer>
  Generated for dataset <code>{html.escape(dataset.slug)}</code> ·
  model <code>claude-opus-4-7</code> · with-skill loads <code>llama-extract</code> as a project skill,
  no-skill runs with <code>--bare</code>
</footer>

</body>
</html>
"""


if __name__ == "__main__":
    main()
