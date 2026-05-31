"""Per-file LlamaExtract latency probe.

Isolates the LlamaExtract *job* wall time (upload + parse + extract) from Claude Code
orchestration, so we can characterize per-file latency and the effect of the parse
cache (warm vs cold).

Cache control: the extract API exposes NO working cache flag. ExtractConfiguration has
no cache field, and the documented hook (parse_config_id -> a saved parse config with
invalidate_cache/do_not_cache) 404s on extract jobs in this SDK/server. The parse step
is cached by document CONTENT hash. So:
  - WARM uploads the original bytes -> content hash matches a prior parse -> cache hit.
  - COLD uploads a re-serialized copy (pypdf rewrite) -> new content hash -> never seen
    -> genuine cold parse. This is a faithful proxy for the real cold case (the first
    time a document is ever processed).

Usage:
  python -m scripts.extract_latency_probe --dataset ffiec_call_reports \
      --cache-mode warm --limit 3 --out results/latency/ffiec_warm.json

Cost: every run bills LlamaExtract credits per page (warm-cache billing is undocumented).
Use --limit / --doc-keys to pilot cheaply before running a full dataset.
"""
from __future__ import annotations

import argparse
import io
import json
import time
from pathlib import Path
from typing import Any

import pypdf

from scripts.datasets import all_slugs, get_dataset


def load_schema(ds, slug: str) -> dict:
    """Prefer the exact schema staged for the batch run; fall back to the model schema."""
    staged = Path("runs_batch") / slug / "with_skill" / "schema.json"
    if staged.exists():
        try:
            return json.loads(staged.read_text())
        except json.JSONDecodeError:
            pass
    return ds.schema_cls.model_json_schema()


def local_pages(pdf_path: Path) -> int | None:
    try:
        return len(pypdf.PdfReader(str(pdf_path)).pages)
    except Exception:
        return None


def cold_pdf_bytes(pdf_path: Path, tag: str) -> bytes:
    """Re-serialize a PDF so its content hash differs from the original (and from other
    tags), forcing a cold parse. Page content is preserved; only bytes/metadata change."""
    reader = pypdf.PdfReader(str(pdf_path))
    writer = pypdf.PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.add_metadata({"/Producer": f"latency-probe-cold-{tag}"})
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", choices=all_slugs(), required=True)
    ap.add_argument("--cache-mode", choices=["warm", "cold"], default="warm")
    ap.add_argument("--limit", type=int, default=None, help="Only the first N manifest docs.")
    ap.add_argument("--doc-keys", default=None, help="Comma-separated doc_keys to restrict to.")
    ap.add_argument("--extract-tier", default="agentic", choices=["cost_effective", "agentic"])
    ap.add_argument("--parse-tier", default="agentic",
                    choices=["fast", "cost_effective", "agentic", "agentic_plus"])
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--label", default=None, help="Free-text label recorded in the output.")
    args = ap.parse_args()

    from llama_cloud import LlamaCloud
    client = LlamaCloud()

    ds = get_dataset(args.dataset)
    schema = load_schema(ds, args.dataset)
    records = json.loads(ds.manifest_path().read_text())
    if args.doc_keys:
        wanted = set(args.doc_keys.split(","))
        records = [r for r in records if ds.doc_key_fn(r) in wanted]
    if args.limit:
        records = records[: args.limit]

    cold_tag = args.label or "default"
    print(f"[probe] dataset={args.dataset} cache_mode={args.cache_mode} "
          f"docs={len(records)} extract={args.extract_tier} parse={args.parse_tier}"
          + (f" cold_tag={cold_tag}" if args.cache_mode == "cold" else " (original bytes)"))

    rows: list[dict[str, Any]] = []
    for i, rec in enumerate(records, 1):
        doc_key = ds.doc_key_fn(rec)
        pdf = ds.pdf_path(rec)
        pages = local_pages(pdf)
        if not pdf.exists():
            print(f"  [{i}/{len(records)}] {doc_key}: MISSING pdf, skipping")
            continue
        configuration: dict[str, Any] = {
            "data_schema": schema,
            "extraction_target": "per_doc",
            "tier": args.extract_tier,
            "parse_tier": args.parse_tier,
            "cite_sources": False,
            "confidence_scores": False,
        }

        t0 = time.perf_counter()
        status = "ERROR"
        job_id = None
        err = None
        try:
            if args.cache_mode == "cold":
                data = cold_pdf_bytes(pdf, cold_tag)
                fo = client.files.create(file=(f"{doc_key}.pdf", data, "application/pdf"),
                                         purpose="extract")
            else:
                with open(pdf, "rb") as f:
                    fo = client.files.create(file=f, purpose="extract")
            t_upload = time.perf_counter()
            job = client.extract.run(file_input=fo.id, configuration=configuration)
            status = getattr(job, "status", "?")
            job_id = getattr(job, "id", None)
        except Exception as e:  # noqa: BLE001
            err = repr(e)[:300]
            t_upload = time.perf_counter()
        t1 = time.perf_counter()
        rows.append({
            "doc_key": doc_key,
            "pages": pages,
            "upload_s": round(t_upload - t0, 2),
            "extract_s": round(t1 - t_upload, 2),
            "wall_s": round(t1 - t0, 2),
            "status": status,
            "job_id": job_id,
            "error": err,
        })
        per_page = (rows[-1]["wall_s"] / pages) if pages else None
        print(f"  [{i}/{len(records)}] {doc_key}: {rows[-1]['wall_s']}s "
              f"({pages} pg{f', {per_page:.2f}s/pg' if per_page else ''}) "
              f"upload={rows[-1]['upload_s']}s status={status}"
              + (f" ERR={err}" if err else ""))

    walls = [r["wall_s"] for r in rows if r["status"] in ("COMPLETED", "SUCCESS")]
    total_pages = sum(r["pages"] or 0 for r in rows)
    summary = {
        "dataset": args.dataset,
        "cache_mode": args.cache_mode,
        "extract_tier": args.extract_tier,
        "parse_tier": args.parse_tier,
        "cold_method": "pypdf-reserialize (new content hash)" if args.cache_mode == "cold" else None,
        "label": args.label,
        "n_docs": len(rows),
        "n_ok": len(walls),
        "total_pages": total_pages,
        "wall_sum_s": round(sum(walls), 1) if walls else None,
        "wall_min_s": round(min(walls), 1) if walls else None,
        "wall_median_s": round(sorted(walls)[len(walls) // 2], 1) if walls else None,
        "wall_max_s": round(max(walls), 1) if walls else None,
        "rows": rows,
    }
    print(f"[probe] {args.cache_mode}: n_ok={summary['n_ok']}/{summary['n_docs']} "
          f"total_pages={total_pages} wall_sum={summary['wall_sum_s']}s "
          f"min/median/max={summary['wall_min_s']}/{summary['wall_median_s']}/{summary['wall_max_s']}s")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(summary, indent=2))
        print(f"[probe] wrote {args.out}")


if __name__ == "__main__":
    main()
