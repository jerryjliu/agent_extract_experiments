"""SEC insurance-segment 10-Q dataset config.

PDFs: rendered from the primary iXBRL HTML via headless Chromium (Playwright).
Ground truth: data.sec.gov XBRL CompanyFacts for the filing's accession.

If Playwright rendering is unavailable or unusable, set FORCE_MANUAL=True (or the
fetcher falls back automatically on ImportError) to emit a DOWNLOAD_INSTRUCTIONS.md
pointing at the SEC Electronic Print service.
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from scripts.datasets.base import DatasetConfig


UA = "extract-bench/0.1 (research benchmark) github.com/jerryjliu/agent_extract_experiments"
HDR = {"User-Agent": UA}


class SEC10QInsuranceExtraction(BaseModel):
    """~20 fields covering insurance-segment 10-Q financial highlights."""

    # Issuer identification
    issuer_name: Optional[str] = Field(default=None, description="Issuer (registrant) name from the cover page.")
    period_end_date: Optional[str] = Field(default=None, description="Period-of-report end date in ISO format YYYY-MM-DD (cover page, 'For the quarterly period ended').")

    # Income statement (Statements of Operations)
    total_revenues: Optional[float] = Field(default=None, description="Total revenues for the most recent quarter (three months ended), USD raw. From the consolidated statements of operations.")
    net_investment_income: Optional[float] = Field(default=None, description="Net investment income for the quarter, USD raw.")
    net_income: Optional[float] = Field(default=None, description="Net income (loss) attributable to the issuer for the quarter, USD raw. May be negative.")
    net_income_per_share_basic: Optional[float] = Field(default=None, description="Basic earnings per share for the quarter, USD per share.")
    net_income_per_share_diluted: Optional[float] = Field(default=None, description="Diluted earnings per share for the quarter, USD per share.")

    # Balance sheet (period end)
    total_assets: Optional[float] = Field(default=None, description="Total assets at period end, USD raw. From the consolidated balance sheet.")
    total_investments: Optional[float] = Field(default=None, description="Total investments at period end, USD raw.")
    total_liabilities: Optional[float] = Field(default=None, description="Total liabilities at period end, USD raw.")
    stockholders_equity: Optional[float] = Field(default=None, description="Total stockholders'/shareholders' equity attributable to the issuer at period end, USD raw.")
    insurance_reserves: Optional[float] = Field(default=None, description="Liability for unpaid claims and claims adjustment expense (net) / total insurance reserves at period end, USD raw.")
    deferred_policy_acquisition_costs: Optional[float] = Field(default=None, description="Deferred policy acquisition costs (DAC) asset at period end, USD raw.")

    # Cash flow (year-to-date)
    cash_from_operations: Optional[float] = Field(default=None, description="Net cash provided by (used in) operating activities, YTD, USD raw. May be negative.")
    cash_from_investing: Optional[float] = Field(default=None, description="Net cash provided by (used in) investing activities, YTD, USD raw. May be negative.")
    cash_from_financing: Optional[float] = Field(default=None, description="Net cash provided by (used in) financing activities, YTD, USD raw. May be negative.")

    # Filing meta
    common_shares_outstanding: Optional[float] = Field(default=None, description="Common shares outstanding as of the latest practicable date (cover page), count of shares (raw count, not thousands).")


# Map our schema field → preferred us-gaap concept(s). Filers vary in tag choice.
CONCEPT_MAP: dict[str, list[str]] = {
    "total_revenues":             ["Revenues", "RevenuesNetOfInterestExpense"],
    "net_investment_income":      ["NetInvestmentIncome"],
    "net_income":                 ["NetIncomeLoss", "ProfitLoss"],
    "net_income_per_share_basic": ["EarningsPerShareBasic", "IncomeLossFromContinuingOperationsPerBasicShare"],
    "net_income_per_share_diluted": ["EarningsPerShareDiluted", "IncomeLossFromContinuingOperationsPerDilutedShare"],
    "total_assets":               ["Assets"],
    "total_investments":          ["Investments", "MarketableSecurities"],
    "total_liabilities":          ["Liabilities"],
    "stockholders_equity":        ["StockholdersEquity"],
    "insurance_reserves":         ["LiabilityForClaimsAndClaimsAdjustmentExpense",
                                   "LiabilityForUnpaidClaimsAndClaimsAdjustmentExpense"],
    "deferred_policy_acquisition_costs": ["DeferredPolicyAcquisitionCosts", "DeferredPolicyAcquisitionCost"],
    "cash_from_operations":       ["NetCashProvidedByUsedInOperatingActivities"],
    "cash_from_investing":        ["NetCashProvidedByUsedInInvestingActivities"],
    "cash_from_financing":        ["NetCashProvidedByUsedInFinancingActivities"],
    "common_shares_outstanding":  ["CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding"],
}


def _company_facts(cik: str) -> dict:
    cik_padded = str(cik).zfill(10)
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_padded}.json"
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def _span_days(v: dict) -> int:
    s, e = v.get("start"), v.get("end")
    if s and e:
        try:
            from datetime import date
            sy, sm, sd = (int(x) for x in s.split("-"))
            ey, em, ed = (int(x) for x in e.split("-"))
            return (date(ey, em, ed) - date(sy, sm, sd)).days
        except Exception:  # noqa: BLE001
            return 0
    return 0  # instant (balance-sheet) items


def _pick_value(facts: dict, concepts: list[str], accession_clean: str,
                period_end: Optional[str]) -> Optional[float]:
    """Find the value tagged to the given accession for one of the candidate concepts.

    For instant (balance-sheet) items, prefer the entry whose period ``end`` equals
    the filing period (current quarter end, not the prior-year comparative). For flow
    (income/cash-flow) items, prefer the entry whose ``end`` matches the period and
    whose window is closest to a quarter (~92 days).
    """
    gaap = facts.get("facts", {}).get("us-gaap", {})
    dei = facts.get("facts", {}).get("dei", {})
    for c in concepts:
        entry = gaap.get(c) or dei.get(c)
        if not entry:
            continue
        candidates = [v for unit_vals in (entry.get("units") or {}).values()
                      for v in unit_vals
                      if str(v.get("accn", "")).replace("-", "") == accession_clean]
        if not candidates:
            continue
        flow = [v for v in candidates if _span_days(v) > 0]
        instant = [v for v in candidates if _span_days(v) == 0]
        if instant and not flow:
            # Balance-sheet item: prefer end == period_end, else the latest end.
            at_period = [v for v in instant if v.get("end") == period_end]
            pool = at_period or sorted(instant, key=lambda v: v.get("end") or "", reverse=True)
            return pool[0].get("val")
        if flow:
            at_period = [v for v in flow if v.get("end") == period_end] or flow
            at_period.sort(key=lambda v: abs(_span_days(v) - 92))
            return at_period[0].get("val")
        return candidates[0].get("val")
    return None


def _build_gt(record: dict[str, Any]) -> dict[str, Any]:
    try:
        facts = _company_facts(record["cik"])
    except Exception as e:  # noqa: BLE001
        return {"cik": record["cik"], "accession": record["accession"],
                "name": record.get("name"), "error": f"companyfacts fetch failed: {e}", "values": {}}
    accn_clean = record["accession"].replace("-", "")
    values: dict[str, Any] = {
        "issuer_name": facts.get("entityName"),
        "period_end_date": record.get("period"),
    }
    for field, concepts in CONCEPT_MAP.items():
        values[field] = _pick_value(facts, concepts, accn_clean, record.get("period"))
    return {
        "cik": record["cik"],
        "accession": record["accession"],
        "ticker": record.get("ticker"),
        "name": record.get("name"),
        "form": record.get("form"),
        "period": record.get("period"),
        "values": values,
        "populated_count": sum(1 for v in values.values() if v is not None),
        "total_fields": len(values),
    }


def _filing_index(cik: str, accession: str) -> dict:
    accn_clean = accession.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn_clean}/index.json"
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def _primary_doc_name(cik: str, accession: str) -> Optional[str]:
    """Heuristic fallback: pick the largest .htm in the filing index."""
    idx = _filing_index(cik, accession)
    items = (idx.get("directory") or {}).get("item") or []
    htms = [i for i in items if (i.get("name") or "").lower().endswith((".htm", ".html"))]
    htms = [i for i in htms if not (i.get("name") or "").lower().startswith(("r", "0"))] or htms
    if not htms:
        return None
    htms.sort(key=lambda i: int(i.get("size") or 0), reverse=True)
    return htms[0]["name"]


def _doc_url(cik: str, accession: str, primary_doc: str) -> str:
    accn_clean = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn_clean}/{primary_doc}"


def _render_pdf_chromium(html_path: Path, out_path: Path) -> None:
    """Render a LOCAL iXBRL HTML file to PDF.

    We render from a local file (not the live SEC URL) because loading the live
    iXBRL document in Chromium fires many parallel sub-resource requests that trip
    SEC's rate limiter and return a bot-block page. The HTML is fetched once via
    urllib with a declared User-Agent (which SEC allows), then rendered offline.
    """
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(f"file://{html_path.resolve()}", wait_until="load", timeout=120_000)
        page.wait_for_timeout(1500)
        page.pdf(
            path=str(out_path),
            format="Letter",
            print_background=True,
            margin={"top": "0.5in", "right": "0.4in", "bottom": "0.5in", "left": "0.4in"},
        )
        browser.close()


def _write_manual_instructions(records: list[dict[str, Any]], pdf_dir: Path) -> tuple[int, int]:
    out = pdf_dir / "DOWNLOAD_INSTRUCTIONS.md"
    lines = [
        "# SEC 10-Q PDF Download Instructions (manual fallback)",
        "",
        "Playwright/Chromium rendering was unavailable. Generate each PDF via the SEC",
        "Electronic Print service <https://www.sec.gov/cgi-bin/browse-edgar> or print the",
        "primary filing document to PDF, saving with the exact filename below.",
        "",
        "| # | Issuer | CIK | Accession | Filename | Status |",
        "|---|--------|-----|-----------|----------|--------|",
    ]
    present = 0
    for i, r in enumerate(records, 1):
        fname = f"{r['cik']}_{r['accession']}.pdf"
        status = "OK" if (pdf_dir / fname).exists() else "MISSING"
        if status == "OK":
            present += 1
        lines.append(f"| {i} | {r.get('name')} | {r['cik']} | {r['accession']} | `{fname}` | {status} |")
    lines.append("")
    lines.append(f"**{present}/{len(records)}** PDFs present locally.")
    out.write_text("\n".join(lines) + "\n")
    return present, len(records)


def _fetch_pdfs(records: list[dict[str, Any]], pdf_dir: Path) -> tuple[int, int]:
    pdf_dir.mkdir(parents=True, exist_ok=True)
    try:
        import playwright.sync_api  # noqa: F401
    except Exception:  # noqa: BLE001
        print("  playwright not available — writing manual download instructions instead")
        return _write_manual_instructions(records, pdf_dir)

    import tempfile
    import time
    n_present = 0
    for r in records:
        out = pdf_dir / f"{r['cik']}_{r['accession']}.pdf"
        if out.exists() and out.stat().st_size > 0:
            n_present += 1
            continue
        try:
            primary = r.get("primary_doc") or _primary_doc_name(r["cik"], r["accession"])
            if not primary:
                print(f"  {r['cik']}/{r['accession']}: no primary .htm found")
                continue
            url = _doc_url(r["cik"], r["accession"], primary)
            # Fetch HTML once via urllib (declared UA — SEC allows), render locally.
            req = urllib.request.Request(url, headers=HDR)
            with urllib.request.urlopen(req, timeout=120) as resp:
                html_bytes = resp.read()
            with tempfile.NamedTemporaryFile("wb", suffix=".htm", delete=False) as tf:
                tf.write(html_bytes)
                tmp_html = Path(tf.name)
            try:
                _render_pdf_chromium(tmp_html, out)
            finally:
                tmp_html.unlink(missing_ok=True)
            if out.exists() and out.stat().st_size > 0:
                n_present += 1
            time.sleep(0.5)  # be gentle with SEC
        except Exception as e:  # noqa: BLE001
            print(f"  {r['cik']}/{r['accession']}: fetch/render failed: {e}")
            if out.exists():
                out.unlink()
    return n_present, len(records)


_DOMAIN_INTRO = """\
# What is a SEC Form 10-Q (insurance-segment issuer)?

A 10-Q is the quarterly report US public companies file with the SEC under the Securities Exchange Act. An insurance-segment issuer's 10-Q contains:

- **Cover page**: registrant (issuer) name, "For the quarterly period ended <date>", and the number of common shares outstanding as of the latest practicable date.
- **Part I, Item 1 — Financial Statements (unaudited)**: consolidated balance sheets (current period end vs prior year-end), consolidated statements of operations (three months and nine months ended), consolidated statements of cash flows (nine months / YTD), and insurance-specific lines (reserves/liability for unpaid claims, deferred policy acquisition costs, net investment income, premiums).
- **Part I, Item 2 — MD&A**: narrative (not extracted here).

The numeric values to extract live in the financial-statement tables. Where multiple periods are shown side by side (e.g., "Three Months Ended September 30" vs "Nine Months Ended"), each field description states which to use — typically period-end balances for balance-sheet items, the most-recent-quarter (three months) figure for income-statement items, and the year-to-date figure for cash-flow items."""


_UNIT_CONVENTIONS = """\
# Unit conventions

- **Dollar amounts**: financial statements are commonly reported in **millions** or **thousands** — check the column header ("In millions" / "$ in thousands"). Convert to **raw USD**: multiply by 1,000,000 for millions or by 1,000 for thousands. Write the raw number.
- **Per-share amounts**: write USD per share exactly as printed (e.g., 1.23). No unit conversion.
- **common_shares_outstanding**: a raw count of shares. If the cover page states the count in thousands, convert to the raw count.
- **Sign convention**: losses and cash outflows are negative.
- **Period selection**: balance-sheet items use the most recent period-end column; income-statement items use the three-months-ended (quarter) figure; cash-flow items use the year-to-date figure.
- **Missing fields**: write `null` if the issuer's statements do not separately report the value."""


_STRING_FIELDS = ["issuer_name", "period_end_date"]


CONFIG = DatasetConfig(
    slug="sec_10q_insurance",
    display_name="SEC 10-Q (insurance segment)",
    schema_cls=SEC10QInsuranceExtraction,
    doc_key_fn=lambda r: f"{r['cik']}_{r['accession']}",
    # issuer_name GT is SEC's normalized entityName (won't match the cover page
    # verbatim); period_end_date is the string identity check.
    field_compare_overrides={"issuer_name": "ignore", "period_end_date": "string"},
    domain_intro=_DOMAIN_INTRO,
    unit_conventions=_UNIT_CONVENTIONS,
    gt_builder=_build_gt,
    pdf_fetcher=_fetch_pdfs,
)
