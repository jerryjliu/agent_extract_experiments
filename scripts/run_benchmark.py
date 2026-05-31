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
  # cost-effective extract mode (with_skill only; no_skill ignores tiers):
  python scripts/run_benchmark.py --mode batch --extract-tier cost_effective
  # decouple the two stages — cheap extraction over a high-fidelity parse:
  python scripts/run_benchmark.py --mode batch --extract-tier cost_effective --parse-tier agentic

The llama-extract CLI accepts --tier (extraction) and --parse-tier (parsing)
independently; this orchestrator exposes both as --extract-tier / --parse-tier and
passes them to with_skill sessions as a tool-usage directive in the prompt (no_skill
has no extract CLI, so its prompt is unchanged).
"""
from __future__ import annotations

import argparse
import json
import os
import re
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

# Tier configuration for the llama-extract skill. The CLI (scripts/extract.py)
# accepts --tier (extraction) and --parse-tier (parsing) independently; the
# benchmark exposes both so we can A/B the cost-effective mode against agentic.
# The tiers reach the run via the with_skill prompt (a tool-usage directive that
# names the exact CLI flags) — no_skill has no extract CLI so its prompt is
# unchanged. verify_tiers_in_trace() then confirms what actually ran.
EXTRACT_TIER_CHOICES = ("cost_effective", "agentic")
PARSE_TIER_CHOICES = ("fast", "cost_effective", "agentic", "agentic_plus")
DEFAULT_EXTRACT_TIER = "agentic"
DEFAULT_PARSE_TIER = "agentic"


def verify_tiers_in_trace(trace_path: Path, extract_tier: str, parse_tier: str) -> str:
    """Best-effort check that extract.py actually ran at the configured tiers.

    Inspects the EXECUTED extract.py Bash commands in the session trace — i.e. the
    `… extract.py … --tier <X> --parse-tier <Y>` strings — and reports the count of
    each (tier, parse_tier) pair seen. We key off the command flags, not extract.py's
    `--verbose` stderr line, because (a) that line's source contains the literal
    f-string template `{args.tier}` which a file-read drops into the trace, and (b)
    background-subagent stderr isn't in the main trace anyway. Reading SKILL.md (whose
    example shows `--tier agentic`) can contribute a stray non-configured count, so we
    accept as long as the configured pair is present and is the plurality.

    Returns "ok (...)", "mismatch: ...", or "unverified (...)" — never raises.
    """
    if not trace_path.exists():
        return "unverified (no trace)"
    text = trace_path.read_text(errors="replace")
    pairs = re.findall(
        r"extract\.py[^\"]*?--tier\s+([a-z_]+)[^\"]*?--parse-tier\s+([a-z_]+)", text)
    if not pairs:
        return "unverified (no extract.py command found in trace)"
    counts: dict[tuple[str, str], int] = {}
    for t, p in pairs:
        counts[(t, p)] = counts.get((t, p), 0) + 1
    want = (extract_tier, parse_tier)
    breakdown = ", ".join(f"{t}/{p}×{n}" for (t, p), n in sorted(counts.items(), key=lambda kv: -kv[1]))
    want_n = counts.get(want, 0)
    if want_n == 0:
        return f"mismatch: expected {extract_tier}/{parse_tier}, saw none; commands: {breakdown}"
    if want_n < max(counts.values()):
        return f"mismatch: {extract_tier}/{parse_tier} not the plurality; commands: {breakdown}"
    return f"ok ({want_n} extract.py call(s) at {extract_tier}/{parse_tier}; commands: {breakdown})"


def _common_flags(condition: str, system_prompt: str, no_skill_agent: str = "bare") -> list[str]:
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
        if no_skill_agent == "full":
            # Full 27-tool agent, identical flags to with_skill, so the only A/B
            # variable is the skill itself. The llama-extract skill is NOT staged
            # into a no_skill run dir, so it never loads (verify_ab stays clean) —
            # this is "full Claude Code without the llama-extract skill installed".
            common += ["--setting-sources", "project,local"]
        else:
            common += ["--bare"]
    else:
        raise ValueError(f"unknown condition: {condition}")
    return common


def _skill_tiers(condition: str, extract_tier: str, parse_tier: str) -> tuple[str | None, str | None]:
    """Tiers to pass the prompt builder. Only with_skill invokes the extract CLI;
    no_skill reads PDFs directly, so it gets no tier directive (prompt unchanged)."""
    if condition == "with_skill":
        return extract_tier, parse_tier
    return None, None


def resolve_command(dataset: DatasetConfig, condition: str,
                    extract_tier: str = DEFAULT_EXTRACT_TIER,
                    parse_tier: str = DEFAULT_PARSE_TIER,
                    no_skill_agent: str = "bare") -> list[str]:
    """Build the claude -p invocation flags for a given (per-file) condition."""
    et, pt = _skill_tiers(condition, extract_tier, parse_tier)
    cmd = _common_flags(condition, SYSTEM_PROMPT_APPEND, no_skill_agent=no_skill_agent)
    cmd.append(build_extraction_prompt(dataset, extract_tier=et, parse_tier=pt))
    return cmd


def resolve_command_batch(dataset: DatasetConfig, condition: str, doc_keys: list[str],
                          extract_tier: str = DEFAULT_EXTRACT_TIER,
                          parse_tier: str = DEFAULT_PARSE_TIER,
                          no_skill_agent: str = "bare") -> list[str]:
    """Build the claude -p invocation flags for a batch-mode condition."""
    et, pt = _skill_tiers(condition, extract_tier, parse_tier)
    cmd = _common_flags(condition, SYSTEM_PROMPT_APPEND_BATCH, no_skill_agent=no_skill_agent)
    cmd.append(build_batch_prompt(dataset, doc_keys, extract_tier=et, parse_tier=pt))
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


def stage_batch_dir(dataset: DatasetConfig, condition: str, records: list[dict[str, Any]],
                    tag: str = "") -> Path:
    """Build the per-condition batch working directory.

    Layout:
      runs_batch/<slug>/<condition>[__<tag>]/
        inputs/<doc_key>.pdf    # symlink to data/<slug>/pdfs/<doc_key>.pdf
        outputs/                # empty; Claude writes here
        schema.json             # pre-staged JSON Schema dump
        .claude/skills/...      # with-skill only

    `tag` namespaces the run (e.g. "fullagent") so experiments never clobber the
    canonical <condition> dirs.
    """
    run_dir = (REPO_ROOT / dataset.batch_session_dir(condition, tag)).resolve()
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
            dry_run: bool = False,
            extract_tier: str = DEFAULT_EXTRACT_TIER,
            parse_tier: str = DEFAULT_PARSE_TIER,
            no_skill_agent: str = "bare") -> dict[str, Any]:
    """Execute one (record, condition) run; return a summary dict.

    no_skill_agent: "bare" (3-tool agent) or "full" (27-tool agent, skill unstaged)
        — only affects the no_skill condition (mirrors run_batch).
    """
    doc_key = dataset.doc_key_fn(record)
    pdf_path = dataset.pdf_path(record)
    if not pdf_path.exists():
        return {"doc_key": doc_key, "condition": condition, "error": f"PDF not found: {pdf_path}"}

    run_dir = dataset.run_dir(record, condition)
    cmd = resolve_command(dataset, condition, extract_tier, parse_tier,
                          no_skill_agent=no_skill_agent)
    # Tiers only apply to the skill (with_skill); no_skill reads PDFs directly.
    tiers = {"extract_tier": extract_tier, "parse_tier": parse_tier} if condition == "with_skill" else {}
    # Record the no_skill agent mode for traceability (only meaningful for no_skill).
    agent_meta = {"no_skill_agent": no_skill_agent} if condition == "no_skill" else {}

    if dry_run:
        # Do NOT call stage_run_dir in dry-run — staging is destructive (rmtree).
        print(f"[DRY] {doc_key} {condition} cwd={run_dir}")
        if agent_meta:
            print(f"      no_skill_agent={no_skill_agent}")
        if tiers:
            print(f"      tiers: extract={extract_tier} parse={parse_tier}")
        print(f"      cmd={' '.join(cmd[:8])} ... (prompt length {len(cmd[-1])} chars)")
        return {"doc_key": doc_key, "condition": condition, "dry_run": True, **tiers, **agent_meta}

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
        **tiers,
        **agent_meta,
    }
    if condition == "with_skill":
        summary["tier_verification"] = verify_tiers_in_trace(trace_path, extract_tier, parse_tier)
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
                   session_dir: Path, tag: str = "") -> dict[str, bool]:
    """Re-stage runs_batch/<slug>/<cond>[__<tag>]/outputs/<key>.json into per-doc dirs the scorer reads.

    Returns: {doc_key: output_present}
    """
    presence: dict[str, bool] = {}
    outputs_dir = session_dir / "outputs"
    session_trace = session_dir / "trace.jsonl"
    for record in records:
        doc_key = dataset.doc_key_fn(record)
        src = outputs_dir / f"{doc_key}.json"
        per_doc_dir = REPO_ROOT / dataset.batch_fanout_dir(record, condition, tag)
        if per_doc_dir.exists():
            shutil.rmtree(per_doc_dir)
        per_doc_dir.mkdir(parents=True)
        if src.exists():
            shutil.copy(src, per_doc_dir / "output.json")
            presence[doc_key] = True
            usage_src = outputs_dir / f"{doc_key}.usage.json"
            if usage_src.exists():
                shutil.copy(usage_src, per_doc_dir / "output.usage.json")
        else:
            presence[doc_key] = False
        if session_trace.exists():
            shutil.copy(session_trace, per_doc_dir / "trace.jsonl")
    return presence


def run_batch(dataset: DatasetConfig, records: list[dict[str, Any]], condition: str,
              dry_run: bool = False,
              extract_tier: str = DEFAULT_EXTRACT_TIER,
              parse_tier: str = DEFAULT_PARSE_TIER,
              no_skill_agent: str = "bare",
              run_tag: str = "") -> dict[str, Any]:
    """Execute one batch session for `condition` over all `records`; return summary.

    no_skill_agent: "bare" (default, 3-tool agent) or "full" (27-tool agent, skill
        unstaged) — only affects the no_skill condition.
    run_tag: namespaces output dirs (<condition>__<tag>) so experiments never clobber
        the canonical runs.
    """
    if not records:
        return {"condition": condition, "error": "no records"}

    doc_keys = [dataset.doc_key_fn(r) for r in records]
    cmd = resolve_command_batch(dataset, condition, doc_keys, extract_tier, parse_tier,
                                no_skill_agent=no_skill_agent)
    # Tiers only apply to the skill (with_skill); no_skill reads PDFs directly.
    tiers = {"extract_tier": extract_tier, "parse_tier": parse_tier} if condition == "with_skill" else {}
    # Record the no_skill agent mode for traceability (only meaningful for no_skill).
    agent_meta = {"no_skill_agent": no_skill_agent} if condition == "no_skill" else {}

    if dry_run:
        # Do NOT call stage_batch_dir in dry-run — staging is destructive (rmtree).
        intended_dir = (REPO_ROOT / dataset.batch_session_dir(condition, run_tag)).resolve()
        rel = intended_dir.relative_to(REPO_ROOT)
        print(f"[DRY] batch {condition} cwd={rel} (not staged)")
        if agent_meta:
            print(f"      no_skill_agent={no_skill_agent}")
        if tiers:
            print(f"      tiers: extract={extract_tier} parse={parse_tier}")
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
        return {"condition": condition, "n_records": len(doc_keys), "dry_run": True, **tiers, **agent_meta}

    session_dir = stage_batch_dir(dataset, condition, records, tag=run_tag)

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
        if tiers:
            enriched.update(tiers)
        if agent_meta:
            enriched.update(agent_meta)
        (session_dir / "session_result.json").write_text(json.dumps(enriched, indent=2))

    presence = fanout_outputs(dataset, condition, records, session_dir, tag=run_tag)

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
        **tiers,
        **agent_meta,
    }
    if condition == "with_skill":
        summary["tier_verification"] = verify_tiers_in_trace(trace_path, extract_tier, parse_tier)
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
    parser.add_argument("--extract-tier", dest="extract_tier",
                        choices=list(EXTRACT_TIER_CHOICES), default=DEFAULT_EXTRACT_TIER,
                        help="llama-extract EXTRACTION tier for with_skill runs (default: "
                             f"{DEFAULT_EXTRACT_TIER}). 'cost_effective' = the cheaper, "
                             "faster extraction mode. Ignored by no_skill.")
    parser.add_argument("--parse-tier", dest="parse_tier",
                        choices=list(PARSE_TIER_CHOICES), default=DEFAULT_PARSE_TIER,
                        help="llama-extract PARSE tier for with_skill runs (default: "
                             f"{DEFAULT_PARSE_TIER}), decoupled from --extract-tier so the "
                             "parsing stage can be tuned independently. Ignored by no_skill.")
    parser.add_argument("--no-skill-agent", dest="no_skill_agent",
                        choices=["bare", "full"], default="full",
                        help="Agent harness for no_skill (per_file and batch modes). 'full' (default, "
                             "canonical) = the same 27-tool agent as with_skill but with the "
                             "llama-extract skill not staged, i.e. 'full Claude Code without the "
                             "skill' — the realistic skill-less baseline. 'bare' = stripped "
                             "3-tool agent (--bare), opt-in; pair with --run-tag bare to avoid "
                             "clobbering the canonical no_skill artifacts.")
    parser.add_argument("--run-tag", dest="run_tag", default="",
                        help="Namespace suffix for batch output dirs (<condition>__<tag>) so "
                             "experiments never overwrite the canonical runs. Empty = canonical.")
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

    if "with_skill" in conditions:
        print(f"with_skill tiers: extract={args.extract_tier} parse={args.parse_tier}"
              + ("  [default]" if (args.extract_tier == DEFAULT_EXTRACT_TIER
                                   and args.parse_tier == DEFAULT_PARSE_TIER) else ""))

    if args.mode == "per_file":
        for record in records:
            for cond in conditions:
                agent_note = f" [no_skill_agent={args.no_skill_agent}]" if cond == "no_skill" and args.no_skill_agent != "bare" else ""
                print(f"=== {record.get('name', dataset.doc_key_fn(record))} | {cond}{agent_note} ===")
                s = run_one(dataset, record, cond, dry_run=args.dry_run,
                            extract_tier=args.extract_tier, parse_tier=args.parse_tier,
                            no_skill_agent=args.no_skill_agent)
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
                    if s.get("tier_verification"):
                        print(f"  tiers: {s['tier_verification']}")

        if not args.dry_run:
            results_dir.mkdir(parents=True, exist_ok=True)
            (results_dir / "run_summaries.json").write_text(json.dumps(summaries, indent=2))
            print(f"\nWrote {len(summaries)} run summaries to {results_dir / 'run_summaries.json'}")

    else:  # batch
        for cond in conditions:
            tag_note = f" [tag={args.run_tag}]" if args.run_tag else ""
            agent_note = f" [no_skill_agent={args.no_skill_agent}]" if cond == "no_skill" and args.no_skill_agent != "bare" else ""
            print(f"=== BATCH ({len(records)} records) | {cond}{agent_note}{tag_note} ===")
            s = run_batch(dataset, records, cond, dry_run=args.dry_run,
                          extract_tier=args.extract_tier, parse_tier=args.parse_tier,
                          no_skill_agent=args.no_skill_agent, run_tag=args.run_tag)
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
                if s.get("tier_verification"):
                    print(f"  tiers: {s['tier_verification']}")
                if missing:
                    print(f"  missing outputs: {missing}")

        if not args.dry_run:
            results_dir.mkdir(parents=True, exist_ok=True)
            (results_dir / "run_summaries_batch.json").write_text(json.dumps(summaries, indent=2))
            print(f"\nWrote {len(summaries)} batch session summaries to "
                  f"{results_dir / 'run_summaries_batch.json'}")


if __name__ == "__main__":
    main()
