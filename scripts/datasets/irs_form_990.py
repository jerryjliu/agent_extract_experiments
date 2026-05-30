"""IRS Form 990 dataset config.

Ground truth: ProPublica Nonprofit Explorer's structured filing fields (the API
returns the parsed line-item values directly, derived from the IRS e-file data).
PDFs: ProPublica's rendered filing PDFs (pdf_url).

The schema is scoped to the line items ProPublica exposes reliably across orgs so
that every scored field has authoritative ground truth.
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from scripts.datasets.base import DatasetConfig


PP_API = "https://projects.propublica.org/nonprofits/api/v2"
UA = "extract-bench/0.1 (research benchmark) github.com/jerryjliu/agent_extract_experiments"


class Form990Extraction(BaseModel):
    """~15 fields from Form 990 Part I (summary), Part IX (expenses)."""

    # Identifying
    ein: Optional[str] = Field(default=None, description="Employer Identification Number from the heading (box D), 9 digits no dashes.")
    legal_name: Optional[str] = Field(default=None, description="Organization legal name from the heading (box C).")
    tax_year: Optional[int] = Field(default=None, description="Tax year the return covers, 4-digit integer (e.g., 2022). From the header / tax period (the calendar year in which the tax period begins).")

    # Part I summary — revenue (Current Year column)
    total_revenue: Optional[float] = Field(default=None, description="Part I, line 12 — Total revenue (current year), USD.")
    contributions_grants: Optional[float] = Field(default=None, description="Part I, line 8 — Contributions and grants (current year), USD.")
    program_service_revenue: Optional[float] = Field(default=None, description="Part I, line 9 — Program service revenue (current year), USD.")
    investment_income: Optional[float] = Field(default=None, description="Part I, line 10 — Investment income (current year), USD.")

    # Part I summary — expenses (Current Year column)
    total_expenses: Optional[float] = Field(default=None, description="Part I, line 18 — Total expenses (current year), USD.")
    revenue_less_expenses: Optional[float] = Field(default=None, description="Part I, line 19 — Revenue less expenses (current year), USD. May be negative.")

    # Part IX — functional expenses (column A, total)
    compensation_current_officers: Optional[float] = Field(default=None, description="Part IX, line 5, column (A) — Compensation of current officers, directors, trustees, and key employees, USD.")
    other_salaries_wages: Optional[float] = Field(default=None, description="Part IX, line 7, column (A) — Other salaries and wages, USD.")
    professional_fundraising_fees: Optional[float] = Field(default=None, description="Part IX, line 11e, column (A) — Professional fundraising services, USD. Often 0 or blank.")

    # Part I — balance sheet (end of year)
    total_assets_eoy: Optional[float] = Field(default=None, description="Part I, line 20 — Total assets (end of year), USD.")
    total_liabilities_eoy: Optional[float] = Field(default=None, description="Part I, line 21 — Total liabilities (end of year), USD.")
    net_assets_eoy: Optional[float] = Field(default=None, description="Part I, line 22 — Net assets or fund balances (end of year), USD.")


# Schema field -> ProPublica filing field. revenue_less_expenses is computed.
PP_FIELD_MAP: dict[str, str] = {
    "total_revenue": "totrevenue",
    "contributions_grants": "totcntrbgfts",
    "program_service_revenue": "totprgmrevnue",
    "investment_income": "invstmntinc",
    "total_expenses": "totfuncexpns",
    "compensation_current_officers": "compnsatncurrofcr",
    "other_salaries_wages": "othrsalwages",
    "professional_fundraising_fees": "profndraising",
    "total_assets_eoy": "totassetsend",
    "total_liabilities_eoy": "totliabend",
    "net_assets_eoy": "totnetassetend",
}


def _get(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def _pp_org(ein: str) -> dict:
    return _get(f"{PP_API}/organizations/{ein}.json")


def _match_filing(org: dict, tax_year: int) -> Optional[dict]:
    for f in (org.get("filings_with_data") or []):
        if f.get("tax_prd_yr") == tax_year:
            return f
    return None


def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _build_gt(record: dict[str, Any]) -> dict[str, Any]:
    org = _pp_org(record["ein"])
    name = (org.get("organization") or {}).get("name") or record.get("name")
    # gt_tax_year may differ from the doc-key tax_year: ProPublica labels fiscal-year
    # filers by a period-end year that can be off-by-one from the year printed on the
    # PDF. seed/realignment sets gt_tax_year to the ProPublica year whose totals match
    # the PDF. The doc key (and PDF filename) still uses tax_year.
    lookup_year = record.get("gt_tax_year", record["tax_year"])
    match = _match_filing(org, lookup_year)
    if match is None:
        return {"ein": record["ein"], "tax_year": record["tax_year"], "name": name,
                "error": f"no ProPublica filing for tax_prd_yr {lookup_year}", "values": {}}

    values: dict[str, Any] = {
        "ein": record["ein"],
        "legal_name": name,
        "tax_year": record["tax_year"],
    }
    for field, pp_key in PP_FIELD_MAP.items():
        values[field] = _num(match.get(pp_key))
    tr, te = values.get("total_revenue"), values.get("total_expenses")
    values["revenue_less_expenses"] = (tr - te) if (tr is not None and te is not None) else None

    return {
        "ein": record["ein"],
        "tax_year": record["tax_year"],
        "name": name,
        "values": values,
        "populated_count": sum(1 for v in values.values() if v is not None),
        "total_fields": len(values),
    }


BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def _fetch_pdfs(records: list[dict[str, Any]], pdf_dir: Path) -> tuple[int, int]:
    """Download each filing's PDF.

    Each manifest record must carry a ``pdf_url`` pointing at a freely-downloadable
    real Form 990 PDF (organizations' own published returns / annual-report pages).
    ProPublica's own download endpoint is Cloudflare-blocked for automation, so the
    PDF link is curated in the manifest and the ProPublica API is used only for the
    structured ground truth.
    """
    pdf_dir.mkdir(parents=True, exist_ok=True)
    n_present = 0
    for r in records:
        out = pdf_dir / f"{r['ein']}_{r['tax_year']}.pdf"
        if out.exists() and out.stat().st_size > 0:
            n_present += 1
            continue
        pdf_url = r.get("pdf_url")
        if not pdf_url:
            print(f"  {r['ein']}/{r['tax_year']}: no pdf_url in manifest record")
            continue
        try:
            req = urllib.request.Request(pdf_url, headers={
                "User-Agent": BROWSER_UA,
                "Accept": "application/pdf,*/*",
            })
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = resp.read()
            if data[:4] != b"%PDF":
                print(f"  {r['ein']}/{r['tax_year']}: not a PDF (head={data[:8]!r}) from {pdf_url}")
                continue
            out.write_bytes(data)
            n_present += 1
        except Exception as e:  # noqa: BLE001
            print(f"  {r['ein']}/{r['tax_year']}: failed: {e}")
            if out.exists():
                out.unlink()
    return n_present, len(records)


_DOMAIN_INTRO = """\
# What is a Form 990?

IRS Form 990 is the annual information return that US tax-exempt nonprofits file with the IRS. Each return runs ~12 pages of core form plus schedules; large nonprofits file 50-150 page packets.

Key locations for the values in this task:
- **Heading (page 1, top)**: tax year / tax period, EIN (box D), legal name (box C).
- **Part I — Summary** (page 1): line 8 (contributions and grants), line 9 (program service revenue), line 10 (investment income), line 12 (total revenue), line 18 (total expenses), line 19 (revenue less expenses), line 20 (total assets, end of year), line 21 (total liabilities, end of year), line 22 (net assets or fund balances, end of year). Part I shows a "Current Year" column — use that.
- **Part IX — Statement of Functional Expenses**: line 5 column (A) (compensation of current officers/directors/trustees/key employees), line 7 column (A) (other salaries and wages), line 11e column (A) (professional fundraising services). Use the Total column (A)."""


_UNIT_CONVENTIONS = """\
# Conventions

- **Dollar amounts**: write raw USD as numbers (Form 990 reports raw dollars — no thousands multiplier). Values may be negative (e.g., revenue less expenses).
- **EIN**: 9-digit string, no dashes (e.g., "200049703", not "20-0049703").
- **tax_year**: 4-digit integer of the year the tax period begins (e.g., 2022).
- **professional_fundraising_fees**: write `0` if the line is blank/zero; `null` only if the line is genuinely absent.
- **Missing fields**: write `null` if a field is not present on this return."""


CONFIG = DatasetConfig(
    slug="irs_form_990",
    display_name="IRS Form 990",
    schema_cls=Form990Extraction,
    doc_key_fn=lambda r: f"{r['ein']}_{r['tax_year']}",
    # legal_name GT is ProPublica's normalized name (won't match the PDF box C
    # verbatim), so it is not scored; ein is the identity check.
    field_compare_overrides={"ein": "string", "legal_name": "ignore"},
    domain_intro=_DOMAIN_INTRO,
    unit_conventions=_UNIT_CONVENTIONS,
    gt_builder=_build_gt,
    pdf_fetcher=_fetch_pdfs,
)
