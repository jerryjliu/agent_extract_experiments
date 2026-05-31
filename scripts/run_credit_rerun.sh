#!/usr/bin/env bash
# Fresh with_skill batch runs at default (agentic) tiers to capture uniform token
# cost; credit cost is derived from local PDF page counts ($0.00125/credit). no_skill
# is re-scored from existing artifacts (token cost unchanged, credit = 0). Outputs
# into results/<slug>_rerun_2026-05-30/ (token + credit + total, three line items).
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=.
DATE=2026-05-30
SLUGS=(ffiec_call_reports sec_10q_insurance irs_form_990 ctgov_protocols)

for slug in "${SLUGS[@]}"; do
  out="results/${slug}_rerun_${DATE}"
  echo "######## ${slug} ########"
  mkdir -p "$out"

  # Fresh with_skill run (overwrites runs_batch/<slug>/with_skill/, writes fanout +
  # usage sidecars). no_skill is left untouched and re-scored from existing artifacts.
  python3 -m scripts.run_benchmark --mode batch --dataset "$slug" --condition with_skill

  # Score BOTH conditions: with_skill from the fresh run, no_skill from the existing
  # runs_batch/<slug>/no_skill artifacts already on disk.
  python3 -m scripts.score --mode batch --dataset "$slug" \
      --csv-out "${out}/scored_batch.csv" --summary-out "${out}/summary_batch.json"

  python3 -m scripts.compute_invocation_stats --dataset "$slug" --out-dir "$out"
  python3 -m scripts.render_report --dataset "$slug" --results-dir "$out"
  echo "######## ${slug} DONE -> ${out} ########"
done

python3 -m scripts.render_cross_dataset_summary --date "$DATE"
echo "ALL CREDIT RERUNS COMPLETE"
