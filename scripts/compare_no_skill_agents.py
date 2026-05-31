"""Compare the canonical BARE no_skill run against a namespaced FULL-agent run.

Isolates the `--bare` agent confound: same condition (no_skill), same dataset, same
prompt — the only difference is the 3-tool bare agent vs the 27-tool full agent
(skill unstaged). Reports, per arm, the agent-loop cost drivers:

  token cost · turns · wall · tool mix · Read-on-PDF count · #large tool_results ·
  text-into-context · cache_creation / cache_read / output tokens · accuracy

Reads the bare run from runs_batch/<slug>/no_skill/ and the full run from
runs_batch/<slug>/no_skill__<tag>/. Accuracy is pulled from the scored summary
JSONs when present (optional).

Run:
  python -m scripts.compare_no_skill_agents --dataset ffiec_call_reports --tag fullagent \
      --out results/ffiec_call_reports_fullagent_2026-05-30/compare.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts.datasets import all_slugs, get_dataset

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def analyze_trace(trace_path: Path) -> dict[str, Any]:
    """Tool mix + how much text the agent pulled into context, from a session trace."""
    tools: dict[str, int] = {}
    read_on_pdf = 0
    result_chars = 0
    max_res_chars = 0
    big_results = 0  # tool_results > 8 KB
    if not trace_path.exists():
        return {"error": f"no trace at {trace_path}"}
    for line in trace_path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "assistant":
            for b in ev.get("message", {}).get("content", []):
                if b.get("type") == "tool_use":
                    name = b.get("name", "?")
                    tools[name] = tools.get(name, 0) + 1
                    if name == "Read":
                        fp = str(b.get("input", {}).get("file_path", "")).lower()
                        if fp.endswith(".pdf"):
                            read_on_pdf += 1
        elif ev.get("type") == "user":
            content = ev.get("message", {}).get("content", [])
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        c = b.get("content", "")
                        if isinstance(c, list):
                            c = "".join(x.get("text", "") for x in c if isinstance(x, dict))
                        n = len(c)
                        result_chars += n
                        max_res_chars = max(max_res_chars, n)
                        if n > 8000:
                            big_results += 1
    return {
        "tools": dict(sorted(tools.items(), key=lambda kv: -kv[1])),
        "n_tool_calls": sum(tools.values()),
        "read_on_pdf": read_on_pdf,
        "text_into_context_tok": result_chars // 4,
        "max_tool_result_tok": max_res_chars // 4,
        "big_tool_results_gt8kb": big_results,
    }


def analyze_arm(session_dir: Path, summary: dict[str, Any] | None) -> dict[str, Any]:
    sr = _load_json(session_dir / "session_result.json") or {}
    usage = sr.get("usage", {}) or {}
    arm = {
        "session_dir": str(session_dir.relative_to(REPO_ROOT)) if session_dir.is_relative_to(REPO_ROOT) else str(session_dir),
        "exists": (session_dir / "session_result.json").exists(),
        "token_cost_usd": sr.get("total_cost_usd"),
        "num_turns": sr.get("num_turns"),
        "wall_seconds": sr.get("wall_seconds_session") or (sr.get("duration_ms") or 0) / 1000 or None,
        "no_skill_agent": sr.get("no_skill_agent"),
        "cache_creation_tok": usage.get("cache_creation_input_tokens"),
        "cache_read_tok": usage.get("cache_read_input_tokens"),
        "output_tok": usage.get("output_tokens"),
        "input_tok": usage.get("input_tokens"),
    }
    arm.update(analyze_trace(session_dir / "trace.jsonl"))
    if summary and isinstance(summary.get("no_skill"), dict):
        ns = summary["no_skill"]
        arm["accuracy"] = ns.get("accuracy")
        arm["n_correct"] = ns.get("n_correct")
        arm["n_runs_with_output"] = ns.get("n_runs_with_output")
    else:
        arm["accuracy"] = None
    return arm


def _fmt(v: Any, money: bool = False) -> str:
    if v is None:
        return "—"
    if money:
        return f"${v:.4f}"
    if isinstance(v, float):
        return f"{v:.3f}"
    return f"{v:,}" if isinstance(v, int) else str(v)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", choices=all_slugs(), required=True)
    p.add_argument("--tag", default="fullagent", help="run-tag of the full-agent run")
    p.add_argument("--date", default="2026-05-30", help="date suffix for default summary paths")
    p.add_argument("--bare-summary", type=Path, default=None,
                   help="default: results/<slug>_rerun_<date>/summary_batch.json")
    p.add_argument("--full-summary", type=Path, default=None,
                   help="default: results/<slug>_<tag>_<date>/summary_no_skill_<tag>.json")
    p.add_argument("--out", type=Path, default=None, help="write the comparison JSON here")
    args = p.parse_args()

    dataset = get_dataset(args.dataset)
    slug = dataset.slug
    bare_dir = REPO_ROOT / dataset.batch_session_dir("no_skill")
    full_dir = REPO_ROOT / dataset.batch_session_dir("no_skill", args.tag)

    bare_summary = _load_json(args.bare_summary or REPO_ROOT /
                              f"results/{slug}_rerun_{args.date}/summary_batch.json")
    full_summary = _load_json(args.full_summary or REPO_ROOT /
                              f"results/{slug}_{args.tag}_{args.date}/summary_no_skill_{args.tag}.json")

    bare = analyze_arm(bare_dir, bare_summary)
    full = analyze_arm(full_dir, full_summary)

    cost_ratio = (full["token_cost_usd"] / bare["token_cost_usd"]
                  if bare.get("token_cost_usd") and full.get("token_cost_usd") else None)

    out = {
        "dataset": slug,
        "tag": args.tag,
        "bare": bare,
        "full": full,
        "deltas": {
            "token_cost_ratio_full_over_bare": round(cost_ratio, 2) if cost_ratio else None,
            "accuracy_bare": bare.get("accuracy"),
            "accuracy_full": full.get("accuracy"),
            "read_on_pdf_bare": bare.get("read_on_pdf"),
            "read_on_pdf_full": full.get("read_on_pdf"),
        },
    }

    # Pretty table to stdout
    rows = [
        ("token cost", _fmt(bare["token_cost_usd"], money=True), _fmt(full["token_cost_usd"], money=True)),
        ("turns", _fmt(bare["num_turns"]), _fmt(full["num_turns"])),
        ("wall (s)", _fmt(bare["wall_seconds"]), _fmt(full["wall_seconds"])),
        ("accuracy", _fmt(bare.get("accuracy")), _fmt(full.get("accuracy"))),
        ("n_tool_calls", _fmt(bare.get("n_tool_calls")), _fmt(full.get("n_tool_calls"))),
        ("Read-on-PDF", _fmt(bare.get("read_on_pdf")), _fmt(full.get("read_on_pdf"))),
        ("text→ctx (tok)", _fmt(bare.get("text_into_context_tok")), _fmt(full.get("text_into_context_tok"))),
        (">8KB results", _fmt(bare.get("big_tool_results_gt8kb")), _fmt(full.get("big_tool_results_gt8kb"))),
        ("cache_read (tok)", _fmt(bare.get("cache_read_tok")), _fmt(full.get("cache_read_tok"))),
        ("cache_creation", _fmt(bare.get("cache_creation_tok")), _fmt(full.get("cache_creation_tok"))),
        ("output (tok)", _fmt(bare.get("output_tok")), _fmt(full.get("output_tok"))),
    ]
    print(f"\n=== no_skill: BARE vs FULL agent — {slug} (tag={args.tag}) ===")
    print(f"{'metric':<18}{'bare':>16}{'full':>16}")
    print("-" * 50)
    for label, b, f in rows:
        print(f"{label:<18}{b:>16}{f:>16}")
    if cost_ratio:
        print(f"\ntoken cost: full is {cost_ratio:.2f}x bare")
    print(f"tools bare: {bare.get('tools')}")
    print(f"tools full: {full.get('tools')}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(out, indent=2))
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
