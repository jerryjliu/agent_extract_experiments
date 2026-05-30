"""Seed data/irs_form_990/manifest.json.

For each candidate nonprofit EIN, query ProPublica's Nonprofit Explorer for the
most recent filing that has BOTH a downloadable PDF (pdf_url) and structured
financial data (totrevenue present). Writes up to --n manifest entries.

Run: python scripts/seed_irs990_manifest.py [--n 12]
"""
from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

UA = {"User-Agent": "extract-bench/0.1 (research benchmark) github.com/jerryjliu/agent_extract_experiments"}
PP_API = "https://projects.propublica.org/nonprofits/api/v2"

# Well-known US 501(c)(3)s spanning sizes/sectors (foundations, universities,
# hospitals, museums, advocacy). EINs verified against ProPublica.
CANDIDATE_EINS = [
    ("200049703", "Wikimedia Foundation"),
    ("530196605", "American National Red Cross"),
    ("131624016", "American Cancer Society"),
    ("131644147", "United Way Worldwide"),
    ("135562308", "Planned Parenthood Federation"),
    ("042103594", "Harvard College (President & Fellows)"),
    ("941156365", "Stanford University (Leland Stanford Jr)"),
    ("042103580", "Massachusetts General Hospital"),
    ("131739919", "Metropolitan Museum of Art"),
    ("530242652", "Smithsonian-affiliated / Nature Conservancy"),
    ("521693387", "Nature Conservancy"),
    ("237069110", "Doctors Without Borders USA (MSF)"),
    ("133433452", "Robin Hood Foundation"),
    ("954681287", "Conservation International"),
    ("204618854", "Khan Academy"),
    ("581417976", "Habitat for Humanity International"),
    ("530242611", "World Wildlife Fund"),
    ("131760110", "Boy Scouts of America (National)"),
]


def _get(url: str) -> dict:
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read())


def _best_filing(ein: str) -> dict | None:
    """Most recent filing with pdf_url and totrevenue present."""
    try:
        org = _get(f"{PP_API}/organizations/{ein}.json")
    except Exception as e:  # noqa: BLE001
        print(f"  {ein}: fetch failed: {e}")
        return None
    name = (org.get("organization") or {}).get("name")
    filings = sorted((org.get("filings_with_data") or []),
                     key=lambda f: f.get("tax_prd_yr") or 0, reverse=True)
    for f in filings:
        if f.get("pdf_url") and f.get("totrevenue") is not None:
            return {"ein": ein, "tax_year": f["tax_prd_yr"], "name": name}
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=12)
    parser.add_argument("--output", type=Path, default=Path("data/irs_form_990/manifest.json"))
    args = parser.parse_args()

    selected: list[dict] = []
    for ein, label in CANDIDATE_EINS:
        if len(selected) >= args.n:
            break
        rec = _best_filing(ein)
        if rec:
            selected.append(rec)
            print(f"  + {rec['ein']} TY{rec['tax_year']} — {rec['name']}")
        else:
            print(f"  - {ein} ({label}): no usable filing")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(selected, indent=2))
    print(f"\nWrote {len(selected)} entries to {args.output}")


if __name__ == "__main__":
    main()
