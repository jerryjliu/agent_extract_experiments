---
name: llama-extract
description: Use this skill when the user asks to extract structured data (JSON matching a schema) from documents (PDF, DOCX, images, etc.) using LlamaExtract. Best for table-heavy or layout-sensitive extraction where general-purpose document reading underperforms.
compatibility: Requires Python 3.9+ and `pip install llama-cloud` (v2.7+). Set `LLAMA_CLOUD_API_KEY` in the environment.
license: MIT
metadata:
  author: LlamaIndex
  version: "0.1.0"
---

# LlamaExtract Skill

Pull structured JSON out of unstructured documents (PDF, DOCX, PPTX, images, etc.) using LlamaExtract — LlamaIndex's specialized extraction model. Schema-driven, citation-capable, and tuned for visually complex layouts (multi-page tables, scanned PDFs, dense forms) where general-purpose document reading drops accuracy.

## Initial Setup

When this skill is invoked, respond with:

```
I'm ready to use LlamaExtract. Before we begin, please confirm:

- `llama-cloud` is installed (`pip install 'llama-cloud>=2.7'`)
- `LLAMA_CLOUD_API_KEY` is set in your shell environment

If both are set, please provide:

1. One or more files to extract from (PDF, DOCX, PPTX, image, etc.)
2. The target schema — Pydantic model or JSON Schema, with field descriptions
3. Any preferences: tier (cost vs. quality), citations on/off, target pages, batching
```

Then wait for the user's input.

---

## Step 0 — Install + auth (if needed)

Install the SDK:

```bash
pip install 'llama-cloud>=2.7'
```

Note: `llama-cloud-services` is deprecated (maintained only through May 1, 2026). Use `llama-cloud` directly.

Create an API key in the LlamaCloud console under **API Key** in the left sidebar (https://cloud.llamaindex.ai/) — the key is shown only at creation time, is scoped per user and per project, and reads from `LLAMA_CLOUD_API_KEY` by default.

```bash
export LLAMA_CLOUD_API_KEY="llx-..."
```

---

## Step 1 — Define a schema

LlamaExtract is **schema-first**. Pydantic models are preferred; raw JSON Schema is accepted.

### Pydantic (recommended)

```python
from typing import Optional, List
from pydantic import BaseModel, Field

class LineItem(BaseModel):
    description: str = Field(description="Item description as printed on the document.")
    amount: float = Field(description="Item amount in USD, raw dollars (not thousands).")

class Invoice(BaseModel):
    invoice_number: str = Field(description="Invoice number printed in the header.")
    issue_date: str = Field(description="ISO 8601 date (YYYY-MM-DD).")
    total: float = Field(description="Total amount due in USD.")
    line_items: List[LineItem] = Field(description="One per row of the line-item table.")
    notes: Optional[str] = Field(default=None, description="Free-text notes/memo field if present.")
```

### Constraints to respect

- Root must be `type: object` (a Pydantic `BaseModel`, not a list at the top level).
- Maximum nesting depth: **7 levels**.
- Maximum properties: **5000**.
- Total `description` characters across the schema: **120,000**.
- Raw schema JSON: **150,000 characters**.

### Why descriptions matter

Field `description=` text is **passed to the extraction LLM at runtime** — it is not just documentation. Treat each `description` as a one-sentence instruction. Specific guidance beats generic restatement:

```python
# Weak — restates the name
revenue: float = Field(description="Revenue.")

# Strong — specifies source, units, edge cases
revenue: float = Field(
    description=(
        "Total revenue from continuing operations for the fiscal year, "
        "in USD (raw dollars, not thousands or millions). "
        "Look in the income statement, top section. "
        "Do not include non-operating items."
    )
)
```

### Optional fields

```python
# Pydantic
auditor: Optional[str] = Field(default=None, description="...")

# Raw JSON Schema
{"auditor": {"anyOf": [{"type": "string"}, {"type": "null"}]}}
```

### Auto-generated schemas

If the user doesn't have a schema, propose one from a natural-language prompt + sample file:

```python
schema = client.extract.generate_schema(
    prompt="Extract invoice header, billing party, and all line items.",
    file_id=file_obj.id,
)
```

For reproducibility, hand-write the final schema rather than relying on the generator each run.

---

## Step 2 — Upload and extract

The canonical pattern: upload file → kick off extract job → poll until terminal status → read result.

```python
import time
from llama_cloud import LlamaCloud

client = LlamaCloud()  # reads LLAMA_CLOUD_API_KEY

file_obj = client.files.create(file="document.pdf", purpose="extract")

job = client.extract.create(
    file_input=file_obj.id,
    configuration={
        "data_schema": Invoice.model_json_schema(),
        "extraction_target": "per_doc",
        "tier": "agentic",
        "parse_tier": "agentic",
        "cite_sources": False,
    },
)

while job.status not in ("COMPLETED", "FAILED", "CANCELLED"):
    time.sleep(2)
    job = client.extract.get(job.id)

if job.status != "COMPLETED":
    raise RuntimeError(f"Extract failed: {job.error_message}")

data = job.extract_result  # dict matching the schema
result = Invoice.model_validate(data)
```

Job statuses: `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`. Errors land in `job.error_message`.

There is **no long-lived "extraction agent"** in v2 — each extract is a fresh per-call job.

---

## Step 3 — Tier selection

LlamaExtract has two tier knobs: `tier` (the extraction loop) and `parse_tier` (the underlying parse pass). Total cost per page = `tier credits + parse_tier credits`, billed at $1.25 per 1000 credits.

### `tier` (extraction)

| Value | Behavior | Credits/page |
|---|---|---|
| `cost_effective` | Single-pass, cheaper | 5 |
| `agentic` | Agentic loop over the doc — **recommended default** | 15 |
| `agentic_plus` | (Coming soon) For very complex / high-stakes extraction | TBA |

### `parse_tier` (underlying parse)

| Value | Best for | Credits/page |
|---|---|---|
| `fast` | Plain text PDFs, simple layout | 1 |
| `cost_effective` | Default for most documents | 3 |
| `agentic` | Dense tables, scanned PDFs, visual elements | 10 |
| `agentic_plus` | Charts, handwriting, very dirty scans | 45 |

### Heuristic (start here, only deviate on signal)

- **Default**: `tier="agentic"` + `parse_tier="agentic"` (25 credits/page = $0.031/page). Right for almost every real extraction job.
- **Drop to `tier="cost_effective"`** only when the user explicitly asks for cheap/draft mode OR the document is short and visually trivial (markdown, plain text).
- **Drop `parse_tier` to `cost_effective`** for clean digital-born PDFs with simple layout.
- **Upgrade `parse_tier` to `agentic_plus`** for scanned PDFs with handwriting, charts, or low-quality scans.

The docs recommend starting at `agentic` to isolate schema issues from parse issues, then dropping tiers only if quality holds.

---

## Step 4 — Citations and confidence scores (optional)

For audit trails or qualitative review, attach citations to extracted fields.

```python
job = client.extract.create(
    file_input=file_obj.id,
    configuration={
        "data_schema": Invoice.model_json_schema(),
        "tier": "agentic",
        "parse_tier": "agentic",
        "cite_sources": True,
        "confidence_scores": True,
    },
)
# … poll until COMPLETED …

# Citations and confidence live in extract_metadata, not extract_result.
job_with_meta = client.extract.get(job.id, expand=["extract_metadata"])
metadata = job_with_meta.extract_metadata
```

Citation shape (per leaf field):

```json
{
  "page": 3,
  "matching_text": "Invoice #INV-2024-001",
  "bounding_boxes": [{"x": 120, "y": 80, "w": 200, "h": 30}],
  "page_dimensions": {"width": 612, "height": 792}
}
```

Confidence shape (per field): `parsing_confidence`, `extraction_confidence`, `confidence` — uncalibrated, useful **relatively** within a doc, not as a probability.

### Caveats

- **Citations and confidence significantly slow extraction** — enable only when essential.
- **Confidence is limited to documents ≤ 100 pages**. For longer docs, split or skip confidence.
- For arrays, citations attach to leaf sub-fields, **not** to the array as a whole.

---

## Step 5 — Batch and async

For more than a handful of files, use `AsyncLlamaCloud` with bounded concurrency.

```python
import asyncio
from llama_cloud import AsyncLlamaCloud

async def extract_one(client, sem, path: str):
    async with sem:
        file_obj = await client.files.create(file=path, purpose="extract")
        job = await client.extract.create(
            file_input=file_obj.id,
            configuration={
                "data_schema": Invoice.model_json_schema(),
                "tier": "agentic",
                "parse_tier": "agentic",
            },
        )
        while job.status not in ("COMPLETED", "FAILED", "CANCELLED"):
            await asyncio.sleep(2)
            job = await client.extract.get(job.id)
        return path, job.extract_result if job.status == "COMPLETED" else None

async def main(paths):
    client = AsyncLlamaCloud()
    sem = asyncio.Semaphore(5)  # respect plan-tier concurrency cap
    return await asyncio.gather(*[extract_one(client, sem, p) for p in paths])
```

Cap the semaphore at your plan's **concurrent extract** limit (see "Limits" below) — going over leads to 429s.

---

## Step 6 — Performance tips

### Parse once, extract many

If you'll extract from the same file multiple times (different schemas, iterative schema design), reuse the parse:

```python
# First call — parse + extract
job1 = client.extract.create(file_input=file_obj.id, configuration={...})

# Inspect the parse job ID from job1's metadata, then reuse:
job2 = client.extract.create(file_input="pjb-...", configuration={...different schema...})
```

This saves `parse_tier` credits on subsequent extractions of the same file.

### 48h parse cache

Re-parsing the same file within 48 hours is **free** — only the extract-tier cost applies.

### Target specific pages

For long documents where the data only lives in a few pages:

```python
configuration={
    "target_pages": "1,3,5-7,9",  # 1-indexed, comma + dash syntax
    "max_pages": 50,
    # ...
}
```

### Extraction target

| `extraction_target` | When to use |
|---|---|
| `per_doc` (default) | One result object per document — most common |
| `per_page` | One result object per page — for page-indexed docs |
| `per_table_row` | One result object per table row — spreadsheets, ordered entity lists |

### System prompt

```python
configuration={
    "system_prompt": "Treat 'N/A' and '—' as null. Currency is GBP unless otherwise marked.",
    # ...
}
```

Use a `system_prompt` for cross-cutting rules (units, null handling, locale) that don't fit naturally into individual field descriptions.

---

## Errors and limits

### File limits

| Limit | Value |
|---|---|
| Max file size | 100 MB |
| Max pages per extraction | 500 (enforced for files >5MB) |

### Rate limits

| Tier | File uploads | Free-tier req/min | Concurrent extract jobs |
|---|---|---|---|
| Free | 50 per 5s per project | 20 | 5 |
| Starter | 50 per 5s per project | — | 5 |
| Pro | 50 per 5s per project | — | 20 |
| Enterprise | 50 per 5s per project | — | 100 |

On 429, sleep with exponential backoff (2s, 4s, 8s, …) and retry.

### Free-tier credits

10,000 credits/month included, no card required. At `agentic` + `agentic` = 25 credits/page, that's ~400 pages/month free.

### Job statuses

| Status | Meaning |
|---|---|
| `PENDING` | Queued, not yet running |
| `RUNNING` | In progress |
| `COMPLETED` | Result is in `job.extract_result` |
| `FAILED` | Check `job.error_message` |
| `CANCELLED` | Caller cancelled |

---

## Configuration reference

All keys go under `configuration={...}` in `client.extract.create(...)`.

| Key | Type | Default | Notes |
|---|---|---|---|
| `data_schema` | dict (JSON Schema) | required | `MyModel.model_json_schema()` |
| `extraction_target` | str | `"per_doc"` | `per_doc` / `per_page` / `per_table_row` |
| `tier` | str | `"agentic"` | `cost_effective` / `agentic` |
| `parse_tier` | str | inherits | `fast` / `cost_effective` / `agentic` / `agentic_plus` |
| `system_prompt` | str | unset | Cross-cutting instructions |
| `target_pages` | str | unset | e.g. `"1,3,5-7,9"` |
| `max_pages` | int | unset | Hard cap on pages parsed |
| `cite_sources` | bool | `false` | Slower; populates `extract_metadata.citations` |
| `confidence_scores` | bool | `false` | Slower; doc must be ≤ 100 pages |

To read citations/confidence after a job completes:

```python
job = client.extract.get(job_id, expand=["extract_metadata"])
```

---

## Supported document types

PDF, DOCX, PPTX, XLSX, common image formats (PNG, JPG, TIFF, WebP), plain text, HTML, Markdown. The parse pass handles the format conversion; the schema-driven extraction loop runs over the parsed representation.

---

## When NOT to use LlamaExtract

- The document is plain text or trivially structured (CSV, JSON, simple Markdown) — use direct parsing.
- The user needs the raw text content rather than schema-fitted data — use a parser (e.g. LiteParse, LlamaParse) instead.
- The schema is unknown and the user wants exploratory reading — read the doc first, then propose a schema.
