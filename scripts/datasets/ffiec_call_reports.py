"""FFIEC Call Reports dataset config.

Schema, FDIC ground-truth fetcher, and CDR download-instructions generator,
moved here from the old scripts/schema.py, scripts/build_ground_truth.py, and
scripts/fetch_call_reports.py. Behaviour is preserved verbatim.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from scripts.datasets.base import DatasetConfig


# ---------------------------------------------------------------------------
# Schema (moved verbatim from scripts/schema.py)
# ---------------------------------------------------------------------------
class CallReportExtraction(BaseModel):
    """Target schema for FFIEC Call Report extraction.

    All dollar amounts are in RAW DOLLARS (not thousands). Ratios are decimal fractions
    (e.g., 0.1234 for 12.34%). Use None for fields that do not apply to the bank's form
    version (e.g., risk-based capital ratios for CBLR-electing community banks).
    """

    form_version: str = Field(
        description=(
            "Which FFIEC form this Call Report uses: '031' (banks with foreign offices), "
            "'041' (domestic-only, standard), or '051' (small banks <$5B, abbreviated). "
            "Visible on the cover page header."
        )
    )

    # Balance sheet (Schedule RC)
    total_assets: Optional[float] = Field(
        default=None,
        description=(
            "Total assets in USD (raw dollars, not thousands). "
            "MDRM RCFD2170 on FFIEC 031/041; RCON2170 on FFIEC 051. "
            "Schedule RC, item 12. Multiply the printed value by 1000 since the form reports in thousands."
        ),
    )
    total_deposits: Optional[float] = Field(
        default=None,
        description=(
            "Total deposits in USD (raw dollars). MDRM RCON2200 on all forms. "
            "Schedule RC, item 13.a. Multiply by 1000."
        ),
    )
    total_loans_net: Optional[float] = Field(
        default=None,
        description=(
            "Total loans and leases, net of unearned income, in USD (raw dollars). "
            "MDRM RCFD2122 on 031/041; RCON2122 on 051. Schedule RC, item 4.b. Multiply by 1000."
        ),
    )
    allowance_credit_losses_loans: Optional[float] = Field(
        default=None,
        description=(
            "Allowance for credit losses on loans and leases (post-CECL), USD raw dollars. "
            "MDRM RCFD3123 on 031/041; RCON3123 on 051. Schedule RC, item 4.c. Multiply by 1000."
        ),
    )
    total_liabilities: Optional[float] = Field(
        default=None,
        description=(
            "Total liabilities in USD (raw dollars). MDRM RCFD2948 on 031/041; "
            "RCON2948 on 051. Schedule RC, item 21. Multiply by 1000."
        ),
    )
    total_equity_capital: Optional[float] = Field(
        default=None,
        description=(
            "Total equity capital in USD (raw dollars). MDRM RCFD3210 on 031/041; "
            "RCON3210 on 051. Schedule RC, item 27.a + 27.b. Multiply by 1000."
        ),
    )
    total_securities: Optional[float] = Field(
        default=None,
        description=(
            "Total securities (available-for-sale + held-to-maturity), USD raw dollars. "
            "MDRM RCFD1773 on 031/041; RCON1773 on 051. Schedule RC, item 2.b. Multiply by 1000."
        ),
    )
    trading_assets: Optional[float] = Field(
        default=None,
        description=(
            "Trading assets in USD (raw dollars). MDRM RCFD3545 on 031/041; "
            "RCON3545 on 051. Schedule RC, item 5. Often zero for community banks. Multiply by 1000."
        ),
    )
    intangible_assets: Optional[float] = Field(
        default=None,
        description=(
            "Total intangible assets (includes goodwill + other intangibles), USD raw dollars. "
            "MDRM RCFD2143 on 031/041; RCON2143 on 051. Schedule RC, item 10. Multiply by 1000."
        ),
    )
    oreo: Optional[float] = Field(
        default=None,
        description=(
            "Other real estate owned (foreclosed properties), USD raw dollars. "
            "MDRM RCFD2150 on 031/041; RCON2150 on 051. Schedule RC, item 7. Multiply by 1000."
        ),
    )

    # Loan composition (Schedule RC-C)
    total_real_estate_loans: Optional[float] = Field(
        default=None,
        description=(
            "Loans secured by real estate, total, USD raw dollars. "
            "MDRM RCFD1410 on 031/041; RCON1410 on 051. Schedule RC-C Part I, item 1. Multiply by 1000."
        ),
    )
    ci_loans: Optional[float] = Field(
        default=None,
        description=(
            "Commercial and industrial loans, total, USD raw dollars. "
            "MDRM RCFD1766 on 031/041; RCON1766 on 051. Schedule RC-C Part I, item 4. Multiply by 1000."
        ),
    )

    # Income statement (Schedule RI) — values are YEAR-TO-DATE as of the report date
    total_interest_income: Optional[float] = Field(
        default=None,
        description=(
            "Total interest income (YEAR-TO-DATE through the report date), USD raw dollars. "
            "MDRM RIAD4107 on all forms. Schedule RI, item 1.h. Multiply by 1000. "
            "Note: a Q3 (09-30) report shows Jan-Sep cumulative, not Q3-only."
        ),
    )
    total_interest_expense: Optional[float] = Field(
        default=None,
        description=(
            "Total interest expense (YTD), USD raw dollars. "
            "MDRM RIAD4073 on all forms. Schedule RI, item 2.f. Multiply by 1000."
        ),
    )
    net_interest_income: Optional[float] = Field(
        default=None,
        description=(
            "Net interest income (YTD), USD raw dollars. = interest income - interest expense. "
            "MDRM RIAD4074 on all forms. Schedule RI, item 3. Multiply by 1000."
        ),
    )
    provision_credit_losses: Optional[float] = Field(
        default=None,
        description=(
            "Provision for credit losses (YTD), USD raw dollars. "
            "MDRM RIAD4230 on all forms. Schedule RI, item 4. Multiply by 1000."
        ),
    )
    total_noninterest_income: Optional[float] = Field(
        default=None,
        description=(
            "Total noninterest income (YTD), USD raw dollars. "
            "MDRM RIAD4079 on all forms. Schedule RI, item 5.m. Multiply by 1000."
        ),
    )
    total_noninterest_expense: Optional[float] = Field(
        default=None,
        description=(
            "Total noninterest expense (YTD), USD raw dollars. "
            "MDRM RIAD4093 on all forms. Schedule RI, item 7.e. Multiply by 1000."
        ),
    )
    net_income: Optional[float] = Field(
        default=None,
        description=(
            "Net income attributable to the bank (YTD), USD raw dollars. "
            "MDRM RIAD4340 on all forms. Schedule RI, item 14. Multiply by 1000."
        ),
    )

    # Asset quality (Schedules RC-N, RI-B)
    net_chargeoffs_loans_leases: Optional[float] = Field(
        default=None,
        description=(
            "Net loan and lease charge-offs (YTD), USD raw dollars. "
            "= charge-offs (RIAD4635) - recoveries (RIAD4605). Schedule RI-B Part I, item 9. "
            "Multiply the YTD net figure by 1000."
        ),
    )
    nonaccrual_loans_total: Optional[float] = Field(
        default=None,
        description=(
            "Total nonaccrual loans and leases, USD raw dollars. "
            "MDRM RCFD1403 on 031/041; RCON1403 on 051. Schedule RC-N, column C, total. "
            "Multiply by 1000."
        ),
    )
    past_due_30_89_days_loans: Optional[float] = Field(
        default=None,
        description=(
            "Loans 30-89 days past due and still accruing, USD raw dollars. "
            "MDRM RCFD1406 on 031/041; RCON1406 on 051. Schedule RC-N, column A, total. "
            "Multiply by 1000."
        ),
    )
    past_due_90_plus_days_loans: Optional[float] = Field(
        default=None,
        description=(
            "Loans 90+ days past due and still accruing, USD raw dollars. "
            "MDRM RCFD1407 on 031/041; RCON1407 on 051. Schedule RC-N, column B, total. "
            "Multiply by 1000."
        ),
    )

    # Capital ratios (Schedule RC-R) — reported as percentages; convert to decimal fraction
    tier1_capital_ratio: Optional[float] = Field(
        default=None,
        description=(
            "Tier 1 risk-based capital ratio as a decimal fraction (e.g., 0.1245 for 12.45%). "
            "MDRM RCFA7206 on 031/041; RCOA7206 on 051. Schedule RC-R Part I. "
            "Divide the printed percentage by 100. CBLR-electing banks may report null."
        ),
    )
    cet1_capital_ratio: Optional[float] = Field(
        default=None,
        description=(
            "Common equity tier 1 capital ratio as a decimal fraction. "
            "MDRM RCFA7273 on 031/041; RCOA7273 on 051. Schedule RC-R Part I. "
            "Divide percentage by 100. CBLR-electing banks may report null."
        ),
    )
    leverage_ratio: Optional[float] = Field(
        default=None,
        description=(
            "Tier 1 leverage ratio (or Community Bank Leverage Ratio for CBLR electors) "
            "as a decimal fraction. MDRM RCFA7204 on 031/041; RCOA7204 on 051. "
            "Schedule RC-R Part I. Divide percentage by 100."
        ),
    )
    total_risk_based_capital_ratio: Optional[float] = Field(
        default=None,
        description=(
            "Total risk-based capital ratio (Tier 1 + Tier 2 / risk-weighted assets) "
            "as a decimal fraction. MDRM RCFA7205 on 031/041; RCOA7205 on 051. "
            "Schedule RC-R Part I. Divide percentage by 100. CBLR-electing banks may report null."
        ),
    )


# Maps each schema field to its FDIC BankFind API field code.
FDIC_FIELD_MAP: dict[str, str] = {
    "total_assets": "ASSET",
    "total_deposits": "DEP",
    "total_loans_net": "LNLSNET",
    "allowance_credit_losses_loans": "LNATRES",
    "total_liabilities": "LIAB",
    "total_equity_capital": "EQ",
    "total_securities": "SC",
    "trading_assets": "TRADE",
    "intangible_assets": "INTAN",
    "oreo": "ORE",
    "total_real_estate_loans": "LNRE",
    "ci_loans": "LNCI",
    "total_interest_income": "INTINC",
    "total_interest_expense": "EINTEXP",
    "net_interest_income": "NIM",
    "provision_credit_losses": "ELNATR",
    "total_noninterest_income": "NONII",
    "total_noninterest_expense": "NONIX",
    "net_income": "NETINC",
    "net_chargeoffs_loans_leases": "NTLNLS",
    "nonaccrual_loans_total": "NCLNLS",
    "past_due_30_89_days_loans": "P3ASSET",
    "past_due_90_plus_days_loans": "P9ASSET",
    "tier1_capital_ratio": "RBC1RWAJ",
    "cet1_capital_ratio": "RBCT1CER",
    "leverage_ratio": "RBC1AAJ",
    "total_risk_based_capital_ratio": "RBCRWAJ",
}


RATIO_FIELDS: frozenset[str] = frozenset({
    "tier1_capital_ratio",
    "cet1_capital_ratio",
    "leverage_ratio",
    "total_risk_based_capital_ratio",
})


DOLLAR_FIELDS: frozenset[str] = frozenset(set(FDIC_FIELD_MAP) - RATIO_FIELDS - {"form_version"})


def fdic_to_schema_value(field: str, fdic_raw: float | int | None) -> float | None:
    """Normalize an FDIC API value (thousands $ or percent) to schema convention."""
    if fdic_raw is None:
        return None
    if field in RATIO_FIELDS:
        return float(fdic_raw) / 100.0
    if field in DOLLAR_FIELDS:
        return float(fdic_raw) * 1000.0
    return float(fdic_raw)


# ---------------------------------------------------------------------------
# Ground-truth builder (moved from scripts/build_ground_truth.py)
# ---------------------------------------------------------------------------
FDIC_FINANCIALS = "https://banks.data.fdic.gov/api/financials"


def _fetch_financials(cert: str, report_date_yyyymmdd: str) -> dict[str, Any] | None:
    """Return the FDIC financials record for one bank + period, or None if missing."""
    fields = ",".join(sorted(set(FDIC_FIELD_MAP.values())))
    params = {
        "filters": f"CERT:{cert} AND REPDTE:{report_date_yyyymmdd}",
        "fields": fields,
        "limit": "1",
    }
    url = f"{FDIC_FINANCIALS}?{urllib.parse.urlencode(params, safe=':')}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    rows = payload.get("data", [])
    if not rows:
        return None
    return rows[0]["data"]


def _build_gt(record: dict[str, Any]) -> dict[str, Any]:
    date_compact = record["report_date"].replace("-", "")
    raw = _fetch_financials(record["cert"], date_compact)
    if raw is None:
        return {
            "rssd": record["rssd"],
            "cert": record["cert"],
            "name": record["name"],
            "report_date": record["report_date"],
            "error": "no FDIC record found",
            "values": {},
        }
    values: dict[str, float | None] = {}
    for field, fdic_code in FDIC_FIELD_MAP.items():
        values[field] = fdic_to_schema_value(field, raw.get(fdic_code))
    populated = sum(1 for v in values.values() if v is not None)
    return {
        "rssd": record["rssd"],
        "cert": record["cert"],
        "name": record["name"],
        "report_date": record["report_date"],
        "asset_bucket": record["asset_bucket"],
        "expected_form_version": record["expected_form_version"],
        "values": values,
        "populated_count": populated,
        "total_fields": len(FDIC_FIELD_MAP),
    }


# ---------------------------------------------------------------------------
# PDF fetcher = CDR download-instructions generator (from fetch_call_reports.py)
# ---------------------------------------------------------------------------
CDR_LOOKUP_URL = "https://cdr.ffiec.gov/public/ManageFacsimiles.aspx"


def _generate_download_instructions(records: list[dict[str, Any]], pdf_dir: Path) -> tuple[int, int]:
    pdf_dir.mkdir(parents=True, exist_ok=True)
    instructions_out = pdf_dir / "DOWNLOAD_INSTRUCTIONS.md"

    lines = [
        "# Call Report PDF Download Instructions",
        "",
        f"The FFIEC CDR ({CDR_LOOKUP_URL}) requires ASP.NET form-state for PDF downloads,",
        "so this step is manual. For each bank below, do the following:",
        "",
        f"1. Open <{CDR_LOOKUP_URL}>.",
        "2. Select **Report Type**: `Call Report` (default).",
        "3. **Cycle Date**: select the report date listed below (typically 09/30/2024).",
        "4. **ID Type**: choose `FDIC Certificate Number`. Enter the **CERT** number below.",
        "5. Press **Search**. Click the bank name in the result table.",
        "6. Click the **Call Report PDF** link in the resulting page.",
        "7. Save the PDF into this directory with the exact filename listed.",
        "",
        "Verification: re-run this script after downloading; missing files will be re-listed.",
        "",
        "| # | Bank | State | CERT | RSSD | Filename | Status |",
        "|---|------|-------|------|------|----------|--------|",
    ]

    present = 0
    for i, bank in enumerate(records, 1):
        fname = f"{bank['rssd']}_{bank['report_date']}.pdf"
        pdf_path = pdf_dir / fname
        status = "OK" if pdf_path.exists() else "MISSING"
        if status == "OK":
            present += 1
        lines.append(
            f"| {i} | {bank['name']} | {bank.get('state','-')} | "
            f"{bank['cert']} | {bank['rssd']} | `{fname}` | {status} |"
        )

    lines.append("")
    lines.append(f"**{present}/{len(records)}** PDFs present locally.")
    text = "\n".join(lines) + "\n"
    instructions_out.write_text(text)
    return present, len(records)


# ---------------------------------------------------------------------------
# Prompt bodies (moved from scripts/prompt.py _SHARED_BODY_* constants)
# ---------------------------------------------------------------------------
_DOMAIN_INTRO = """\
# What is an FFIEC Call Report?

A Call Report (Consolidated Reports of Condition and Income) is a quarterly regulatory filing every US commercial bank submits to the FFIEC. The PDF contains balance sheet (Schedule RC), income statement (Schedule RI), loan composition (Schedule RC-C), asset quality (Schedule RC-N, RI-B), and capital ratios (Schedule RC-R), among others.

# Form versions

There are three form versions; **you must identify which one this Call Report uses** to select the right MDRM code:

- **FFIEC 031** — banks with foreign offices. Uses `RCFD*` codes for balance sheet items, `RCFA*` for capital ratios.
- **FFIEC 041** — domestic-only standard banks. Also uses `RCFD*` / `RCFA*`.
- **FFIEC 051** — abbreviated form for small banks (<$5B in assets, no foreign offices). Uses `RCON*` codes for balance sheet items, `RCOA*` for capital ratios.

Income statement codes (`RIAD*`) are the same across all three forms.

The form version is printed on the cover page (e.g., "Form FFIEC 041")."""


_UNIT_CONVENTIONS = """\
# Unit conventions

- **Dollar fields**: the form reports values in thousands of dollars, but the schema expects RAW DOLLARS. Multiply each dollar value by 1000. (e.g., the form prints "1,234,567" meaning $1,234,567 thousand — write `1234567000` in the JSON.)
- **Ratios** (`tier1_capital_ratio`, `cet1_capital_ratio`, `leverage_ratio`, `total_risk_based_capital_ratio`): the form reports these as percentages with two decimals (e.g., "12.45"). Divide by 100 to convert to a decimal fraction (e.g., `0.1245`).
- **Form version**: write the three-digit form code as a string: `"031"`, `"041"`, or `"051"`.
- **Year-to-date income items**: Schedule RI values are *year-to-date* as of the report date. A Q3 (September 30) report shows January–September cumulative — that is the correct value to report.
- **Missing fields**: if a field genuinely does not apply to the bank's form version (e.g., risk-based capital ratios for a bank that elected the Community Bank Leverage Ratio framework), write `null` for that field."""


CONFIG = DatasetConfig(
    slug="ffiec_call_reports",
    display_name="FFIEC Call Report",
    schema_cls=CallReportExtraction,
    doc_key_fn=lambda r: f"{r['rssd']}_{r['report_date']}",
    field_compare_overrides={"form_version": "ignore"},
    domain_intro=_DOMAIN_INTRO,
    unit_conventions=_UNIT_CONVENTIONS,
    gt_builder=_build_gt,
    pdf_fetcher=_generate_download_instructions,
)
