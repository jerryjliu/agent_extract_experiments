#!/usr/bin/env bash
# Phase 5: full batch benchmark over the 3 new datasets, both conditions, then
# score + invocation stats + per-dataset report + cross-dataset summary.
set -uo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=.

DATASETS=(ctgov_protocols irs_form_990 sec_10q_insurance)

for slug in "${DATASETS[@]}"; do
  echo "############## RUN $slug ##############"
  python3 scripts/run_benchmark.py --dataset "$slug" --mode batch 2>&1
  echo "############## SCORE $slug ##############"
  python3 scripts/score.py --dataset "$slug" --mode batch 2>&1 | tail -2
  echo "############## STATS $slug ##############"
  python3 scripts/compute_invocation_stats.py --dataset "$slug" 2>&1 | tail -4
  echo "############## RENDER $slug ##############"
  python3 scripts/render_report.py --dataset "$slug" 2>&1 | tail -1
done

echo "############## CROSS-DATASET SUMMARY ##############"
python3 scripts/render_cross_dataset_summary.py --date 2026-05-29 2>&1
echo "############## PHASE 5 DONE ##############"
