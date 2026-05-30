"""Select 15 commercial banks across size buckets for the FFIEC Call Report benchmark.

Uses the FDIC BankFind Suite Institutions API (free, no auth) to find active commercial-bank
charters in five asset-size buckets, then samples within each. Output: data/bank_list.json.

Run: python scripts/select_banks.py [--seed N]
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

import urllib.request
import urllib.parse


FDIC_INSTITUTIONS = "https://banks.data.fdic.gov/api/institutions"

# (label, expected_form, asset_min, asset_max, sample_size) — assets in THOUSANDS (FDIC convention).
# We oversample candidates per bucket then pick by RSSDID to keep the selection deterministic
# given a seed.
BUCKETS: list[tuple[str, str, int, int, int]] = [
    ("community", "051", 100_000, 1_000_000, 5),               # $100M to $1B
    ("midsize_regional", "041", 1_000_000, 50_000_000, 5),     # $1B to $50B
    ("large_regional", "031", 50_000_000, 500_000_000, 3),     # $50B to $500B
    ("megabank", "031", 500_000_000, 5_000_000_000, 2),        # $500B+
]


def fetch_bucket(asset_min: int, asset_max: int, limit: int = 200) -> list[dict[str, Any]]:
    """Pull active commercial-bank institutions in an asset range."""
    # BKCLASS values: N (national), SM (state member), NM (state nonmember), SB (savings bank)
    # We include N, NM, SM — exclude SB/SA so we only get commercial banks filing Call Reports.
    filters = (
        f"ACTIVE:1 AND "
        f"BKCLASS:(N OR NM OR SM) AND "
        f"ASSET:[{asset_min} TO {asset_max}]"
    )
    params = {
        "filters": filters,
        "fields": "NAME,CERT,FED_RSSD,ASSET,STNAME,BKCLASS,STMULT,FDICDBS",
        "sort_by": "ASSET",
        "sort_order": "DESC",
        "limit": str(limit),
    }
    url = f"{FDIC_INSTITUTIONS}?{urllib.parse.urlencode(params, safe=':[] ')}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    rows = []
    for entry in data.get("data", []):
        d = entry["data"]
        # FED_RSSD is the FFIEC CDR identifier; some institutions lack one (rare; skip them).
        if not d.get("FED_RSSD"):
            continue
        rows.append({
            "rssd": str(d["FED_RSSD"]),
            "cert": str(d["CERT"]),
            "name": d["NAME"],
            "state": d.get("STNAME"),
            "bkclass": d.get("BKCLASS"),
            "asset_thousands": int(d["ASSET"]),
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=20260524, help="Random seed for sampling.")
    parser.add_argument("--report-date", default="2024-09-30")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/ffiec_call_reports/manifest.json"),
    )
    args = parser.parse_args()
    rng = random.Random(args.seed)

    selected: list[dict[str, Any]] = []
    for label, expected_form, lo, hi, n in BUCKETS:
        candidates = fetch_bucket(lo, hi, limit=200)
        if len(candidates) < n:
            raise RuntimeError(
                f"Not enough candidates in bucket {label} [{lo}, {hi}]: "
                f"found {len(candidates)}, needed {n}"
            )
        rng.shuffle(candidates)
        picked = candidates[:n]
        for row in picked:
            row["asset_bucket"] = label
            row["expected_form_version"] = expected_form
            row["report_date"] = args.report_date
        selected.extend(picked)
        names = ", ".join(r["name"] for r in picked)
        print(f"[{label}] {len(picked)} picks: {names}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(selected, indent=2))
    print(f"\nWrote {len(selected)} banks to {args.output}")


if __name__ == "__main__":
    main()
