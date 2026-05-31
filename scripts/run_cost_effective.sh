#!/usr/bin/env bash
# Non-destructive cost-effective batch runs across all 4 datasets.
# Runs with_skill ONLY at --extract-tier/--parse-tier cost_effective (no_skill is
# tier-independent, so it would just reproduce the existing baseline). Scores into
# results/<slug>_cost_effective_<DATE>/ and restores the canonical run summary after
# each dataset so existing results are untouched.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=.
DATE=2026-05-30
SLUGS=(ffiec_call_reports sec_10q_insurance irs_form_990 ctgov_protocols)

for slug in "${SLUGS[@]}"; do
  out="results/${slug}_cost_effective_${DATE}"
  echo "######## ${slug} :: cost_effective ########"
  mkdir -p "$out"

  python3 -m scripts.run_benchmark --mode batch --dataset "$slug" \
      --condition with_skill --extract-tier cost_effective --parse-tier cost_effective

  # Preserve the cost_effective with_skill run summary + session artifact in the new dir.
  cp "results/${slug}/run_summaries_batch.json" "${out}/run_summaries_batch.json"
  cp "runs_batch/${slug}/with_skill/session_result.json" \
     "${out}/session_result_with_skill.json" 2>/dev/null || true

  # Score the cost_effective run into the new dir (with_skill column is authoritative;
  # the no_skill column is stale carryover scratch and is ignored).
  python3 -m scripts.score --mode batch --dataset "$slug" \
      --csv-out "${out}/scored_batch.csv" \
      --summary-out "${out}/summary_batch.json"

  # Restore canonical run summary (the cost_effective run overwrote it).
  git checkout -- "results/${slug}/run_summaries_batch.json"
  echo "######## ${slug} DONE -> ${out} ########"
done
echo "ALL COST_EFFECTIVE RUNS COMPLETE"
