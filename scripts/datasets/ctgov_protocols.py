"""ClinicalTrials.gov protocol+SAP dataset config.

Ground truth: protocolSection JSON from the v2 API. PDFs: cdn.clinicaltrials.gov.
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from scripts.datasets.base import DatasetConfig


API = "https://clinicaltrials.gov/api/v2/studies"
UA = "extract-bench/0.1 (research benchmark) github.com/jerryjliu/agent_extract_experiments"


class CTGovProtocolExtraction(BaseModel):
    """Target schema: ~20 fields covering identification, design, eligibility, outcomes."""

    # Identification
    nct_id: Optional[str] = Field(default=None, description="National Clinical Trial number (e.g., NCT05386329). On the cover/title page.")
    brief_title: Optional[str] = Field(default=None, description="Brief title as listed on the cover page.")
    official_title: Optional[str] = Field(default=None, description="Official protocol title (the full formal study title).")
    sponsor: Optional[str] = Field(default=None, description="Lead sponsor name from the protocol cover page.")

    # Design
    study_type: Optional[str] = Field(default=None, description='Study type: one of "INTERVENTIONAL", "OBSERVATIONAL", or "EXPANDED_ACCESS".')
    phase: Optional[str] = Field(default=None, description='Phase string: e.g., "PHASE2", "PHASE3", "PHASE1|PHASE2" (join multiple with |). "NA" if not applicable.')
    allocation: Optional[str] = Field(default=None, description='Allocation: "RANDOMIZED" or "NON_RANDOMIZED".')
    masking: Optional[str] = Field(default=None, description='Masking model: "NONE", "SINGLE", "DOUBLE", "TRIPLE", or "QUADRUPLE".')
    intervention_model: Optional[str] = Field(default=None, description='Intervention model: e.g., "PARALLEL", "CROSSOVER", "SINGLE_GROUP", "SEQUENTIAL", "FACTORIAL".')

    # Enrollment
    enrollment_count: Optional[int] = Field(default=None, description="Total target enrollment (integer count of subjects).")

    # Dates (ISO format YYYY-MM-DD)
    start_date: Optional[str] = Field(default=None, description="Study start date in ISO format YYYY-MM-DD.")
    primary_completion_date: Optional[str] = Field(default=None, description="Primary completion date in ISO format YYYY-MM-DD.")
    completion_date: Optional[str] = Field(default=None, description="Overall study completion date in ISO format YYYY-MM-DD.")

    # Eligibility
    minimum_age: Optional[str] = Field(default=None, description='Minimum age as written, e.g., "18 Years".')
    maximum_age: Optional[str] = Field(default=None, description='Maximum age as written, e.g., "75 Years", or "N/A" if no upper limit.')
    sex: Optional[str] = Field(default=None, description='Eligible sex: "ALL", "FEMALE", or "MALE".')
    healthy_volunteers: Optional[bool] = Field(default=None, description="True if healthy volunteers are accepted, false otherwise.")

    # Outcomes
    primary_outcome_measure: Optional[str] = Field(default=None, description="Primary outcome measure name as written (the first primary outcome).")
    primary_outcome_time_frame: Optional[str] = Field(default=None, description='Time frame of the primary outcome, e.g., "24 weeks".')

    # Status
    overall_status: Optional[str] = Field(default=None, description='Overall recruitment status: e.g., "RECRUITING", "COMPLETED", "TERMINATED", "ACTIVE_NOT_RECRUITING".')


def _get(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def _fetch_study(nct_id: str) -> dict:
    return _get(f"{API}/{nct_id}?format=json")


def _build_gt(record: dict[str, Any]) -> dict[str, Any]:
    study = _fetch_study(record["nct_id"])
    proto = study.get("protocolSection", {})
    ident = proto.get("identificationModule", {})
    design = proto.get("designModule", {})
    elig = proto.get("eligibilityModule", {})
    status_mod = proto.get("statusModule", {})
    outcomes = proto.get("outcomesModule", {})
    sponsor_mod = proto.get("sponsorCollaboratorsModule", {})

    def first_primary(field: str) -> Optional[str]:
        po = outcomes.get("primaryOutcomes") or []
        return po[0].get(field) if po else None

    design_info = design.get("designInfo") or {}
    enrollment_info = design.get("enrollmentInfo") or {}
    masking_info = design_info.get("maskingInfo") or {}

    values: dict[str, Any] = {
        "nct_id": ident.get("nctId"),
        "brief_title": ident.get("briefTitle"),
        "official_title": ident.get("officialTitle"),
        "sponsor": (sponsor_mod.get("leadSponsor") or {}).get("name"),
        "study_type": design.get("studyType"),
        "phase": "|".join(design.get("phases") or []) or None,
        "allocation": design_info.get("allocation"),
        "masking": masking_info.get("masking"),
        "intervention_model": design_info.get("interventionModel"),
        "enrollment_count": enrollment_info.get("count"),
        "start_date": (status_mod.get("startDateStruct") or {}).get("date"),
        "primary_completion_date": (status_mod.get("primaryCompletionDateStruct") or {}).get("date"),
        "completion_date": (status_mod.get("completionDateStruct") or {}).get("date"),
        "minimum_age": elig.get("minimumAge"),
        "maximum_age": elig.get("maximumAge"),
        "sex": elig.get("sex"),
        "healthy_volunteers": elig.get("healthyVolunteers"),
        "primary_outcome_measure": first_primary("measure"),
        "primary_outcome_time_frame": first_primary("timeFrame"),
        "overall_status": status_mod.get("overallStatus"),
    }
    # Normalize dates that arrive as YYYY-MM to first-of-month ISO.
    for k in ("start_date", "primary_completion_date", "completion_date"):
        v = values.get(k)
        if isinstance(v, str) and len(v) == 7:  # "YYYY-MM"
            values[k] = f"{v}-01"
    return {
        "nct_id": record["nct_id"],
        "doc_id": record["doc_id"],
        "label": record.get("label"),
        "values": values,
        "populated_count": sum(1 for v in values.values() if v is not None),
        "total_fields": len(values),
    }


def _fetch_pdfs(records: list[dict[str, Any]], pdf_dir: Path) -> tuple[int, int]:
    """Download each protocol PDF from cdn.clinicaltrials.gov. Returns (n_present, n_total)."""
    pdf_dir.mkdir(parents=True, exist_ok=True)
    n_present = 0
    for r in records:
        nct = r["nct_id"]
        doc = r["doc_id"]
        out = pdf_dir / f"{nct}_{doc}.pdf"
        if out.exists() and out.stat().st_size > 0:
            n_present += 1
            continue
        shard = nct[-2:]
        url = f"https://cdn.clinicaltrials.gov/large-docs/{shard}/{nct}/{doc}.pdf"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as resp, out.open("wb") as f:
                f.write(resp.read())
            n_present += 1
        except Exception as e:  # noqa: BLE001
            print(f"  {nct}: failed to fetch {url}: {e}")
            if out.exists():
                out.unlink()
    return n_present, len(records)


_DOMAIN_INTRO = """\
# What is a ClinicalTrials.gov protocol document?

A protocol + statistical analysis plan (SAP) is the formal study design document a clinical trial registers with the NIH ClinicalTrials.gov registry. Each PDF combines a study protocol (background, objectives, eligibility, study design, intervention, outcome measures) and a statistical analysis plan (sample size, endpoints, statistical methods). Documents range 50-200 pages and are author-submitted as PDF/A.

The cover and synopsis pages typically declare: the NCT number, the title (brief + official), the lead sponsor, the study type, the phase, the enrollment target, and key dates. Eligibility criteria (minimum/maximum age, sex, healthy-volunteer acceptance) appear in a dedicated eligibility section. Primary and secondary outcome measures, each with a name and a "time frame" expression (e.g., "Progression-free survival at 24 weeks"), appear in the outcomes/endpoints section. Each schema field's description tells you which section to look in."""


_UNIT_CONVENTIONS = """\
# Conventions

- **Identifiers / titles / sponsor**: write strings exactly as printed on the cover/synopsis pages.
- **Phase**: write the registry-style uppercase token ("PHASE1", "PHASE2", "PHASE3", "PHASE4", "EARLY_PHASE1", "NA"). If the protocol declares multiple phases, join with "|" (e.g., "PHASE1|PHASE2").
- **study_type / allocation / masking / intervention_model / sex / overall_status**: write the canonical uppercase registry token ("INTERVENTIONAL", "RANDOMIZED", "DOUBLE", "PARALLEL", "ALL", "RECRUITING", etc.).
- **enrollment_count**: an integer count of total target enrolled subjects (not arms × subjects-per-arm).
- **healthy_volunteers**: a JSON boolean (true/false).
- **Dates**: ISO format "YYYY-MM-DD". If the protocol gives only month/year, use the first day of the month.
- **Missing fields**: if the protocol does not state a value, write `null`."""


# Fields scored by exact string match: identifiers, sponsor, and the categorical
# design/eligibility tokens that the protocol document actually contains.
_STRING_FIELDS = [
    "nct_id", "brief_title", "sponsor",
    "study_type", "phase", "allocation", "masking", "intervention_model",
    "minimum_age", "maximum_age", "sex", "healthy_volunteers",
]
# Not scored: long narrative free-text (exact match is methodologically wrong) and
# registry administrative metadata that the protocol PDF does not reliably contain
# (those dates / recruitment status come from separate registry submissions, so the
# document genuinely lacks them — scoring them would penalize both conditions for GT
# the source document never had).
_IGNORE_FIELDS = [
    "official_title", "primary_outcome_measure", "primary_outcome_time_frame",
    "start_date", "primary_completion_date", "completion_date", "overall_status",
]


CONFIG = DatasetConfig(
    slug="ctgov_protocols",
    display_name="ClinicalTrials.gov protocol",
    schema_cls=CTGovProtocolExtraction,
    doc_key_fn=lambda r: f"{r['nct_id']}_{r['doc_id']}",
    field_compare_overrides={**{f: "string" for f in _STRING_FIELDS},
                             **{f: "ignore" for f in _IGNORE_FIELDS}},
    domain_intro=_DOMAIN_INTRO,
    unit_conventions=_UNIT_CONVENTIONS,
    gt_builder=_build_gt,
    pdf_fetcher=_fetch_pdfs,
)
