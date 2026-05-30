"""Benchmark orchestrator: run claude -p in both conditions across a dataset.

Two modes:

- per_file (default): one claude -p session per (record, condition) pair. Stages
  runs/<slug>/<doc_key>_<condition>/ with input.pdf and (for with-skill)
  .claude/skills/llama-extract/, shells out, captures trace.jsonl + result.json.
- batch: one claude -p session per condition over ALL records. Stages
  runs_batch/<slug>/<condition>/ with inputs/<doc_key>.pdf symlinks, outputs/
  (empty), schema.json, and (for with-skill) .claude/skills/llama-extract/. A
  single session writes outputs/<doc_key>.json per document; the fanout step
  re-stages those into runs_batch/<slug>/<doc_key>_<condition>/ for the scorer.

Run:
  python scripts/run_benchmark.py                                  # ffiec, per-file, all x 2
  python scripts/run_benchmark.py --dataset ctgov_protocols --mode batch
  python scripts/run_benchmark.py --mode batch --limit 2           # batch smoke (first 2 PDFs)
  python scripts/run_benchmark.py --mode batch --condition with_skill
  python scripts/run_benchmark.py --mode batch --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from scripts.datasets import DEFAULT_SLUG, all_slugs, get_dataset
from scripts.datasets.base import DatasetConfig
from scripts.prompt import (
    SYSTEM_PROMPT_APPEND,
    SYSTEM_PROMPT_APPEND_BATCH,
    build_batch_prompt,
    build_extraction_prompt,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
LLAMA_EXTRACT_SKILL = REPO_ROOT / "llama-extract"
EMPTY_MCP_CONFIG = REPO_ROOT / "scripts" / "empty_mcp_config.json"
CONDITIONS = ("with_skill", "no_skill")
MODEL = "claude-opus-4-7"


def _common_flags(condition: str, system_prompt: str) -> list[str]:
    common = [
        "claude",
        "--print",
        "--model", MODEL,
        "--permission-mode", "bypassPermissions",
        "--output-format", "stream-json",
        "--verbose",
        "--append-system-prompt", system_prompt,
        "--no-session-persistence",
        "--strict-mcp-config",
        "--mcp-config", str(EMPTY_MCP_CONFIG),
        "--disallowedTools", "WebFetch WebSearch",
    ]
    if condition == "with_skill":
        common += ["--setting-sources", "project,local"]
    elif condition == "no_skill":
        common += ["--bare"]
    else:
        raise ValueError(f"unknown condition: {condition}")
    return common


def resolve_command(dataset: DatasetConfig, condition: str) -> list[str]:
    """Build the claude -p invocation flags for a given (per-file) condition."""
    cmd = _common_flags(condition, SYSTEM_PROMPT_APPEND)
    cmd.append(build_extraction_prompt(dataset))
    return cmd


def resolve_command_batch(dataset: DatasetConfig, condition: str, doc_keys: list[str]) -> list[str]:
    """Build the claude -p invocation flags for a batch-mode condition."""
    cmd = _common_flags(condition, SYSTEM_PROMPT_APPEND_BATCH)
    cmd.append(build_batch_prompt(dataset, doc_keys))
    return cmd


def stage_run_dir(run_dir: Path, pdf_path: Path, condition: str) -> None:
    """Build the per-run working directory."""
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)
    shutil.copy(pdf_path, run_dir / "input.pdf")
    if condition == "with_skill":
        target = run_dir / ".claude" / "skills" / "llama-extract"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(LLAMA_EXTRACT_SKILL, target)


def stage_batch_dir(dataset: DatasetConfig, condition: str, records: list[dict[str, Any]]) -> Path:
    """Build the per-condition batch working directory.

    Layout:
      runs_batch/<slug>/<condition>/
        inputs/<doc_key>.pdf    # symlink to data/<slug>/pdfs/<doc_key>.pdf
        outputs/                # empty; Claude writes here
        schema.json             # pre-staged JSON Schema dump
        .claude/skills/...      # with-skill only
    """
    run_dir = (REPO_ROOT / dataset.batch_session_dir(condition)).resolve()
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)

    inputs_dir = run_dir / "inputs"
    inputs_dir.mkdir()
    for record in records:
        doc_key = dataset.doc_key_fn(record)
        src = (REPO_ROOT / dataset.pdf_path(record)).resolve()
        if not src.exists():
            raise FileNotFoundError(f"PDF not found for symlink: {src}")
        (inputs_dir / f"{doc_key}.pdf").symlink_to(src)

    (run_dir / "outputs").mkdir()

    schema_path = run_dir / "schema.json"
    schema_path.write_text(json.dumps(dataset.schema_cls.model_json_schema(), indent=2))

    if condition == "with_skill":
        target = run_dir / ".claude" / "skills" / "llama-extract"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(LLAMA_EXTRACT_SKILL, target)

    return run_dir


def parse_trace(trace_path: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return (init_event, result_event) from a stream-json trace file."""
    init_event = None
    result_event = None
    with trace_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if init_event is None and event.get("type") == "system" and event.get("subtype") == "init":
                init_event = event
            if event.get("type") == "result":
                result_event = event
    return init_event, result_event


def verify_ab(init_event: dict[str, Any] | None, condition: str) -> str | None:
    """Return error string if A/B is contaminated, else None."""
    if init_event is None:
        return "no system/init event found"
    skills = init_event.get("skills") or []
    if condition == "with_skill" and "llama-extract" not in skills:
        return f"with_skill condition but llama-extract not loaded (skills={skills})"
    if condition == "no_skill" and "llama-extract" in skills:
        return f"no_skill condition but llama-extract IS loaded (skills={skills})"
    return None


def run_one(dataset: DatasetConfig, record: dict[str, Any], condition: str,
            dry_run: bool = False) -> dict[str, Any]:
    """Execute one (record, condition) run; return a summary dict."""
    doc_key = dataset.doc_key_fn(record)
    pdf_path = dataset.pdf_path(record)
    if not pdf_path.exists():
        return {"doc_key": doc_key, "condition": condition, "error": f"PDF not found: {pdf_path}"}

    run_dir = dataset.run_dir(record, condition)
    cmd = resolve_command(dataset, condition)

    if dry_run:
        # Do NOT call stage_run_dir in dry-run — staging is destructive (rmtree).
        print(f"[DRY] {doc_key} {condition} cwd={run_dir}")
        print(f"      cmd={' '.join(cmd[:8])} ... (prompt length {len(cmd[-1])} chars)")
        return {"doc_key": doc_key, "condition": condition, "dry_run": True}

    stage_run_dir(run_dir, pdf_path, condition)

    trace_path = run_dir / "trace.jsonl"
    stderr_path = run_dir / "stderr.log"

    t0 = time.time()
    with trace_path.open("wb") as out, stderr_path.open("wb") as err:
        proc = subprocess.run(cmd, cwd=run_dir, stdout=out, stderr=err, check=False)
    wall_seconds = time.time() - t0

    init_event, result_event = parse_trace(trace_path)
    ab_error = verify_ab(init_event, condition)

    summary = {
        "doc_key": doc_key,
        "name": record.get("name"),
        "condition": condition,
        "exit_code": proc.returncode,
        "wall_seconds": round(wall_seconds, 2),
        "ab_error": ab_error,
        "output_present": (run_dir / "output.json").exists(),
        "skills_loaded": init_event.get("skills") if init_event else None,
    }
    if result_event is not None:
        summary.update({
            "total_cost_usd": result_event.get("total_cost_usd"),
            "duration_ms": result_event.get("duration_ms"),
            "duration_api_ms": result_event.get("duration_api_ms"),
            "num_turns": result_event.get("num_turns"),
            "stop_reason": result_event.get("stop_reason"),
        })
        (run_dir / "result.json").write_text(json.dumps(result_event, indent=2))
    return summary


def fanout_outputs(dataset: DatasetConfig, condition: str, records: list[dict[str, Any]],
                   session_dir: Path) -> dict[str, bool]:
    """Re-stage runs_batch/<slug>/<cond>/outputs/<key>.json into per-doc dirs the scorer reads.

    Returns: {doc_key: output_present}
    """
    presence: dict[str, bool] = {}
    outputs_dir = session_dir / "outputs"
    session_trace = session_dir / "trace.jsonl"
    for record in records:
        doc_key = dataset.doc_key_fn(record)
        src = outputs_dir / f"{doc_key}.json"
        per_doc_dir = REPO_ROOT / dataset.batch_fanout_dir(record, condition)
        if per_doc_dir.exists():
            shutil.rmtree(per_doc_dir)
        per_doc_dir.mkdir(parents=True)
        if src.exists():
            shutil.copy(src, per_doc_dir / "output.json")
            presence[doc_key] = True
        else:
            presence[doc_key] = False
        if session_trace.exists():
            shutil.copy(session_trace, per_doc_dir / "trace.jsonl")
    return presence


def run_batch(dataset: DatasetConfig, records: list[dict[str, Any]], condition: str,
              dry_run: bool = False) -> dict[str, Any]:
    """Execute one batch session for `condition` over all `records`; return summary."""
    if not records:
        return {"condition": condition, "error": "no records"}

    doc_keys = [dataset.doc_key_fn(r) for r in records]
    cmd = resolve_command_batch(dataset, condition, doc_keys)

    if dry_run:
        # Do NOT call stage_batch_dir in dry-run — staging is destructive (rmtree).
        intended_dir = (REPO_ROOT / dataset.batch_session_dir(condition)).resolve()
        rel = intended_dir.relative_to(REPO_ROOT)
        print(f"[DRY] batch {condition} cwd={rel} (not staged)")
        print(f"      cmd={' '.join(cmd[:8])} ... (prompt length {len(cmd[-1])} chars)")
        print(f"      n_inputs={len(doc_keys)}  schema={rel}/schema.json")
        for record in records:
            src = (REPO_ROOT / dataset.pdf_path(record)).resolve()
            try:
                src_rel = src.relative_to(REPO_ROOT)
            except ValueError:
                src_rel = src
            print(f"        inputs/{dataset.doc_key_fn(record)}.pdf -> {src_rel}")
        if condition == "with_skill":
            print(f"      skill_staged={rel}/.claude/skills/llama-extract")
        return {"condition": condition, "n_records": len(doc_keys), "dry_run": True}

    session_dir = stage_batch_dir(dataset, condition, records)

    trace_path = session_dir / "trace.jsonl"
    stderr_path = session_dir / "stderr.log"

    t0 = time.time()
    with trace_path.open("wb") as out, stderr_path.open("wb") as err:
        proc = subprocess.run(cmd, cwd=session_dir, stdout=out, stderr=err, check=False)
    wall_seconds = time.time() - t0

    init_event, result_event = parse_trace(trace_path)
    ab_error = verify_ab(init_event, condition)

    if result_event is not None:
        # wall_seconds_session is the true end-to-end wall the orchestrator measured.
        # duration_ms inside the result event reflects only the final continuation
        # phase when Claude uses the Task tool to launch background subagents.
        enriched = dict(result_event)
        enriched["wall_seconds_session"] = round(wall_seconds, 2)
        (session_dir / "session_result.json").write_text(json.dumps(enriched, indent=2))

    presence = fanout_outputs(dataset, condition, records, session_dir)

    summary: dict[str, Any] = {
        "mode": "batch",
        "condition": condition,
        "n_records": len(records),
        "exit_code": proc.returncode,
        "wall_seconds": round(wall_seconds, 2),
        "ab_error": ab_error,
        "skills_loaded": init_event.get("skills") if init_event else None,
        "n_outputs_present": sum(1 for v in presence.values() if v),
        "missing_outputs": [k for k, v in presence.items() if not v],
    }
    if result_event is not None:
        summary.update({
            "total_cost_usd": result_event.get("total_cost_usd"),
            "duration_ms": result_event.get("duration_ms"),
            "duration_api_ms": result_event.get("duration_api_ms"),
            "num_turns": result_event.get("num_turns"),
            "stop_reason": result_event.get("stop_reason"),
        })
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=all_slugs(), default=DEFAULT_SLUG)
    parser.add_argument("--manifest", type=Path, default=None,
                        help="Override manifest path (defaults to the dataset's manifest).")
    parser.add_argument("--limit", type=int, default=None, help="Run only first N records.")
    parser.add_argument("--filing", type=str, default=None,
                        help="Single record's doc_key (per_file mode only).")
    parser.add_argument(
        "--condition",
        choices=["with_skill", "no_skill", "both"],
        default="both",
    )
    parser.add_argument("--mode", choices=["per_file", "batch"], default="per_file",
                        help="per_file = one claude -p per (record, condition); "
                             "batch = one claude -p per condition over the corpus.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    dataset = get_dataset(args.dataset)

    if args.mode == "batch" and args.filing is not None:
        print("ERROR: --filing is per-file only; batch mode is corpus-level by definition. "
              "Use --limit N to restrict the corpus instead.", file=sys.stderr)
        sys.exit(2)

    manifest_path = args.manifest or dataset.manifest_path()
    records = json.loads(manifest_path.read_text())
    if args.filing:
        records = [r for r in records if dataset.doc_key_fn(r) == args.filing
                   or str(r.get("rssd")) == args.filing]
        if not records:
            print(f"No record found with key {args.filing}", file=sys.stderr)
            sys.exit(1)
    if args.limit:
        records = records[: args.limit]

    conditions = CONDITIONS if args.condition == "both" else (args.condition,)

    if not args.dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        print("Warning: ANTHROPIC_API_KEY not set — claude may use stored credentials")

    summaries: list[dict[str, Any]] = []
    results_dir = dataset.results_dir()

    if args.mode == "per_file":
        for record in records:
            for cond in conditions:
                print(f"=== {record.get('name', dataset.doc_key_fn(record))} | {cond} ===")
                s = run_one(dataset, record, cond, dry_run=args.dry_run)
                summaries.append(s)
                if "error" in s:
                    print(f"  ERROR: {s['error']}")
                elif args.dry_run:
                    pass
                else:
                    cost = s.get("total_cost_usd")
                    dur = s.get("wall_seconds")
                    ab = s.get("ab_error") or "ok"
                    out = "yes" if s.get("output_present") else "no"
                    cost_str = f"${cost:.4f}" if cost is not None else "n/a"
                    print(f"  exit={s['exit_code']}, wall={dur}s, cost={cost_str}, "
                          f"output={out}, ab={ab}")

        if not args.dry_run:
            results_dir.mkdir(parents=True, exist_ok=True)
            (results_dir / "run_summaries.json").write_text(json.dumps(summaries, indent=2))
            print(f"\nWrote {len(summaries)} run summaries to {results_dir / 'run_summaries.json'}")

    else:  # batch
        for cond in conditions:
            print(f"=== BATCH ({len(records)} records) | {cond} ===")
            s = run_batch(dataset, records, cond, dry_run=args.dry_run)
            summaries.append(s)
            if "error" in s:
                print(f"  ERROR: {s['error']}")
            elif args.dry_run:
                pass
            else:
                cost = s.get("total_cost_usd")
                dur = s.get("wall_seconds")
                ab = s.get("ab_error") or "ok"
                n_out = s.get("n_outputs_present")
                missing = s.get("missing_outputs") or []
                cost_str = f"${cost:.4f}" if cost is not None else "n/a"
                print(f"  exit={s['exit_code']}, wall={dur}s, cost={cost_str}, "
                      f"outputs={n_out}/{len(records)}, ab={ab}")
                if missing:
                    print(f"  missing outputs: {missing}")

        if not args.dry_run:
            results_dir.mkdir(parents=True, exist_ok=True)
            (results_dir / "run_summaries_batch.json").write_text(json.dumps(summaries, indent=2))
            print(f"\nWrote {len(summaries)} batch session summaries to "
                  f"{results_dir / 'run_summaries_batch.json'}")


if __name__ == "__main__":
    main()
