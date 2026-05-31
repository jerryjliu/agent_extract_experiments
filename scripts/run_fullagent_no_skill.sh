#!/usr/bin/env bash
# Measure the `--bare` agent confound: re-run the no_skill arm as the FULL 27-tool
# agent (skill unstaged) and compare to the canonical bare run. Non-destructive —
# everything is namespaced under no_skill__<TAG>; canonical no_skill artifacts and
# results/<slug>_rerun_<DATE>/ are never touched. no_skill calls no LlamaExtract, so
# this spends only Claude tokens (zero credits).
#
# Usage:
#   scripts/run_fullagent_no_skill.sh                 # all four datasets
#   scripts/run_fullagent_no_skill.sh ctgov_protocols sec_10q_insurance irs_form_990
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=.
DATE=2026-05-30
TAG=fullagent
if [ "$#" -gt 0 ]; then
  SLUGS=("$@")
else
  SLUGS=(ffiec_call_reports sec_10q_insurance irs_form_990 ctgov_protocols)
fi

for slug in "${SLUGS[@]}"; do
  out="results/${slug}_${TAG}_${DATE}"
  echo "######## ${slug} (full-agent no_skill) ########"
  mkdir -p "$out"

  # Full-agent no_skill, namespaced. Writes runs_batch/<slug>/no_skill__<TAG>/ +
  # per-doc fanout dirs; never touches canonical no_skill.
  python3 -m scripts.run_benchmark --mode batch --dataset "$slug" \
      --condition no_skill --no-skill-agent full --run-tag "$TAG"

  # Score the tagged no_skill arm (with_skill columns under the tag are empty by
  # design — its cost/accuracy/credits are agent-independent and already captured).
  python3 -m scripts.score --mode batch --dataset "$slug" --run-tag "$TAG" \
      --csv-out "${out}/scored_no_skill_${TAG}.csv" \
      --summary-out "${out}/summary_no_skill_${TAG}.json"

  # Bare-vs-full comparison (cost, accuracy, tool mix, Read-on-PDF, cache-read).
  python3 -m scripts.compare_no_skill_agents --dataset "$slug" --tag "$TAG" --date "$DATE" \
      --out "${out}/compare.json"

  echo "######## ${slug} DONE -> ${out} ########"
done

echo "ALL FULL-AGENT no_skill RUNS COMPLETE"
