"""Fetch (or generate download instructions for) PDFs via dataset.pdf_fetcher.

Programmatic datasets (ClinicalTrials.gov, IRS 990, SEC 10-Q) download directly;
FFIEC writes a DOWNLOAD_INSTRUCTIONS.md for the manual CDR flow.

Run: python scripts/fetch_pdfs.py --dataset <slug>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.datasets import DEFAULT_SLUG, all_slugs, get_dataset


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=all_slugs(), default=DEFAULT_SLUG)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--pdf-dir", type=Path, default=None)
    args = parser.parse_args()

    ds = get_dataset(args.dataset)
    records = json.loads((args.manifest or ds.manifest_path()).read_text())
    pdf_dir = args.pdf_dir or ds.pdf_dir()
    pdf_dir.mkdir(parents=True, exist_ok=True)
    n_present, n_total = ds.pdf_fetcher(records, pdf_dir)
    print(f"{n_present}/{n_total} PDFs present in {pdf_dir}")


if __name__ == "__main__":
    main()
