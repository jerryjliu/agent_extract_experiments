"""Walk baseline-v1 and current with_skill runs; compute invocation-rate + token stats.

For each version (baseline_v1, v2):
- Per-run: did the Skill tool fire? did the agent write a *.py? did it call extract.py via Bash?
- Aggregates: invocation rate, CLI-call rate, script-write rate.
- Plus mean output tokens, mean wall, mean cost, mean turns from result.json.

Writes results/invocation_stats.json with both versions.
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

import pypdf

from scripts import pricing
from scripts.datasets import DEFAULT_SLUG, all_slugs, get_dataset


def corpus_local_pages(ds) -> int:
    """Sum the source-PDF page counts over the dataset manifest. Authoritative
    billable page count for LlamaExtract credit (full-doc extraction)."""
    total = 0
    try:
        records = json.loads(ds.manifest_path().read_text())
    except Exception:
        return 0
    for r in records:
        p = ds.pdf_path(r)
        try:
            total += len(pypdf.PdfReader(str(p)).pages)
        except Exception:
            pass
    return total


def analyze_trace(trace_path: Path) -> dict[str, Any]:
    """Walk a trace.jsonl and return tool-use counts + parallel-turn measure.

    parallelism signals:
    - `parallel_turns` / `max_parallel_in_turn`: count assistant turns with multiple
      tool_use blocks (in-turn parallel tool calls).
    - `task_starts`: count of system/task_started events, which fire when Claude
      launches a Task subagent. This is the dominant parallelism mechanism in
      batch mode — extract.py CLI calls wrapped in Tasks run concurrently across
      sequential assistant turns, so parallel_turns can be 0 while task_starts == N.
    """
    has_skill = False
    py_writes = 0
    extract_calls = 0
    n_tools = 0
    parallel_turns = 0
    max_parallel_in_turn = 0
    task_starts = 0
    with trace_path.open() as f:
        for line in f:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = ev.get("type")
            if t == "system" and ev.get("subtype") == "task_started":
                task_starts += 1
                continue
            if t != "assistant":
                continue
            turn_tools = 0
            for b in (ev.get("message", {}).get("content") or []):
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                turn_tools += 1
                n_tools += 1
                name = b.get("name")
                inp = b.get("input") or {}
                if name == "Skill":
                    has_skill = True
                if name == "Write" and isinstance(inp, dict) and str(inp.get("file_path", "")).endswith(".py"):
                    py_writes += 1
                if name == "Bash" and isinstance(inp, dict) and "extract.py" in str(inp.get("command", "")):
                    extract_calls += 1
            if turn_tools > 1:
                parallel_turns += 1
            if turn_tools > max_parallel_in_turn:
                max_parallel_in_turn = turn_tools
    return {
        "invoked_skill": has_skill,
        "py_writes": py_writes,
        "extract_calls": extract_calls,
        "n_tool_uses": n_tools,
        "parallel_turns": parallel_turns,
        "max_parallel_in_turn": max_parallel_in_turn,
        "task_starts": task_starts,
    }


def analyze_run(run_dir: Path) -> dict[str, Any]:
    """Return per-run stats parsed from trace.jsonl + result.json."""
    trace_path = run_dir / "trace.jsonl"
    result_path = run_dir / "result.json"
    if not trace_path.exists():
        return {}
    tool_stats = analyze_trace(trace_path)
    result = {}
    if result_path.exists():
        try:
            result = json.loads(result_path.read_text())
        except json.JSONDecodeError:
            result = {}
    opus = (result.get("modelUsage") or {}).get("claude-opus-4-7", {})
    return {
        **tool_stats,
        "cost": result.get("total_cost_usd"),
        "wall_ms": result.get("duration_ms"),
        "api_ms": result.get("duration_api_ms"),
        "turns": result.get("num_turns"),
        "output_tokens": opus.get("outputTokens"),
        "cache_read_tokens": opus.get("cacheReadInputTokens"),
        "cache_create_tokens": opus.get("cacheCreationInputTokens"),
    }


def analyze_batch_session(session_dir: Path, n_pages: int = 0) -> dict[str, Any]:
    """Stats for a single batch session (runs_batch/<condition>/).

    `n_pages` is the billable LlamaExtract page count for this condition (0 for
    no_skill, which never calls LlamaExtract); used with the session's tiers to
    compute credit cost.
    """
    trace_path = session_dir / "trace.jsonl"
    sr_path = session_dir / "session_result.json"
    if not trace_path.exists():
        return {"n_runs": 0}
    tool_stats = analyze_trace(trace_path)
    sr: dict[str, Any] = {}
    if sr_path.exists():
        try:
            sr = json.loads(sr_path.read_text())
        except json.JSONDecodeError:
            sr = {}
    opus = (sr.get("modelUsage") or {}).get("claude-opus-4-7", {})
    et = sr.get("extract_tier") or pricing.DEFAULT_EXTRACT_TIER
    pt = sr.get("parse_tier") or pricing.DEFAULT_PARSE_TIER
    token = round(float(sr.get("total_cost_usd") or 0.0), 4)
    credit = round(pricing.credit_cost_usd(n_pages, et, pt) or 0.0, 4) if n_pages else 0.0
    return {
        "n_runs": 1,  # one session per condition
        "n_invoked_skill": 1 if tool_stats["invoked_skill"] else 0,
        "skill_invocation_rate": 1.0 if tool_stats["invoked_skill"] else 0.0,
        "n_wrote_py": 1 if tool_stats["py_writes"] > 0 else 0,
        "py_write_rate": 1.0 if tool_stats["py_writes"] > 0 else 0.0,
        "n_called_cli": 1 if tool_stats["extract_calls"] > 0 else 0,
        "cli_call_rate": 1.0 if tool_stats["extract_calls"] > 0 else 0.0,
        "extract_calls": tool_stats["extract_calls"],
        "py_writes": tool_stats["py_writes"],
        "n_tool_uses": tool_stats["n_tool_uses"],
        "parallel_turns": tool_stats["parallel_turns"],
        "max_parallel_in_turn": tool_stats["max_parallel_in_turn"],
        "task_starts": tool_stats["task_starts"],
        "session_token_cost_usd": token,
        "session_credit_cost_usd": credit,
        "session_total_cost_usd": round(token + credit, 4),
        "session_n_pages": n_pages or None,
        "session_extract_tier": sr.get("extract_tier"),
        "session_parse_tier": sr.get("parse_tier"),
        # wall_seconds_session is the true end-to-end wall measured by the orchestrator;
        # falls back to result.duration_ms for older session_result.json files (which is
        # only the final phase when Claude used background Tasks).
        "session_wall_s": round(float(sr.get("wall_seconds_session", (sr.get("duration_ms") or 0.0) / 1000.0)), 1),
        "session_api_s": round(float(sr.get("duration_api_ms") or 0.0) / 1000, 1),
        "session_turns": sr.get("num_turns"),
        "session_output_tokens": opus.get("outputTokens"),
        "session_cache_read_tokens": opus.get("cacheReadInputTokens"),
        "session_cache_create_tokens": opus.get("cacheCreationInputTokens"),
    }


def aggregate(runs_dir: Path, condition: str) -> dict[str, Any]:
    results = []
    for d in sorted(runs_dir.glob(f"*_{condition}")):
        stats = analyze_run(d)
        if stats:
            stats["rssd"] = d.name.split("_")[0]
            results.append(stats)
    n = len(results)
    if n == 0:
        return {"n_runs": 0}
    n_invoked = sum(1 for r in results if r["invoked_skill"])
    n_wrote_py = sum(1 for r in results if r["py_writes"] > 0)
    n_called_cli = sum(1 for r in results if r["extract_calls"] > 0)
    mean_cost = statistics.mean(r["cost"] for r in results if r["cost"] is not None)
    mean_wall = statistics.mean(r["wall_ms"] for r in results if r["wall_ms"] is not None) / 1000
    mean_api = statistics.mean(r["api_ms"] for r in results if r["api_ms"] is not None) / 1000
    mean_turns = statistics.mean(r["turns"] for r in results if r["turns"] is not None)
    mean_out = statistics.mean(r["output_tokens"] for r in results if r["output_tokens"] is not None)
    total_cost = sum(r["cost"] for r in results if r["cost"] is not None)
    return {
        "n_runs": n,
        "n_invoked_skill": n_invoked,
        "skill_invocation_rate": n_invoked / n,
        "n_wrote_py": n_wrote_py,
        "py_write_rate": n_wrote_py / n,
        "n_called_cli": n_called_cli,
        "cli_call_rate": n_called_cli / n,
        "mean_cost_usd": round(mean_cost, 4),
        "total_cost_usd": round(total_cost, 4),
        "mean_wall_s": round(mean_wall, 1),
        "mean_api_s": round(mean_api, 1),
        "mean_turns": round(mean_turns, 1),
        "mean_output_tokens": int(mean_out),
        "per_run": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=all_slugs(), default=DEFAULT_SLUG)
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="Override results dir for invocation_stats.json output.")
    args = parser.parse_args()

    ds = get_dataset(args.dataset)
    runs_dir = ds.runs_dir()
    runs_batch_dir = ds.runs_batch_dir()
    results_dir = args.out_dir or ds.results_dir()
    # The v1 baseline snapshot only exists for the FFIEC corpus.
    baseline_dir = runs_dir / "baseline-v1"

    out: dict[str, Any] = {}
    versions = []
    if baseline_dir.exists():
        out["baseline_v1"] = {
            "with_skill": aggregate(baseline_dir, "with_skill"),
            "no_skill": aggregate(baseline_dir, "no_skill"),
        }
        out["v2"] = {
            "with_skill": aggregate(runs_dir, "with_skill"),
            # no_skill is unchanged; reuse the baseline numbers
            "no_skill": aggregate(baseline_dir, "no_skill"),
        }
        versions = ["baseline_v1", "v2"]
    else:
        # No baseline snapshot: just report whatever per-file runs exist.
        out["v2"] = {
            "with_skill": aggregate(runs_dir, "with_skill"),
            "no_skill": aggregate(runs_dir, "no_skill"),
        }
        versions = ["v2"]

    # Add a batch block if the batch sessions are on disk
    batch_ws = runs_batch_dir / "with_skill"
    batch_ns = runs_batch_dir / "no_skill"
    if batch_ws.exists() or batch_ns.exists():
        n_pages = corpus_local_pages(ds)
        out["batch"] = {
            "with_skill": analyze_batch_session(batch_ws, n_pages=n_pages),
            "no_skill": analyze_batch_session(batch_ns, n_pages=0),
        }
    results_dir.mkdir(parents=True, exist_ok=True)
    out_path = results_dir / "invocation_stats.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"Wrote {out_path}")
    # Print a quick summary
    for ver in versions:
        ws = out[ver]["with_skill"]
        ns = out[ver]["no_skill"]
        if ws.get("n_runs", 0) == 0:
            continue
        print(f"\n=== {ver} ===")
        print(f"  with_skill: n={ws['n_runs']}  invocation={ws['n_invoked_skill']}/{ws['n_runs']} ({ws['skill_invocation_rate']*100:.0f}%)"
              f"  cli={ws['n_called_cli']}/{ws['n_runs']}  py_writes={ws['n_wrote_py']}/{ws['n_runs']}"
              f"  mean_cost=${ws['mean_cost_usd']:.4f}  mean_wall={ws['mean_wall_s']}s  mean_out={ws['mean_output_tokens']}")
        print(f"  no_skill:   n={ns['n_runs']}  mean_cost=${ns['mean_cost_usd']:.4f}  mean_wall={ns['mean_wall_s']}s  mean_out={ns['mean_output_tokens']}")
    if "batch" in out:
        ws = out["batch"]["with_skill"]
        ns = out["batch"]["no_skill"]
        print(f"\n=== batch ===")
        print(f"  with_skill: skill={ws.get('n_invoked_skill', 0)}/1  cli_calls={ws.get('extract_calls', 0)}"
              f"  py_writes={ws.get('py_writes', 0)}  parallel_turns={ws.get('parallel_turns', 0)}"
              f"  max_parallel={ws.get('max_parallel_in_turn', 0)}  pages={ws.get('session_n_pages') or 0}"
              f"  token=${ws.get('session_token_cost_usd', 0):.4f}  credit=${ws.get('session_credit_cost_usd', 0):.4f}"
              f"  total=${ws.get('session_total_cost_usd', 0):.4f}  wall={ws.get('session_wall_s', 0)}s")
        print(f"  no_skill:   parallel_turns={ns.get('parallel_turns', 0)}"
              f"  max_parallel={ns.get('max_parallel_in_turn', 0)}"
              f"  token=${ns.get('session_token_cost_usd', 0):.4f}  credit=${ns.get('session_credit_cost_usd', 0):.4f}"
              f"  total=${ns.get('session_total_cost_usd', 0):.4f}  wall={ns.get('session_wall_s', 0)}s")


if __name__ == "__main__":
    main()
