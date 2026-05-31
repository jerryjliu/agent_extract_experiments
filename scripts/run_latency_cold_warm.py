"""Controlled cold-vs-warm parse-cache latency experiment for one dataset (batch mode).

Runs the Claude Code with_skill BATCH twice over the SAME files, differing only by parse
cache state:
  - COLD: re-serialized PDFs (new content hash) -> never-parsed -> cold parse.
  - WARM: the same re-serialized PDFs again -> served from the parse cache.

Uses BENCH_PDF_DIR to point the benchmark at the re-serialized copies, so the source
data under data/<slug>/pdfs is never touched. Captures each session's end-to-end wall
and cost, folds in the per-file probe pilot if present, and writes
results/<slug>_rerun_2026-05-30/latency_cold_warm.json for the renderers.

Usage: python -m scripts.run_latency_cold_warm --dataset ffiec_call_reports
Cost: two full with_skill batch passes (credits per page x2).
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pypdf

from scripts import pricing
from scripts.datasets import all_slugs, get_dataset


def make_cold_pdfs(ds, records, out_dir: Path) -> int:
    """Write re-serialized <doc_key>.pdf copies (new content hash) into out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    total_pages = 0
    for rec in records:
        key = ds.doc_key_fn(rec)
        src = ds.pdf_path(rec)
        if not src.exists():
            print(f"  WARN: missing {src}, skipping")
            continue
        reader = pypdf.PdfReader(str(src))
        writer = pypdf.PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.add_metadata({"/Producer": f"cold-cache-probe-{key}"})
        buf = io.BytesIO()
        writer.write(buf)
        (out_dir / f"{key}.pdf").write_bytes(buf.getvalue())
        total_pages += len(reader.pages)
    return total_pages


def run_batch_capture(slug: str, cold_pdf_dir: Path, label: str) -> dict:
    """Run one with_skill batch (agentic) against cold_pdf_dir; return its session stats."""
    env = dict(os.environ)
    env["BENCH_PDF_DIR"] = str(cold_pdf_dir)
    env["PYTHONPATH"] = "."
    print(f"\n######## batch pass: {label} (BENCH_PDF_DIR={cold_pdf_dir}) ########")
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.run_benchmark", "--mode", "batch",
         "--dataset", slug, "--condition", "with_skill"],
        env=env, check=False,
    )
    sr_path = Path("runs_batch") / slug / "with_skill" / "session_result.json"
    sr = json.loads(sr_path.read_text()) if sr_path.exists() else {}
    return {
        "label": label,
        "exit_code": proc.returncode,
        "wall_s": sr.get("wall_seconds_session"),
        "token_cost_usd": sr.get("total_cost_usd"),
        "num_turns": sr.get("num_turns"),
        "extract_tier": sr.get("extract_tier"),
        "parse_tier": sr.get("parse_tier"),
    }


def score_pass(slug: str, summary_out: Path) -> dict:
    """Score the current with_skill batch artifacts into summary_out. Run with a CLEAN
    env (no BENCH_PDF_DIR) so page counts come from the real source PDFs."""
    env = {k: v for k, v in os.environ.items() if k != "BENCH_PDF_DIR"}
    env["PYTHONPATH"] = "."
    csv_out = Path("/tmp") / f"score_{slug}_pass.csv"
    subprocess.run(
        [sys.executable, "-m", "scripts.score", "--mode", "batch", "--dataset", slug,
         "--csv-out", str(csv_out), "--summary-out", str(summary_out)],
        env=env, check=False,
    )
    return json.loads(summary_out.read_text()) if summary_out.exists() else {}


def load_pilot(slug: str) -> dict:
    """Fold in per-file probe pilot results if present (results/latency/<slug>_pilot_*.json)."""
    out = {}
    for mode in ("cold", "warm"):
        p = Path("results/latency") / f"{slug.split('_')[0]}_pilot_{mode}.json"
        # also try the full slug prefix
        if not p.exists():
            p = Path("results/latency") / f"{slug}_pilot_{mode}.json"
        if p.exists():
            try:
                out[mode] = json.loads(p.read_text())
            except json.JSONDecodeError:
                pass
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", choices=all_slugs(), required=True)
    ap.add_argument("--results-dir", type=Path, default=None,
                    help="Where to write latency_cold_warm.json (default: the 2026-05-30 rerun dir).")
    ap.add_argument("--cold-pdf-dir", type=Path, default=None)
    args = ap.parse_args()

    ds = get_dataset(args.dataset)
    records = json.loads(ds.manifest_path().read_text())
    results_dir = args.results_dir or Path("results") / f"{args.dataset}_rerun_2026-05-30"
    results_dir.mkdir(parents=True, exist_ok=True)
    cold_dir = args.cold_pdf_dir or Path("/tmp") / f"{args.dataset}_cold_pdfs"
    canonical_summary = Path("results") / args.dataset / "run_summaries_batch.json"

    print(f"[latency] {args.dataset}: generating {len(records)} re-serialized cold PDFs -> {cold_dir}")
    total_pages = make_cold_pdfs(ds, records, cold_dir)
    credit = round(pricing.credit_cost_usd(total_pages, "agentic", "agentic") or 0.0, 2)
    print(f"[latency] {total_pages} pages; est credit per pass ${credit:.2f} (x2 passes)")

    cold_summary, warm_summary = {}, {}
    try:
        cold = run_batch_capture(args.dataset, cold_dir, "cold")
        # Score the cold pass BEFORE the warm pass overwrites the fanout outputs.
        cold_summary = score_pass(args.dataset, results_dir / "summary_batch_cold.json")
        warm = run_batch_capture(args.dataset, cold_dir, "warm")
        warm_summary = score_pass(args.dataset, results_dir / "summary_batch_warm.json")
    finally:
        # The benchmark overwrites the canonical run summary; restore it so the
        # original baseline stays untouched (rerun outputs live in the _rerun dir).
        if canonical_summary.exists():
            subprocess.run(["git", "checkout", "--", str(canonical_summary)], check=False)

    # Fold the scored accuracy/credit/total into each pass for the report's table.
    for pass_stats, summ in ((cold, cold_summary), (warm, warm_summary)):
        ws = (summ.get("with_skill") or {}) if summ else {}
        pass_stats["accuracy"] = ws.get("accuracy")
        pass_stats["n_correct"] = ws.get("n_correct")
        pass_stats["n_wrong"] = ws.get("n_wrong")
        pass_stats["n_missing"] = ws.get("n_missing")
        pass_stats["credit_cost_usd"] = ws.get("credit_cost_usd")
        pass_stats["total_cost_usd"] = ws.get("total_cost_usd")

    speedup = (cold["wall_s"] / warm["wall_s"]) if (cold["wall_s"] and warm["wall_s"]) else None
    out = {
        "dataset": args.dataset,
        "n_docs": len(records),
        "total_pages": total_pages,
        "credit_cost_usd_per_pass": credit,
        "batch": {
            "cold": cold,
            "warm": warm,
            "speedup": round(speedup, 2) if speedup else None,
        },
        "per_file_pilot": load_pilot(args.dataset),
        "note": ("Same re-serialized files both passes; cold = first parse (new content "
                 "hash), warm = parse-cache hit. Credit cost is identical per pass (billed "
                 "per page); only latency differs."),
    }
    out_path = results_dir / "latency_cold_warm.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\n[latency] cold wall={cold['wall_s']}s  warm wall={warm['wall_s']}s  "
          f"speedup={out['batch']['speedup']}x")
    print(f"[latency] wrote {out_path}")


if __name__ == "__main__":
    main()
