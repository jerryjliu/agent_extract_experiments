"""Seed data/sec_10q_insurance/manifest.json.

For each insurance/financial-services issuer (by CIK), query the SEC submissions
API for its most recent 10-Q and record the accession, period, and primary
document filename. Writes up to --n manifest entries.

Run: python scripts/seed_sec10q_manifest.py [--n 12]
"""
from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

HDR = {"User-Agent": "extract-bench/0.1 (research benchmark) github.com/jerryjliu/agent_extract_experiments"}

# Large US insurance / financial-services issuers (CIK, ticker, name).
ISSUERS = [
    ("0000080661", "PGR", "Progressive"),
    ("0001099219", "MET", "MetLife"),
    ("0000899051", "ALL", "Allstate"),
    ("0000086312", "TRV", "Travelers"),
    ("0000005272", "AIG", "American International Group"),
    ("0000874766", "HIG", "Hartford Financial"),
    ("0001137774", "PRU", "Prudential Financial"),
    ("0000059558", "LNC", "Lincoln National"),
    ("0000004977", "AFL", "Aflac"),
    ("0000020286", "CINF", "Cincinnati Financial"),
    ("0001000228", "PFG", "Principal Financial"),
    ("0000914208", "UNM", "Unum Group"),
    ("0000732712", "VZ-skip", "skip"),  # placeholder filtered out below
    ("0001081316", "MKL", "Markel"),
    ("0000064996", "MCY", "Mercury General"),
    ("0000743988", "AIZ", "Assurant"),
]


def _get(url: str) -> dict:
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=HDR), timeout=60).read())


def _latest_10q(cik: str) -> dict | None:
    sub = _get(f"https://data.sec.gov/submissions/CIK{cik}.json")
    rec = sub["filings"]["recent"]
    forms = rec["form"]
    for i, f in enumerate(forms):
        if f == "10-Q":
            return {
                "accession": rec["accessionNumber"][i],
                "period": rec["reportDate"][i],
                "primary_doc": rec["primaryDocument"][i],
            }
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=12)
    parser.add_argument("--output", type=Path, default=Path("data/sec_10q_insurance/manifest.json"))
    args = parser.parse_args()

    selected: list[dict] = []
    for cik, ticker, name in ISSUERS:
        if ticker.endswith("-skip"):
            continue
        if len(selected) >= args.n:
            break
        try:
            q = _latest_10q(cik)
        except Exception as e:  # noqa: BLE001
            print(f"  - {name} ({cik}): submissions fetch failed: {e}")
            continue
        if not q:
            print(f"  - {name} ({cik}): no 10-Q found")
            continue
        cik_int = str(int(cik))  # un-padded for Archives URLs
        rec = {"cik": cik_int, "ticker": ticker, "name": name,
               "accession": q["accession"], "form": "10-Q",
               "period": q["period"], "primary_doc": q["primary_doc"]}
        selected.append(rec)
        print(f"  + {name} ({ticker}) CIK {cik_int} 10-Q {q['accession']} period {q['period']}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(selected, indent=2))
    print(f"\nWrote {len(selected)} entries to {args.output}")


if __name__ == "__main__":
    main()
