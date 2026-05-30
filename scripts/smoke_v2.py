"""Phase 4 smoke gate: run with_skill on 2 banks and verify the new skill actually triggers.

Pass conditions (per the v2 plan):
- At least 1 of 2 smoke runs invokes Skill({"skill":"llama-extract"})
- Zero Write(*.py) tool calls across both runs (no script transcription)
- At least 1 of 2 runs invokes the bundled CLI via Bash(python ... extract.py ...)
- output.json exists and parses in both run dirs

Prints a clear pass/fail summary. Exit code 0 = pass, 1 = fail.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


# Picks: one community 051 (Legends — the only v1 invocation; sanity check) and one
# megabank 031 (Bank of America — the hardest case in the benchmark).
SMOKE_BANKS = ["2745426", "480228"]


def parse_trace(run_dir: Path) -> dict[str, Any]:
    """Return aggregate stats about tool use in one run."""
    trace = run_dir / "trace.jsonl"
    if not trace.exists():
        return {"trace_present": False}
    skill_invocations = []
    tool_uses: list[tuple[str, dict]] = []
    init_skills: list[str] | None = None
    with trace.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "system" and ev.get("subtype") == "init":
                init_skills = ev.get("skills") or []
                continue
            if ev.get("type") != "assistant":
                continue
            for b in (ev.get("message", {}).get("content") or []):
                if not isinstance(b, dict):
                    continue
                if b.get("type") != "tool_use":
                    continue
                name = b.get("name")
                inp = b.get("input") or {}
                tool_uses.append((name, inp))
                if name == "Skill":
                    skill_invocations.append(inp.get("skill") or inp.get("name") or "<unknown>")
    py_writes = [
        inp.get("file_path", "")
        for name, inp in tool_uses
        if name == "Write" and isinstance(inp, dict) and str(inp.get("file_path", "")).endswith(".py")
    ]
    cli_bash_calls = [
        inp.get("command", "")
        for name, inp in tool_uses
        if name == "Bash" and isinstance(inp, dict) and "extract.py" in str(inp.get("command", ""))
    ]
    return {
        "trace_present": True,
        "init_skills_listed_llama_extract": init_skills is not None and "llama-extract" in init_skills,
        "init_skills": init_skills,
        "skill_invocations": skill_invocations,
        "n_tool_uses": len(tool_uses),
        "py_writes": py_writes,
        "cli_bash_calls": cli_bash_calls,
        "tool_use_names": [name for name, _ in tool_uses],
    }


def load_result(run_dir: Path) -> dict[str, Any]:
    r = run_dir / "result.json"
    if not r.exists():
        return {}
    return json.loads(r.read_text())


def load_output(run_dir: Path) -> bool:
    o = run_dir / "output.json"
    if not o.exists():
        return False
    try:
        json.loads(o.read_text())
        return True
    except json.JSONDecodeError:
        return False


def run_one_bank(rssd: str) -> None:
    print(f"\n=== Running with_skill smoke for RSSD {rssd} ===", flush=True)
    cmd = [
        "python", "scripts/run_benchmark.py",
        "--filing", rssd,
        "--condition", "with_skill",
    ]
    subprocess.run(cmd, check=False, env={**__import__("os").environ, "PYTHONPATH": "."})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--banks", nargs="+", default=SMOKE_BANKS,
                        help="RSSDs to smoke (default: Legends 2745426 + BofA 480228).")
    parser.add_argument("--skip-run", action="store_true",
                        help="Skip running the orchestrator; just inspect existing run dirs.")
    args = parser.parse_args()

    period = "2024-09-30"
    if not args.skip_run:
        for rssd in args.banks:
            run_one_bank(rssd)

    print(f"\n{'='*60}\nSMOKE GATE EVALUATION\n{'='*60}")
    results = []
    for rssd in args.banks:
        run_dir = Path("runs") / f"{rssd}_{period}_with_skill"
        trace_info = parse_trace(run_dir)
        result_info = load_result(run_dir)
        output_ok = load_output(run_dir)
        cost = result_info.get("total_cost_usd")
        wall = result_info.get("duration_ms")
        out_tokens = (result_info.get("modelUsage") or {}).get("claude-opus-4-7", {}).get("outputTokens")

        print(f"\n--- RSSD {rssd} ---")
        print(f"  trace_present:                    {trace_info.get('trace_present')}")
        print(f"  llama-extract in init skills:     {trace_info.get('init_skills_listed_llama_extract')}")
        print(f"  Skill tool invocations:           {len(trace_info.get('skill_invocations') or [])}  {trace_info.get('skill_invocations')}")
        print(f"  Tool use names:                   {trace_info.get('tool_use_names')}")
        print(f"  *.py Write events (SHOULD BE 0):  {len(trace_info.get('py_writes') or [])}  {trace_info.get('py_writes')}")
        print(f"  extract.py Bash invocations:      {len(trace_info.get('cli_bash_calls') or [])}")
        print(f"  output.json present + valid JSON: {output_ok}")
        print(f"  cost:                             ${cost:.4f}" if cost else "  cost: n/a")
        print(f"  wall time:                        {(wall or 0)/1000:.1f}s")
        print(f"  output tokens:                    {out_tokens}")
        results.append({
            "rssd": rssd,
            "invoked_skill": len(trace_info.get("skill_invocations") or []) >= 1,
            "wrote_script": len(trace_info.get("py_writes") or []) > 0,
            "called_cli": len(trace_info.get("cli_bash_calls") or []) >= 1,
            "output_ok": output_ok,
            "cost": cost,
            "wall_s": (wall or 0) / 1000,
            "out_tokens": out_tokens,
        })

    n_total = len(results)
    n_invoked = sum(1 for r in results if r["invoked_skill"])
    n_wrote = sum(1 for r in results if r["wrote_script"])
    n_called_cli = sum(1 for r in results if r["called_cli"])
    n_output = sum(1 for r in results if r["output_ok"])

    print(f"\n{'='*60}\nVERDICT\n{'='*60}")
    print(f"  Skill invocation rate:  {n_invoked}/{n_total}   {'PASS' if n_invoked >= 1 else 'FAIL'} (need >= 1)")
    print(f"  Script-write rate:      {n_wrote}/{n_total}   {'PASS' if n_wrote == 0 else 'FAIL'} (need == 0)")
    print(f"  CLI-call rate:          {n_called_cli}/{n_total}   {'PASS' if n_called_cli >= 1 else 'FAIL'} (need >= 1)")
    print(f"  output.json present:    {n_output}/{n_total}   {'PASS' if n_output == n_total else 'FAIL'}")

    passed = (n_invoked >= 1) and (n_wrote == 0) and (n_called_cli >= 1) and (n_output == n_total)
    print(f"\n  OVERALL: {'PASS' if passed else 'FAIL — iterate on SKILL.md description'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
