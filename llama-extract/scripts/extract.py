#!/usr/bin/env python3
"""LlamaExtract — bundled CLI for the llama-extract skill.

Runs schema-driven structured extraction against one document and writes the result as JSON.
The CLI handles upload, extract job submission, polling, and result writing — the same flow
the SKILL.md documents inline, but as a reusable executable.

Usage:
  python extract.py \\
      --input ./document.pdf \\
      --schema ./schema.json \\
      --output ./output.json \\
      [--tier {cost_effective,agentic}] \\
      [--parse-tier {fast,cost_effective,agentic,agentic_plus}] \\
      [--system-prompt "..."] \\
      [--target-pages "1,3,5-7"] \\
      [--max-pages N] \\
      [--extraction-target {per_doc,per_page,per_table_row}] \\
      [--cite-sources] \\
      [--confidence-scores] \\
      [--polling-timeout 1800] \\
      [--verbose]

Schema file format: a JSON object that is a valid LlamaExtract data_schema (i.e. a JSON
Schema with root type=object). Pydantic users should dump
`MyModel.model_json_schema()` to a file first.

Exits 0 on success; non-zero on any failure. Status / cost info goes to stderr. With
--verbose, also prints the final output path to stdout.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run LlamaExtract on one document.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--input", required=True, type=Path, help="Path to the input document (PDF, DOCX, image, etc.)")
    p.add_argument("--schema", required=True, type=Path, help="Path to a JSON Schema file.")
    p.add_argument("--output", required=True, type=Path, help="Path where output JSON will be written.")
    p.add_argument("--tier", choices=["cost_effective", "agentic"], default="agentic",
                   help="Extraction tier (default: agentic, 15 credits/page).")
    p.add_argument("--parse-tier", dest="parse_tier",
                   choices=["fast", "cost_effective", "agentic", "agentic_plus"], default="agentic",
                   help="Parse tier (default: agentic, 10 credits/page).")
    p.add_argument("--system-prompt", dest="system_prompt", default=None,
                   help="Cross-cutting extraction rules (units, locale, null handling).")
    p.add_argument("--target-pages", dest="target_pages", default=None,
                   help="Restrict to specific pages, e.g. '1,3,5-7'. 1-indexed.")
    p.add_argument("--max-pages", dest="max_pages", type=int, default=None,
                   help="Hard cap on pages parsed.")
    p.add_argument("--extraction-target", dest="extraction_target",
                   choices=["per_doc", "per_page", "per_table_row"], default="per_doc")
    p.add_argument("--cite-sources", dest="cite_sources", action="store_true",
                   help="Populate per-field source citations (slower).")
    p.add_argument("--confidence-scores", dest="confidence_scores", action="store_true",
                   help="Populate per-field confidence (slower; doc must be <= 100 pages).")
    p.add_argument("--polling-timeout", dest="polling_timeout", type=float, default=1800.0,
                   help="Max seconds to wait for the extract job (default: 1800).")
    p.add_argument("--polling-interval", dest="polling_interval", type=float, default=2.0,
                   help="Seconds between polling checks (default: 2.0).")
    p.add_argument("--max-interval", dest="max_interval", type=float, default=8.0,
                   help="Max polling interval after backoff (default: 8.0).")
    p.add_argument("--verbose", action="store_true",
                   help="Log progress to stderr; print output path to stdout on success.")
    return p.parse_args()


def log(msg: str, verbose: bool) -> None:
    if verbose:
        print(msg, file=sys.stderr)


def main() -> int:
    args = parse_args()

    if not os.environ.get("LLAMA_CLOUD_API_KEY"):
        print("ERROR: LLAMA_CLOUD_API_KEY is not set", file=sys.stderr)
        return 2

    if not args.input.exists():
        print(f"ERROR: input file not found: {args.input}", file=sys.stderr)
        return 2

    if not args.schema.exists():
        print(f"ERROR: schema file not found: {args.schema}", file=sys.stderr)
        return 2

    try:
        schema = json.loads(args.schema.read_text())
    except json.JSONDecodeError as e:
        print(f"ERROR: schema is not valid JSON: {e}", file=sys.stderr)
        return 2

    if not isinstance(schema, dict) or schema.get("type") != "object":
        print("ERROR: schema must be a JSON object with root type='object'", file=sys.stderr)
        return 2

    try:
        from llama_cloud import LlamaCloud
    except ImportError:
        print("ERROR: llama_cloud not installed. Run: pip install 'llama-cloud>=2.7'", file=sys.stderr)
        return 2

    client = LlamaCloud()

    log(f"[llama-extract] uploading {args.input}", args.verbose)
    with open(args.input, "rb") as f:
        file_obj = client.files.create(file=f, purpose="extract")
    log(f"[llama-extract] uploaded as {file_obj.id}", args.verbose)

    configuration: dict = {
        "data_schema": schema,
        "extraction_target": args.extraction_target,
        "tier": args.tier,
        "parse_tier": args.parse_tier,
        "cite_sources": args.cite_sources,
        "confidence_scores": args.confidence_scores,
    }
    if args.system_prompt:
        configuration["system_prompt"] = args.system_prompt
    if args.target_pages:
        configuration["target_pages"] = args.target_pages
    if args.max_pages:
        configuration["max_pages"] = args.max_pages

    log(
        f"[llama-extract] starting extract job (tier={args.tier}, parse_tier={args.parse_tier}, "
        f"target={args.extraction_target}, cite={args.cite_sources}, confidence={args.confidence_scores})",
        args.verbose,
    )
    job = client.extract.run(
        file_input=file_obj.id,
        configuration=configuration,
        polling_interval=args.polling_interval,
        max_interval=args.max_interval,
        polling_timeout=args.polling_timeout,
        verbose=args.verbose,
    )

    status = getattr(job, "status", None)
    log(f"[llama-extract] terminal status: {status}", args.verbose)
    if status not in ("SUCCESS", "COMPLETED"):
        err = getattr(job, "error_message", None) or status
        print(f"ERROR: extract job failed: {err}", file=sys.stderr)
        return 1

    data = None
    for attr in ("extract_result", "result", "data"):
        v = getattr(job, attr, None)
        if v:
            data = v
            break
    if data is None:
        print(f"ERROR: no result on completed job: {job}", file=sys.stderr)
        return 1

    # Some SDK versions wrap the payload in {"data": ...}; unwrap if obvious
    if isinstance(data, dict) and "data" in data and len(data) == 1:
        data = data["data"]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2))
    log(f"[llama-extract] wrote {args.output} ({args.output.stat().st_size} bytes)", args.verbose)

    if args.verbose:
        print(str(args.output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
