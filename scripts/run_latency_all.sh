#!/usr/bin/env bash
# Controlled cold->warm parse-cache latency experiment for the remaining datasets,
# run SEQUENTIALLY (concurrent runs would inflate each other's latency via shared
# LlamaCloud load). Each dataset: rewritten-paired cold+warm, scored per pass, then
# its report.html is re-rendered with the split with_skill rows + latency panel.
# Finally the cross-dataset summary is regenerated so all 4 datasets show cold/warm.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=.
DATE=2026-05-30
SLUGS=(ctgov_protocols irs_form_990 sec_10q_insurance)

for slug in "${SLUGS[@]}"; do
  echo "######## ${slug} cold/warm ########"
  python3 -m scripts.run_latency_cold_warm --dataset "$slug"
  python3 -m scripts.render_report --dataset "$slug" --results-dir "results/${slug}_rerun_${DATE}"
  rm -rf "/tmp/${slug}_cold_pdfs"
  echo "######## ${slug} DONE ########"
done

python3 -m scripts.render_cross_dataset_summary --date "$DATE"
echo "ALL COLD/WARM EXPERIMENTS COMPLETE"
