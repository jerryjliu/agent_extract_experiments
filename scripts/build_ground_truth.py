"""Build ground truth via dataset.gt_builder. Dispatches by --dataset.

For each record in the dataset's manifest, derive structured ground-truth values
from the authoritative source (FDIC API, ClinicalTrials.gov API, IRS e-file XML,
SEC XBRL CompanyFacts, ...) and write one JSON per record to
data/<slug>/ground_truth/<doc_key>.json.

Run: python scripts/build_ground_truth.py --dataset <slug>
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from scripts.datasets import DEFAULT_SLUG, all_slugs, get_dataset


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=all_slugs(), default=DEFAULT_SLUG)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--sleep", type=float, default=0.3, help="Seconds between requests.")
    args = parser.parse_args()

    ds = get_dataset(args.dataset)
    manifest_path = args.manifest or ds.manifest_path()
    output_dir = args.output_dir or ds.gt_dir()
    records = json.loads(manifest_path.read_text())
    output_dir.mkdir(parents=True, exist_ok=True)

    n_ok = 0
    for record in records:
        out_path = output_dir / f"{ds.doc_key_fn(record)}.json"
        gt = ds.gt_builder(record)
        out_path.write_text(json.dumps(gt, indent=2))
        if "error" in gt:
            status = f"  ERROR: {gt['error']}"
        else:
            n_ok += 1
            status = f"  {gt.get('populated_count')}/{gt.get('total_fields')} fields populated"
        print(f"{record.get('name', ds.doc_key_fn(record))}")
        print(status)
        time.sleep(args.sleep)
    print(f"\nWrote {len(records)} ground-truth files to {output_dir} ({n_ok} without error)")


if __name__ == "__main__":
    main()
