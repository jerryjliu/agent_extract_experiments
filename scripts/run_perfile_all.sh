#!/usr/bin/env bash
# Per-file benchmark sweep: one claude -p session PER DOCUMENT, both conditions,
# full-agent no_skill. Results land in results/<slug>_rerun_<DATE>/ as the per-file
# filenames (summary.json / scored.csv) ALONGSIDE the batch files (summary_batch.json
# / scored_batch.csv) — the batch artifacts are never touched.
#
# Non-destructive to batch: per-file writes only summary.json, scored.csv, a recomputed
# invocation_stats.json (which carries BOTH the v2/per-file and batch blocks), and
# report.html. with_skill spends LlamaCloud credits per page (~$0.03125/page); no_skill
# spends only Claude tokens.
#
# Usage:
#   scripts/run_perfile_all.sh                       # the 3 remaining datasets (ffiec done in pilot)
#   scripts/run_perfile_all.sh ffiec_call_reports    # a specific dataset (or several)
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=.
DATE=2026-05-30

if [ "$#" -gt 0 ]; then
  SLUGS=("$@")
else
  SLUGS=(ctgov_protocols irs_form_990 sec_10q_insurance)
fi

for slug in "${SLUGS[@]}"; do
  OUT="results/${slug}_rerun_${DATE}"
  mkdir -p "$OUT"
  echo "############################################################"
  echo "# PER-FILE SWEEP: ${slug}  ->  ${OUT}"
  echo "############################################################"
  python scripts/run_benchmark.py --dataset "$slug" --mode per_file \
      --condition both --no-skill-agent full
  python scripts/score.py --dataset "$slug" --mode per_file \
      --csv-out "$OUT/scored.csv" --summary-out "$OUT/summary.json"
  python scripts/compute_invocation_stats.py --dataset "$slug" --out-dir "$OUT"
  python scripts/render_report.py --dataset "$slug" --results-dir "$OUT"
done

# Merge batch + per-file into the top-level cross-dataset summary.
python scripts/render_cross_dataset_summary.py --date "$DATE"
echo "Done. Per-file results written under results/<slug>_rerun_${DATE}/ and merged into results/cross_dataset_summary.{md,html}"
