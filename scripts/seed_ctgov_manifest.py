"""Seed data/ctgov_protocols/manifest.json.

Queries the ClinicalTrials.gov v2 API for interventional trials that have a
Study Protocol + SAP PDF, downloads each candidate's protocol doc just far enough
to read its size, and selects N whose PDF size falls in a band that proxies the
50-150 page target (~250 KB - 3 MB). Writes the manifest.

Run: python scripts/seed_ctgov_manifest.py [--n 12]
"""
from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from pathlib import Path

UA = {"User-Agent": "extract-bench/0.1 (research benchmark) github.com/jerryjliu/agent_extract_experiments"}
API = "https://clinicaltrials.gov/api/v2/studies"
MIN_BYTES = 250_000      # ~50+ pages of text/tables
MAX_BYTES = 3_000_000    # keep under ~150-200 pages


def _get(url: str) -> dict:
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read())


def _content_length(url: str) -> int | None:
    try:
        req = urllib.request.Request(url, headers=UA, method="HEAD")
        with urllib.request.urlopen(req, timeout=60) as r:
            cl = r.headers.get("Content-Length")
            return int(cl) if cl else None
    except Exception:  # noqa: BLE001
        return None


def _candidates(conditions: list[str], page_size: int = 40) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for cond in conditions:
        params = {
            "query.cond": cond,
            "aggFilters": "docs:prot,studyType:int",
            "pageSize": str(page_size),
            "fields": "NCTId,BriefTitle,Phase",
        }
        url = f"{API}?{urllib.parse.urlencode(params)}"
        try:
            r = _get(url)
        except Exception as e:  # noqa: BLE001
            print(f"  search failed for {cond}: {e}")
            continue
        for s in r.get("studies", []):
            nct = s["protocolSection"]["identificationModule"].get("nctId")
            if nct and nct not in seen:
                seen.add(nct)
                out.append({"nct_id": nct,
                            "label": s["protocolSection"]["identificationModule"].get("briefTitle", "")[:60]})
    return out


def _protocol_doc(nct: str) -> dict | None:
    """Return {filename} of the protocol largeDoc for an NCT, or None."""
    try:
        s = _get(f"{API}/{nct}?format=json")
    except Exception:  # noqa: BLE001
        return None
    docs = ((s.get("documentSection") or {}).get("largeDocumentModule") or {}).get("largeDocs") or []
    for d in docs:
        if d.get("hasProtocol") and (d.get("filename") or "").lower().endswith(".pdf"):
            return d
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=12)
    parser.add_argument("--output", type=Path, default=Path("data/ctgov_protocols/manifest.json"))
    args = parser.parse_args()

    conditions = ["cancer", "diabetes", "heart failure", "alzheimer", "asthma", "depression"]
    cands = _candidates(conditions)
    print(f"{len(cands)} candidate trials with protocol docs")

    selected: list[dict] = []
    for c in cands:
        if len(selected) >= args.n:
            break
        nct = c["nct_id"]
        doc = _protocol_doc(nct)
        if not doc:
            continue
        filename = doc["filename"]
        doc_id = filename[:-4] if filename.lower().endswith(".pdf") else filename
        shard = nct[-2:]
        url = f"https://cdn.clinicaltrials.gov/large-docs/{shard}/{nct}/{filename}"
        size = _content_length(url)
        if size is None or not (MIN_BYTES <= size <= MAX_BYTES):
            continue
        selected.append({"nct_id": nct, "doc_id": doc_id, "label": c["label"],
                         "_pdf_bytes": size})
        print(f"  + {nct} {doc_id} ({size//1024} KB) — {c['label']}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Strip the helper _pdf_bytes from the written manifest (keep it tidy).
    manifest = [{k: v for k, v in r.items() if not k.startswith("_")} for r in selected]
    args.output.write_text(json.dumps(manifest, indent=2))
    print(f"\nWrote {len(manifest)} entries to {args.output}")


if __name__ == "__main__":
    main()
