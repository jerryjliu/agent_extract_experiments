"""Snapshot the v1 (current) benchmark artifacts so the v2 re-run can produce a comparison.

Idempotent: skips files that already exist with a "[skip]" note. Run before Phase 2 of the
v2 plan.
"""
from __future__ import annotations

import shutil
from pathlib import Path


SNAPSHOTS = [
    # (src, dst)  — files are copied; directories are copied recursively
    ("runs", "runs/baseline-v1"),
    ("results/scored.csv", "results/scored-baseline-v1.csv"),
    ("results/summary.json", "results/summary-baseline-v1.json"),
    ("results/report.html", "results/report-baseline-v1.html"),
    ("llama-extract/SKILL.md", "llama-extract/SKILL.v1.md"),
]


def main() -> None:
    for src, dst in SNAPSHOTS:
        src_p = Path(src)
        dst_p = Path(dst)
        if dst_p.exists():
            print(f"[skip] {dst} already exists")
            continue
        if not src_p.exists():
            print(f"[skip] {src} does not exist")
            continue
        if src_p.is_dir():
            # Special case: avoid copying the in-progress snapshot dir into itself
            ignore = shutil.ignore_patterns("baseline-v1") if src == "runs" else None
            dst_p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src_p, dst_p, ignore=ignore)
            print(f"[copy-dir] {src} -> {dst}")
        else:
            dst_p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_p, dst_p)
            print(f"[copy] {src} -> {dst}")


if __name__ == "__main__":
    main()
