"""Score the benchmark: compare each run's output.json against ground truth.

For each (doc_key, condition, field), classify as:
- na      — ground truth missing OR field marked "ignore" (cannot evaluate)
- missing — extracted is null/absent but ground truth has a value
- correct — within tolerance (0.5% relative for numerics; exact for strings)
- wrong   — value disagrees beyond tolerance
- format_error — extracted JSON malformed / schema-invalid

Writes (under results/<slug>/ by default):
- scored.csv / scored_batch.csv  (long format, one row per (doc_key, condition, field))
- summary.json / summary_batch.json (per-condition aggregates: accuracy, cost, duration)
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import pypdf

from scripts import pricing
from scripts.datasets import DEFAULT_SLUG, all_slugs, get_dataset


def local_page_count(pdf_path: Path) -> int | None:
    """Authoritative billable page count from the source PDF (full-doc extraction =>
    pages parsed == PDF page count). Returns None if the PDF can't be read."""
    try:
        return len(pypdf.PdfReader(str(pdf_path)).pages)
    except Exception:
        return None


def load_usage(run_dir: Path) -> dict[str, Any] | None:
    """API usage sidecar (num_pages_extracted etc.), if extract.py captured one.
    The current llama_cloud SDK leaves these null; kept for forward-compatible
    cross-checking against the local page count."""
    path = run_dir / "output.usage.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


REL_TOLERANCE = 0.005  # 0.5% relative tolerance for numeric matches


def compare_numeric(extracted: float | None, truth: float | None) -> tuple[str, float | None, float | None]:
    """Return (status, abs_error, rel_error)."""
    if truth is None:
        return "na", None, None
    if extracted is None:
        return "missing", None, None
    try:
        ex = float(extracted)
    except (TypeError, ValueError):
        return "format_error", None, None
    abs_err = abs(ex - truth)
    rel_err = abs_err / max(abs(truth), 1.0)
    if rel_err <= REL_TOLERANCE:
        return "correct", abs_err, rel_err
    return "wrong", abs_err, rel_err


def compare_string(extracted: str | None, truth: str | None) -> tuple[str, None, None]:
    if truth is None:
        return "na", None, None
    if extracted is None:
        return "missing", None, None
    return ("correct" if str(extracted).strip() == str(truth).strip() else "wrong"), None, None


def load_extracted(run_dir: Path, schema_cls) -> dict[str, Any] | None:
    """Read and validate output.json against the schema."""
    path = run_dir / "output.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {"_format_error": True}
    try:
        model = schema_cls.model_validate(raw)
    except Exception:
        # Return the raw dict; we'll still score what's there
        return raw if isinstance(raw, dict) else {"_format_error": True}
    return model.model_dump()


def load_result(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "result.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=all_slugs(), default=DEFAULT_SLUG)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--runs-dir", type=Path, default=None,
                        help="Defaults to runs/<slug> (per_file) or runs_batch/<slug> (batch).")
    parser.add_argument("--gt-dir", type=Path, default=None)
    parser.add_argument("--csv-out", type=Path, default=None)
    parser.add_argument("--summary-out", type=Path, default=None)
    parser.add_argument("--mode", choices=["per_file", "batch"], default="per_file",
                        help="per_file = sum per-doc result.json files for cost/duration; "
                             "batch = read one session_result.json per condition.")
    parser.add_argument("--session-result-template", type=str, default=None,
                        help="Where to read session-level cost/duration in batch mode. "
                             "Defaults to runs_batch/<slug>/{condition}[__<run-tag>]/session_result.json.")
    parser.add_argument("--run-tag", dest="run_tag", default="",
                        help="Score a namespaced batch run (<condition>__<tag>) instead of the "
                             "canonical dirs. Matches run_benchmark.py --run-tag. Empty = canonical.")
    args = parser.parse_args()

    dataset = get_dataset(args.dataset)
    manifest_path = args.manifest or dataset.manifest_path()
    gt_dir = args.gt_dir or dataset.gt_dir()
    if args.runs_dir is not None:
        runs_dir = args.runs_dir
    else:
        runs_dir = dataset.runs_dir() if args.mode == "per_file" else dataset.runs_batch_dir()
    results_dir = dataset.results_dir()
    csv_out = args.csv_out or (results_dir / ("scored_batch.csv" if args.mode == "batch" else "scored.csv"))
    summary_out = args.summary_out or (results_dir / ("summary_batch.json" if args.mode == "batch" else "summary.json"))
    session_result_template = args.session_result_template or str(
        dataset.runs_batch_dir()
        / (f"{{condition}}__{args.run_tag}" if args.run_tag else "{condition}")
        / "session_result.json")

    records = json.loads(manifest_path.read_text())
    schema_fields = list(dataset.schema_cls.model_fields.keys())
    compare_overrides = dataset.field_compare_overrides
    rows: list[dict[str, Any]] = []
    cond_agg: dict[str, dict[str, Any]] = {
        c: {
            "n_correct": 0, "n_wrong": 0, "n_missing": 0, "n_na": 0, "n_format_error": 0,
            "token_cost_usd": 0.0, "total_duration_ms": 0.0,
            "n_pages": 0, "n_pages_api": 0, "page_count_mismatches": [],
            "extract_tier": None, "parse_tier": None,
            "n_runs": 0, "n_runs_with_output": 0, "n_runs_with_result": 0,
        }
        for c in ("with_skill", "no_skill")
    }

    for record in records:
        doc_key = dataset.doc_key_fn(record)
        gt_path = gt_dir / f"{doc_key}.json"
        if not gt_path.exists():
            print(f"WARN: no ground truth for {doc_key}, skipping")
            continue
        gt = json.loads(gt_path.read_text())
        truth = gt.get("values", {})

        for condition in ("with_skill", "no_skill"):
            if args.mode == "batch":
                run_dir = dataset.batch_fanout_dir(record, condition, args.run_tag)
            else:
                run_dir = runs_dir / f"{doc_key}_{condition}"
            extracted = load_extracted(run_dir, dataset.schema_cls)
            result = load_result(run_dir)
            agg = cond_agg[condition]
            agg["n_runs"] += 1
            if extracted is not None:
                agg["n_runs_with_output"] += 1
            # Billable LlamaExtract pages apply only to the with_skill path. The local
            # PDF page count is authoritative for credit cost (full-doc extraction =>
            # pages parsed == PDF pages); the API usage sidecar, when populated, is
            # cross-checked and any divergence is flagged.
            if condition == "with_skill":
                local_pages = local_page_count(dataset.pdf_path(record))
                if local_pages is not None:
                    agg["n_pages"] += local_pages
                usage = load_usage(run_dir)
                api_pages = usage.get("num_pages_extracted") if usage else None
                if api_pages is not None:
                    agg["n_pages_api"] += int(api_pages)
                    if local_pages is not None and int(api_pages) != local_pages:
                        agg["page_count_mismatches"].append(
                            {"doc_key": doc_key, "api_pages": int(api_pages),
                             "local_pages": local_pages})
            # Per-doc cost/duration accumulation only meaningful in per_file mode;
            # batch mode pulls one session_result.json per condition after the loop.
            if args.mode == "per_file" and result:
                agg["n_runs_with_result"] += 1
                agg["token_cost_usd"] += float(result.get("total_cost_usd") or 0.0)
                agg["total_duration_ms"] += float(result.get("duration_ms") or 0.0)

            for field in schema_fields:
                truth_val = truth.get(field)
                rule = compare_overrides.get(field, "numeric")
                if rule == "ignore":
                    status, abs_err, rel_err = "na", None, None
                elif extracted is None:
                    status, abs_err, rel_err = ("missing" if truth_val is not None else "na"), None, None
                elif extracted.get("_format_error"):
                    status, abs_err, rel_err = "format_error", None, None
                else:
                    ex_val = extracted.get(field)
                    if rule == "string":
                        status, abs_err, rel_err = compare_string(ex_val, truth_val)
                    else:
                        status, abs_err, rel_err = compare_numeric(ex_val, truth_val)
                agg[f"n_{status}"] += 1
                rows.append({
                    "doc_key": doc_key,
                    "name": record.get("name"),
                    "condition": condition,
                    "field": field,
                    "extracted": extracted.get(field) if isinstance(extracted, dict) else None,
                    "truth": truth_val,
                    "status": status,
                    "abs_error": abs_err,
                    "rel_error": rel_err,
                })

    # In batch mode, read cost/duration from one session_result.json per condition.
    session_extras: dict[str, dict[str, Any]] = {c: {} for c in ("with_skill", "no_skill")}
    if args.mode == "batch":
        for c in ("with_skill", "no_skill"):
            sr_path = Path(session_result_template.format(condition=c))
            if sr_path.exists():
                sr = json.loads(sr_path.read_text())
                wall_s = sr.get("wall_seconds_session")
                if wall_s is not None:
                    cond_agg[c]["total_duration_ms"] = float(wall_s) * 1000.0
                else:
                    cond_agg[c]["total_duration_ms"] = float(sr.get("duration_ms") or 0.0)
                cond_agg[c]["token_cost_usd"] = float(sr.get("total_cost_usd") or 0.0)
                cond_agg[c]["extract_tier"] = sr.get("extract_tier")
                cond_agg[c]["parse_tier"] = sr.get("parse_tier")
                cond_agg[c]["n_runs_with_result"] = 1
                session_extras[c]["session_num_turns"] = sr.get("num_turns")
                session_extras[c]["session_duration_api_ms"] = sr.get("duration_api_ms")
                session_extras[c]["session_stop_reason"] = sr.get("stop_reason")
            else:
                print(f"WARN: batch mode but no session_result.json at {sr_path}")

    # Compute accuracy = correct / (correct + wrong + missing + format_error)
    summary: dict[str, dict[str, Any]] = {}
    for c, agg in cond_agg.items():
        denom = agg["n_correct"] + agg["n_wrong"] + agg["n_missing"] + agg["n_format_error"]
        accuracy = (agg["n_correct"] / denom) if denom > 0 else None
        # Credit cost = billable pages x per-page credits (extract tier + parse tier)
        # x $/credit. Pages accrue only on the with_skill (LlamaExtract) path, so
        # no_skill is structurally zero.
        et = agg["extract_tier"] or pricing.DEFAULT_EXTRACT_TIER
        pt = agg["parse_tier"] or pricing.DEFAULT_PARSE_TIER
        credit = (pricing.credit_cost_usd(agg["n_pages"], et, pt) or 0.0) if agg["n_pages"] else 0.0
        token = round(agg["token_cost_usd"], 4)
        credit = round(credit, 4)
        if agg["page_count_mismatches"] and c == "with_skill":
            print(f"WARN: {len(agg['page_count_mismatches'])} page-count mismatch(es) "
                  f"between API and local PDF for with_skill: {agg['page_count_mismatches']}")
        entry: dict[str, Any] = {
            "accuracy": accuracy,
            "n_correct": agg["n_correct"],
            "n_wrong": agg["n_wrong"],
            "n_missing": agg["n_missing"],
            "n_na": agg["n_na"],
            "n_format_error": agg["n_format_error"],
            "n_runs": agg["n_runs"],
            "n_runs_with_output": agg["n_runs_with_output"],
            "token_cost_usd": token,
            "credit_cost_usd": credit,
            "total_cost_usd": round(token + credit, 4),
            "n_pages": agg["n_pages"] or None,
            "n_pages_api": agg["n_pages_api"] or None,
            "page_count_mismatches": agg["page_count_mismatches"],
            "extract_tier": agg["extract_tier"],
            "parse_tier": agg["parse_tier"],
            "total_duration_ms": int(agg["total_duration_ms"]),
        }
        if args.mode == "per_file":
            mean_dur = (agg["total_duration_ms"] / agg["n_runs_with_result"]) if agg["n_runs_with_result"] else None
            entry["mean_duration_ms"] = int(mean_dur) if mean_dur is not None else None
        else:  # batch — single session, mean is meaningless
            entry["session_duration_ms"] = int(agg["total_duration_ms"])
            entry.update(session_extras[c])
        summary[c] = entry

    csv_out.parent.mkdir(parents=True, exist_ok=True)
    with csv_out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        if rows:
            writer.writeheader()
            writer.writerows(rows)
    summary_out.parent.mkdir(parents=True, exist_ok=True)
    summary_out.write_text(json.dumps(summary, indent=2))
    print(f"Wrote {len(rows)} rows to {csv_out}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
