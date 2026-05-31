"""Render the cleaned cross-dataset batch-mode report to results/cross_dataset_summary.md.

For each registered dataset it reads the *updated* batch results: the latest
``results/<slug>_rerun_*`` directory if one exists, otherwise the canonical
``results/<slug>``. Pulls summary_batch.json (accuracy/cost/wall) and
invocation_stats.json (skill firing, CLI calls).

Run: python scripts/render_cross_dataset_summary.py [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

from scripts.datasets import REGISTRY, get_dataset


RESULTS = Path("results")


def _pct(x: float | None) -> str:
    return f"{x*100:.1f}%" if isinstance(x, (int, float)) else "—"


def _money(x: float | None) -> str:
    return f"${x:,.2f}" if isinstance(x, (int, float)) else "—"


def _secs(ms: float | None) -> str:
    return f"{ms/1000:.0f}s" if isinstance(ms, (int, float)) and ms else "—"


def _ratio_str(cost_with: float | None, cost_no: float | None) -> str:
    """Direction-aware total-cost comparison. with_skill can be cheaper OR pricier
    than no_skill once LlamaCloud credits are included."""
    if not (cost_with and cost_no):
        return "—"
    if cost_no >= cost_with:
        return f"{cost_no/cost_with:.1f}× cheaper"
    return f"{cost_with/cost_no:.1f}× pricier"


def _wall_cold_warm(r: dict) -> tuple[str, str]:
    """(cold, warm) with_skill wall strings. Uses the cold/warm experiment when present,
    else falls back to the single canonical wall in the cold slot."""
    if r.get("lat_cold") or r.get("lat_warm"):
        cold = _secs((r["lat_cold"] or 0) * 1000) if r.get("lat_cold") else "—"
        warm = _secs((r["lat_warm"] or 0) * 1000) if r.get("lat_warm") else "—"
        return cold, warm
    return _secs(r.get("wall_w")), "—"


def _ratio_html(cost_with: float | None, cost_no: float | None) -> str:
    if not (cost_with and cost_no):
        return '<span class="dim">—</span>'
    cheaper = cost_no >= cost_with
    cls = "pos" if cheaper else "neg"
    return f'<span class="{cls}">{_ratio_str(cost_with, cost_no)}</span>'


def _load(p: Path) -> dict:
    return json.loads(p.read_text()) if p.exists() else {}


def _cost_sentence(rows: list[dict]) -> str:
    """Data-driven replacement for the old hardcoded 'with_skill is 3-8x cheaper'
    cost narrative — now that total cost includes LlamaCloud credits."""
    tok = [r["token_ratio"] for r in rows if r.get("token_ratio")]
    pairs = [(r["tot_w"], r["tot_n"]) for r in rows if r.get("tot_w") and r.get("tot_n")]
    pricier = [w / n for (w, n) in pairs if w > n]
    cheaper = [n / w for (w, n) in pairs if n >= w]
    parts: list[str] = []
    if tok:
        parts.append(f"On Claude tokens alone, with_skill is {min(tok):.1f}–{max(tok):.1f}× cheaper "
                     "(it offloads document reading to LlamaCloud).")
    parts.append("But LlamaExtract bills per page — ~$0.03125/page at agentic parse+extract "
                 "($0.00125/credit) — so credit cost scales with corpus pages, not Claude work.")
    if pricier and not cheaper:
        parts.append(f"Across all {len(pairs)} corpora those credits more than offset the token saving: "
                     f"with_skill total cost runs {min(pricier):.1f}–{max(pricier):.1f}× higher than no_skill.")
    elif pricier and cheaper:
        parts.append(f"On total cost the result is mixed — with_skill is pricier on {len(pricier)}/{len(pairs)} "
                     f"corpora and still cheaper on {len(cheaper)}/{len(pairs)}.")
    elif cheaper:
        parts.append(f"Even with credits included, with_skill total cost stays "
                     f"{min(cheaper):.1f}–{max(cheaper):.1f}× cheaper than no_skill.")
    parts.append("Token, credit, and total are shown as separate line items above.")
    return " ".join(parts)


def _md_bold_to_html(s: str) -> str:
    """Escape HTML, then render the small markdown subset used in FINDINGS."""
    import html as _h
    import re
    out = _h.escape(s)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"`(.+?)`", r"<code>\1</code>", out)
    out = re.sub(r"\*(.+?)\*", r"<i>\1</i>", out)
    return out


def _source_dir(slug: str) -> tuple[Path, str]:
    """Latest results/<slug>_rerun_* if present, else canonical results/<slug>."""
    reruns = sorted(glob.glob(str(RESULTS / f"{slug}_rerun_*")))
    if reruns:
        d = Path(reruns[-1])
        return d, d.name.split("_rerun_")[-1] + " (rerun)"
    return RESULTS / slug, "original"


# One-line, dataset-specific explanation of the with-vs-no accuracy result. These
# are the established conclusions from the per-dataset error analyses.
FINDINGS: dict[str, str] = {
    "ffiec_call_reports": "Skill **edges ahead** — Call Report values are raw dollars on a fixed "
        "form grid; delegated extraction reads the schedule cells cleanly.",
    "sec_10q_insurance": "Skill **trails by ~18pp** — a systematic dropped unit conversion: on "
        "\"(in millions)\" filings llama-extract returns the table value verbatim (28 of 37 misses "
        "are off by exactly ×1,000,000). Direct reading sees the header and multiplies.",
    "irs_form_990": "**Near tie** — Form 990 amounts are raw dollars (no unit trap). Most remaining "
        "errors are `investment_income`, which mismatches the ground-truth line definition under "
        "*both* conditions.",
    "ctgov_protocols": "Skill **trails by ~20pp** — brittle exact-string matches on free-text "
        "`brief_title`/`sponsor`, plus nulls on categorical design fields (allocation/masking) that "
        "direct reading recovers from deep in the document.",
}


def _delta_html(d: float | None) -> str:
    if d is None:
        return '<span class="dim">—</span>'
    cls = "pos" if d >= 0 else "neg"
    return f'<span class="{cls}">{"+" if d>=0 else ""}{d*100:.1f}pp</span>'


def _row_from_summary(ds, slug: str, src: Path, src_label: str,
                      summary: dict, inv_block: dict, latb: dict) -> dict:
    """Build one render row from a scored summary (batch summary_batch.json OR per-file
    summary.json) plus its matching invocation block (batch/v2) and optional cold/warm
    latency block (batch only; per-file passes {})."""
    lat_cold = (latb.get("cold") or {}).get("wall_s") if latb else None
    lat_warm = (latb.get("warm") or {}).get("wall_s") if latb else None
    lat_speedup = latb.get("speedup") if latb else None
    ws, ns = summary.get("with_skill", {}) or {}, summary.get("no_skill", {}) or {}
    acc_w, acc_n = ws.get("accuracy"), ns.get("accuracy")
    # Three-way cost split: token (Claude) + credit (LlamaCloud) = total.
    tok_w, cred_w, tot_w = ws.get("token_cost_usd"), ws.get("credit_cost_usd"), ws.get("total_cost_usd")
    tok_n, tot_n = ns.get("token_cost_usd"), ns.get("total_cost_usd")  # no_skill credit == 0
    pages_w = ws.get("n_pages")
    wall_w = ws.get("total_duration_ms") or ws.get("session_duration_ms")
    wall_n = ns.get("total_duration_ms") or ns.get("session_duration_ms")
    acc_delta = (acc_w - acc_n) if (acc_w is not None and acc_n is not None) else None
    cost_ratio = (tot_n / tot_w) if (tot_w and tot_n) else None
    token_ratio = (tok_n / tok_w) if (tok_w and tok_n) else None
    return {
        "name": ds.display_name, "slug": slug, "src": src, "src_label": src_label,
        "n": ws.get("n_runs") or ns.get("n_runs") or 0,
        "acc_w": acc_w, "acc_n": acc_n, "acc_delta": acc_delta,
        "tok_w": tok_w, "cred_w": cred_w, "tot_w": tot_w, "tok_n": tok_n, "tot_n": tot_n,
        "pages_w": pages_w, "cost_ratio": cost_ratio, "token_ratio": token_ratio,
        "wall_w": wall_w, "wall_n": wall_n,
        "lat_cold": lat_cold, "lat_warm": lat_warm, "lat_speedup": lat_speedup,
        "skill": (inv_block.get("with_skill") or {}).get("n_invoked_skill"),
        "cli": (inv_block.get("with_skill") or {}).get("extract_calls"),
        "why": FINDINGS.get(slug, ""),
        "why_html": _md_bold_to_html(FINDINGS.get(slug, "")),
    }


def _acc_table_md(rows: list[dict], why_col: bool = True) -> list[str]:
    # The "Why" findings are batch-derived directional conclusions, so they only
    # apply to the batch table; per-file passes why_col=False.
    if why_col:
        out = ["| Dataset | N | Acc (with) | Acc (no) | Δ (with−no) | Why |",
               "|---|---|---|---|---|---|"]
    else:
        out = ["| Dataset | N | Acc (with) | Acc (no) | Δ (with−no) |",
               "|---|---|---|---|---|"]
    for r in rows:
        d = r["acc_delta"]
        dstr = (f"{'+' if d>=0 else ''}{d*100:.1f}pp") if d is not None else "—"
        base = f"| {r['name']} | {r['n']} | {_pct(r['acc_w'])} | {_pct(r['acc_n'])} | {dstr} |"
        out.append(base + (f" {FINDINGS.get(r['slug'],'')} |" if why_col else ""))
    return out


def _cost_table_md(rows: list[dict], with_latency: bool = True) -> list[str]:
    if with_latency:
        out = ["| Dataset | Token (with) | Credit (with) | Total (with) | Total (no) | Total vs no | "
               "Pages | Wall cold (with) | Wall warm (with) | Wall (no) |",
               "|---|---|---|---|---|---|---|---|---|---|"]
    else:
        out = ["| Dataset | Token (with) | Credit (with) | Total (with) | Total (no) | Total vs no | "
               "Pages | Wall (with) | Wall (no) |",
               "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        pages = f"{r['pages_w']:,}" if isinstance(r["pages_w"], int) else "—"
        base = (f"| {r['name']} | {_money(r['tok_w'])} | {_money(r['cred_w'])} | {_money(r['tot_w'])} | "
                f"{_money(r['tot_n'])} | {_ratio_str(r['tot_w'], r['tot_n'])} | {pages} |")
        if with_latency:
            wall_cold, wall_warm = _wall_cold_warm(r)
            out.append(base + f" {wall_cold} | {wall_warm} | {_secs(r['wall_n'])} |")
        else:
            out.append(base + f" {_secs(r['wall_w'])} | {_secs(r['wall_n'])} |")
    return out


def _compare_table_md(rows_b: list[dict], rows_pf: list[dict]) -> list[str]:
    """Per-dataset batch→per-file deltas for the headline numbers."""
    pf_by = {r["slug"]: r for r in rows_pf}
    out = ["| Dataset | Acc with (batch→file) | Acc no (batch→file) | "
           "Total with (batch→file) | Total no (batch→file) |",
           "|---|---|---|---|---|"]
    for rb in rows_b:
        rp = pf_by.get(rb["slug"])
        if not rp:
            continue
        out.append(
            f"| {rb['name']} | {_pct(rb['acc_w'])} → {_pct(rp['acc_w'])} | "
            f"{_pct(rb['acc_n'])} → {_pct(rp['acc_n'])} | "
            f"{_money(rb['tot_w'])} → {_money(rp['tot_w'])} | "
            f"{_money(rb['tot_n'])} → {_money(rp['tot_n'])} |")
    return out


def _acc_rows_html(rows: list[dict], why_col: bool = True) -> str:
    import html as _h
    def row(r: dict) -> str:
        cells = (f"<td class='name'>{_h.escape(r['name'])}</td><td>{r['n']}</td>"
                 f"<td class='with'>{_pct(r['acc_w'])}</td><td class='no'>{_pct(r['acc_n'])}</td>"
                 f"<td>{_delta_html(r['acc_delta'])}</td>")
        if why_col:
            cells += f"<td class='why'>{r['why_html']}</td>"
        return f"<tr>{cells}</tr>"
    return "\n".join(row(r) for r in rows)


def _cost_rows_html(rows: list[dict], with_latency: bool = True) -> str:
    import html as _h
    def row(r: dict) -> str:
        pages = f"{r['pages_w']:,}" if isinstance(r["pages_w"], int) else "—"
        cells = (
            f"<td class='name'>{_h.escape(r['name'])}</td>"
            f"<td class='with'>{_money(r['tok_w'])}</td>"
            f"<td class='with'>{_money(r['cred_w'])}</td>"
            f"<td class='with'><b>{_money(r['tot_w'])}</b></td>"
            f"<td class='no'>{_money(r['tot_n'])}</td>"
            f"<td>{_ratio_html(r['tot_w'], r['tot_n'])}</td>"
            f"<td>{pages}</td>"
        )
        if with_latency:
            cells += (f"<td class='with'>{_wall_cold_warm(r)[0]}</td>"
                      f"<td class='with'>{_wall_cold_warm(r)[1]}</td>"
                      f"<td class='no'>{_secs(r['wall_n'])}</td>")
        else:
            cells += (f"<td class='with'>{_secs(r['wall_w'])}</td>"
                      f"<td class='no'>{_secs(r['wall_n'])}</td>")
        return f"<tr>{cells}</tr>"
    return "\n".join(row(r) for r in rows)


def _compare_rows_html(rows_b: list[dict], rows_pf: list[dict]) -> str:
    import html as _h
    pf_by = {r["slug"]: r for r in rows_pf}
    out: list[str] = []
    for rb in rows_b:
        rp = pf_by.get(rb["slug"])
        if not rp:
            continue
        out.append(
            f"<tr><td class='name'>{_h.escape(rb['name'])}</td>"
            f"<td class='with'>{_pct(rb['acc_w'])} → {_pct(rp['acc_w'])}</td>"
            f"<td class='no'>{_pct(rb['acc_n'])} → {_pct(rp['acc_n'])}</td>"
            f"<td class='with'>{_money(rb['tot_w'])} → {_money(rp['tot_w'])}</td>"
            f"<td class='no'>{_money(rb['tot_n'])} → {_money(rp['tot_n'])}</td></tr>")
    return "\n".join(out)


def render_html(rows: list[dict], rows_pf: list[dict], date: str | None, cost_sentence: str) -> str:
    import html as _h
    acc_tr = _acc_rows_html(rows)
    cost_tr = _cost_rows_html(rows, with_latency=True)

    lat_rows = [r for r in rows if r.get("lat_cold") and r.get("lat_warm")]
    lat_section = ""
    if lat_rows:
        lat_tr = "\n".join(
            f"<tr><td class='name'>{_h.escape(r['name'])}</td>"
            f"<td class='no'>{_secs((r['lat_cold'] or 0)*1000)}</td>"
            f"<td class='with'>{_secs((r['lat_warm'] or 0)*1000)}</td>"
            f"<td><span class='pos'>{r['lat_speedup']:.1f}×</span></td></tr>"
            for r in lat_rows
        )
        lat_section = f"""
  <div class="section-label">Parse cache · cold vs warm latency</div>
  <p class="intro" style="margin-bottom:0.6rem">Same corpus and config — only the LlamaCloud
  <b>parse</b> cache differs (cached by document content hash). A warm cache skips re-parsing.
  <b>Credit cost is identical per pass</b> (billed per page) — only latency changes. No API toggle
  disables the cache on the extract path, and any parse-option or tier change busts it. The batch
  speedup below understates the isolated cache effect (the batch parallelizes parses and carries fixed
  orchestration overhead, and is noisy run-to-run); a per-file probe shows ~7×.</p>
  <table>
    <thead><tr><th>Dataset</th><th class="no">Cold wall</th><th class="with">Warm wall</th><th>Speedup</th></tr></thead>
    <tbody>
{lat_tr}
    </tbody>
  </table>"""
    # Per-file mode section (one claude session per document). Cost table omits the
    # cold/warm columns — there is no per-file cold/warm latency pilot.
    perfile_section = ""
    if rows_pf:
        pf_acc_tr = _acc_rows_html(rows_pf, why_col=False)
        pf_cost_tr = _cost_rows_html(rows_pf, with_latency=False)
        perfile_section = f"""
  <div class="section-label">Per-file mode · accuracy</div>
  <p class="intro" style="margin-bottom:0.6rem">One claude session <b>per document</b> (vs one session over the
  whole corpus in batch mode). Same conditions: <span class="with">with_skill</span> delegates to LlamaCloud;
  <span class="no">no_skill</span> is the full agent without the <code>llama-extract</code> skill.</p>
  <table>
    <thead><tr><th>Dataset</th><th>N</th><th class="with">Acc (with)</th><th class="no">Acc (no)</th><th>Δ (with−no)</th></tr></thead>
    <tbody>
{pf_acc_tr}
    </tbody>
  </table>

  <div class="section-label">Per-file mode · cost</div>
  <p class="intro" style="margin-bottom:0.6rem">Per-file <span class="no">no_skill</span> loses the batch session's cross-document
  prompt-cache amortization but also avoids its growing single-session context, so its token cost can land on either side of the
  batch no_skill arm (higher on FFIEC/ctgov, lower/flat on IRS/SEC). Credit cost (<span class="with">with_skill</span>) is billed
  per page and is unchanged by mode.</p>
  <table>
    <thead><tr><th>Dataset</th><th class="with">Token (with)</th><th class="with">Credit (with)</th><th class="with">Total (with)</th><th class="no">Total (no)</th><th>Total vs no</th><th>Pages</th><th class="with">Wall (with)</th><th class="no">Wall (no)</th></tr></thead>
    <tbody>
{pf_cost_tr}
    </tbody>
  </table>"""

    compare_section = ""
    cmp_tr = _compare_rows_html(rows, rows_pf) if rows_pf else ""
    if cmp_tr:
        compare_section = f"""
  <div class="section-label">Batch vs per-file</div>
  <p class="intro" style="margin-bottom:0.6rem">Headline numbers for the same dataset under the two execution modes
  (batch = one session per corpus → per-file = one session per document).</p>
  <table>
    <thead><tr><th>Dataset</th><th class="with">Acc with (batch→file)</th><th class="no">Acc no (batch→file)</th><th class="with">Total with (batch→file)</th><th class="no">Total no (batch→file)</th></tr></thead>
    <tbody>
{cmp_tr}
    </tbody>
  </table>"""

    links = "\n".join(
        f"<li><b>{_h.escape(r['name'])}</b> <span class='dim'>({_h.escape(r['src_label'])})</span> — "
        f"<a href='{r['src'].relative_to(RESULTS)}/report.html'>report.html</a></li>"
        for r in rows
    )
    stamp = f"Generated {date} · " if date else ""
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>Cross-Dataset Batch Extraction — Claude Code vs Claude Code + LlamaExtract Skill</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/fontsource/fonts/overused-grotesk@latest/latin.css" rel="stylesheet">
<style>
:root {{
  --bg:#FFFFFF; --bg-alt:#F5F5F5; --border:#E7E7E7; --text:#000; --text-dim:#737373;
  --purple:#3E18F9; --orange:#FF8705;
  --gradient-stroke:linear-gradient(180deg,#37D7FA 0%,#4B72FE 40%,#FF8DF2 68%,#FF8705 100%);
  --success:#1FA853; --danger:#E0344C;
}}
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{font-family:'Overused Grotesk',-apple-system,sans-serif;background:var(--bg);color:var(--text);line-height:1.55;}}
.hero{{text-align:center;padding:2.5rem 2rem 2rem;
  background:linear-gradient(180deg,rgba(150,231,249,0.08),rgba(146,174,255,0.06) 30%,rgba(255,191,248,0.04) 60%,transparent 85%);
  border-bottom:1px solid var(--border);position:relative;}}
.hero::after{{content:'';position:absolute;bottom:0;left:0;right:0;height:2px;background:var(--gradient-stroke);}}
.hero h1{{font-size:2.1rem;font-weight:500;letter-spacing:-0.03em;line-height:1.1;margin-bottom:0.4rem;}}
.hero h1 .accent{{background:var(--gradient-stroke);-webkit-background-clip:text;-webkit-text-fill-color:transparent;}}
.hero .subtitle{{color:var(--text-dim);font-size:0.95rem;max-width:760px;margin:0.3rem auto 0;}}
.container{{max-width:1100px;margin:0 auto;padding:2rem 1.5rem 4rem;}}
.section-label{{font-family:'IBM Plex Mono',monospace;font-size:0.7rem;font-weight:500;color:var(--text-dim);
  text-transform:uppercase;letter-spacing:0.04em;margin:2rem 0 0.9rem;padding-bottom:0.4rem;
  border-bottom:2px solid transparent;border-image:var(--gradient-stroke) 1;}}
table{{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;margin-bottom:1rem;}}
th,td{{padding:0.6rem 0.7rem;text-align:left;border-bottom:1px solid var(--border);vertical-align:top;}}
th{{font-family:'IBM Plex Mono',monospace;font-weight:500;font-size:0.72rem;text-transform:uppercase;color:var(--text-dim);}}
tbody tr:hover{{background:var(--bg-alt);}}
td.name{{font-weight:500;}}
td.with,th.with{{color:var(--purple);}}
td.no,th.no{{color:var(--orange);}}
td.why{{font-size:0.82rem;color:var(--text);max-width:420px;}}
.pos{{color:var(--success);font-weight:500;}} .neg{{color:var(--danger);font-weight:500;}} .dim{{color:var(--text-dim);}}
.findings li{{margin:0.5rem 0;font-size:0.9rem;}} .findings{{padding-left:1.1rem;}}
.links{{list-style:none;padding:0;}} .links li{{padding:0.35rem 0;border-bottom:1px dashed var(--border);font-size:0.9rem;}}
.links a{{color:var(--purple);text-decoration:none;font-family:'IBM Plex Mono',monospace;font-size:0.82rem;}}
.links a:hover{{text-decoration:underline;}}
.intro{{font-size:0.92rem;color:var(--text);max-width:860px;margin-bottom:0.5rem;}}
.intro .with{{color:var(--purple);font-weight:500;}} .intro .no{{color:var(--orange);font-weight:500;}}
code{{font-family:'IBM Plex Mono',monospace;font-size:0.85em;background:var(--bg-alt);padding:0.05rem 0.3rem;border-radius:3px;}}
footer{{text-align:center;padding:2rem 1rem;color:var(--text-dim);font-family:'IBM Plex Mono',monospace;font-size:0.7rem;}}
</style></head>
<body>
<div class="hero">
  <h1>Claude Code <span class="accent">vs</span> Claude Code + LlamaExtract Skill</h1>
  <div class="subtitle">Cross-dataset structured extraction — {len(rows)} document corpora, in batch (one session per corpus) and per-file (one session per document) modes.</div>
</div>
<div class="container">
  <p class="intro">Claude Code extracts a structured schema from a corpus of PDFs.
  <span class="with">with_skill</span> loads the <code>llama-extract</code> skill (delegates extraction to a LlamaCloud
  parse+extract job); <span class="no">no_skill</span> is the full agent without the <code>llama-extract</code> skill (Claude reads the PDFs directly).
  Model <code>claude-opus-4-7</code>. Numbers use the latest run per dataset (rerun where present).</p>

  <div class="section-label">Batch mode · accuracy</div>
  <table>
    <thead><tr><th>Dataset</th><th>N</th><th class="with">Acc (with)</th><th class="no">Acc (no)</th><th>Δ (with−no)</th><th>Why</th></tr></thead>
    <tbody>
{acc_tr}
    </tbody>
  </table>

  <div class="section-label">Batch mode · cost &amp; latency</div>
  <p class="intro" style="margin-bottom:0.6rem">Total cost = <span class="with">Claude tokens</span> + LlamaCloud <b>credits</b>
  (parse + extract, billed per page at $0.00125/credit). <span class="no">no_skill</span> never calls LlamaCloud, so its credit cost is $0 and its total equals its token cost.</p>
  <table>
    <thead><tr><th>Dataset</th><th class="with">Token (with)</th><th class="with">Credit (with)</th><th class="with">Total (with)</th><th class="no">Total (no)</th><th>Total vs no</th><th>Pages</th><th class="with">Wall cold (with)</th><th class="with">Wall warm (with)</th><th class="no">Wall (no)</th></tr></thead>
    <tbody>
{cost_tr}
    </tbody>
  </table>
{lat_section}
{perfile_section}
{compare_section}

  <div class="section-label">What holds across every dataset</div>
  <ul class="findings">
    <li><b>Cost: total = Claude tokens + LlamaCloud credits.</b> {_h.escape(cost_sentence)}</li>
    <li><b>Accuracy is deterministic and reproduces to the field.</b> The with-vs-no gap is set by whether correct extraction needs a <i>convention applied after reading</i> (SEC unit scaling) or <i>robust free-text matching</i> (ClinicalTrials) — things the delegated extractor doesn't do — versus face-value cell reads where it keeps pace (FFIEC, IRS).</li>
    <li><b>Wall time for with_skill is dominated by a variable LlamaCloud tail, not Claude.</b> <code>extract.py</code> uploads each PDF and polls a remote job. In a fast run that tail is ~15s and with_skill finishes in ~63–69s across datasets (4–7× faster than no_skill); under load the same runs took 196–805s. The Claude-side time is small and stable, so the swing is external I/O the cost meter never sees. For batch SLAs, watch that tail — not cost.</li>
  </ul>

  <div class="section-label">Per-dataset reports</div>
  <ul class="links">
{links}
  </ul>
</div>
<footer>{stamp}generated by <code>scripts/render_cross_dataset_summary.py</code> · with-skill loads <code>llama-extract</code> as a project skill, no-skill is the full agent without it</footer>
</body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=RESULTS / "cross_dataset_summary.md")
    parser.add_argument("--html-output", type=Path, default=RESULTS / "cross_dataset_summary.html")
    parser.add_argument("--date", type=str, default=None)
    args = parser.parse_args()

    rows: list[dict] = []      # batch mode
    rows_pf: list[dict] = []   # per-file mode
    for slug in REGISTRY:
        ds = get_dataset(slug)
        src, src_label = _source_dir(slug)
        inv_all = _load(src / "invocation_stats.json")
        latcw = _load(src / "latency_cold_warm.json")
        latb = (latcw.get("batch") or {}) if latcw else {}
        sb = _load(src / "summary_batch.json")
        if sb:
            rows.append(_row_from_summary(ds, slug, src, src_label, sb,
                                          inv_all.get("batch") or {}, latb))
        # Per-file summary lands in the same source dir (score.py --mode per_file with
        # --summary-out). Optional: datasets without a per-file run are simply omitted.
        sp = _load(src / "summary.json")
        if sp:
            rows_pf.append(_row_from_summary(ds, slug, src, src_label, sp,
                                             inv_all.get("v2") or {}, {}))

    cost_sentence = _cost_sentence(rows)

    L: list[str] = ["# Cross-Dataset Comparison — Batch & Per-File", ""]
    if args.date:
        L.append(f"Generated {args.date} by `scripts/render_cross_dataset_summary.py`.")
        L.append("")
    L += [
        "Claude Code extracting a structured schema from a corpus of PDFs. **with_skill** loads the "
        "`llama-extract` skill (delegates extraction to a LlamaCloud parse+extract job); **no_skill** is "
        "the full agent without the `llama-extract` skill (Claude reads the PDFs directly). Model "
        "`claude-opus-4-7`. Results are shown in two execution modes: **batch** (one session per corpus) "
        "and **per-file** (one session per document). Numbers use the latest run per dataset (rerun where present).",
        "",
        "## Batch mode",
        "",
        "### Accuracy",
        "",
    ]
    L += _acc_table_md(rows)
    L += [
        "",
        "### Cost & latency",
        "",
        "Total cost = Claude tokens + LlamaCloud credits (parse + extract, billed per page at "
        "$0.00125/credit). `no_skill` never calls LlamaCloud, so its credit cost is $0 and its total "
        "equals its token cost.",
        "",
    ]
    L += _cost_table_md(rows, with_latency=True)

    lat_rows = [r for r in rows if r.get("lat_cold") and r.get("lat_warm")]
    if lat_rows:
        L += [
            "",
            "### Parse-cache latency (cold vs warm)",
            "",
            "Same corpus and config — only the LlamaCloud parse cache differs. The parse step is "
            "cached by document content hash; a warm cache skips re-parsing. **Credit cost is identical "
            "per pass** (billed per page) — only latency changes. There is no API toggle to disable the "
            "cache on the extract path, and any change to parse options or tier busts it. The batch "
            "speedup below understates the isolated cache effect (batch parallelizes parses across Claude "
            "Task subagents and carries fixed orchestration overhead, and is noisy run-to-run); a per-file "
            "probe shows ~7×. See the dataset report for the breakdown.",
            "",
            "| Dataset | Cold wall | Warm wall | Speedup |",
            "|---|---|---|---|",
        ]
        for r in lat_rows:
            spd = f"{r['lat_speedup']:.1f}×" if r.get("lat_speedup") else "—"
            L.append(f"| {r['name']} | {_secs((r['lat_cold'] or 0)*1000)} | "
                     f"{_secs((r['lat_warm'] or 0)*1000)} | {spd} |")

    if rows_pf:
        L += [
            "",
            "## Per-file mode",
            "",
            "One claude session **per document** (vs one session over the whole corpus in batch mode). "
            "Per-file `no_skill` loses the batch session's cross-document prompt-cache amortization but also "
            "avoids its growing single-session context, so its token cost can land on either side of the "
            "batch no_skill arm (higher on FFIEC/ctgov, lower/flat on IRS/SEC). Credit cost (with_skill) is "
            "per-page and unchanged by mode.",
            "",
            "### Accuracy",
            "",
        ]
        L += _acc_table_md(rows_pf, why_col=False)
        L += ["", "### Cost", ""]
        L += _cost_table_md(rows_pf, with_latency=False)

        cmp_lines = _compare_table_md(rows, rows_pf)
        if len(cmp_lines) > 2:  # header + separator + ≥1 data row
            L += [
                "",
                "## Batch vs per-file",
                "",
                "Headline numbers for the same dataset under the two execution modes "
                "(batch = one session per corpus → per-file = one session per document).",
                "",
            ]
            L += cmp_lines

    L += [
        "",
        "## What holds across every dataset",
        "",
        f"- **Cost: total = Claude tokens + LlamaCloud credits.** {cost_sentence}",
        "- **Accuracy is deterministic and reproduces to the field.** The with-vs-no gap is set by "
        "whether correct extraction needs a *convention applied after reading* (SEC unit scaling) or "
        "*robust free-text matching* (ClinicalTrials) — things the delegated extractor doesn't do — "
        "versus face-value cell reads where it keeps pace (FFIEC, IRS).",
        "- **Wall time for with_skill is dominated by a variable LlamaCloud tail, not Claude.** "
        "`extract.py` uploads each PDF and polls a remote job. In a fast run that tail is ~15s and "
        "with_skill finishes in ~63–69s across datasets (4–7× faster than no_skill); when LlamaCloud "
        "is under load the same runs took 196–805s. The Claude-side time is small and stable, so the "
        "swing is external I/O the cost meter never sees. For batch SLAs, watch that tail — not cost.",
        "",
        "## Per-dataset reports",
        "",
    ]
    for r in rows:
        rel = r["src"].relative_to(RESULTS)
        L.append(f"- **{r['name']}** ({r['src_label']}): [`{rel}/report.html`]({rel}/report.html)")
    L.append("")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(L))
    args.html_output.write_text(render_html(rows, rows_pf, args.date, cost_sentence))
    print(f"Wrote {args.output} and {args.html_output} "
          f"({len(rows)} batch, {len(rows_pf)} per-file)")


if __name__ == "__main__":
    main()
