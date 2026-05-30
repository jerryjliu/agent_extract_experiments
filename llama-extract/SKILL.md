---
name: llama-extract
description: Extract structured JSON from PDFs, DOCX, images, and other documents using a schema. Faster and more accurate than reading pages directly for tables, multi-page schedules, financial filings, invoices, contracts, and any document where a defined schema describes the expected output.
when_to_use: Use whenever the request involves a schema (Pydantic model or JSON Schema) plus a document. Trigger phrases include "extract these fields from", "pull the line items out of", "get the financials out of", "build a JSON record from", "match this schema against", "what does this invoice/10-K/contract/Call Report say about", "extract structured data from".
compatibility: Requires Python 3.9+ and `pip install 'llama-cloud>=2.7'`. Set `LLAMA_CLOUD_API_KEY` in the environment.
allowed-tools: Bash(python *), Write, Read
license: MIT
metadata:
  author: LlamaIndex
  version: "0.2.0"
---

# LlamaExtract Skill

Extract structured JSON from documents using a schema. This skill ships a bundled CLI (`scripts/extract.py`) that handles upload, extraction, polling, and result writing. Your job is to (a) define the schema as JSON Schema, (b) invoke the CLI, (c) read the result.

## When to use this skill

Use this skill whenever a request combines:
- **A document** (PDF, DOCX, PPTX, XLSX, image, or similar)
- **A target schema** (Pydantic model, JSON Schema, or a list of fields the user wants extracted)

Concrete triggers: extracting line items from an invoice, pulling fields from a 10-K / Call Report / contract / KYC doc / insurance claim, getting a structured record from a form. The bundled CLI is faster than reading PDF pages directly, especially for: multi-page tables, dense schedules, regulatory filings, scanned PDFs, and any document with a defined output schema.

## Step 1 — Write the schema to a JSON file

Encode the target schema as JSON Schema. If the user provides a Pydantic model, convert it:

```python
# Conversion snippet (Pydantic -> JSON Schema file)
import json
from my_models import MyModel
open("./schema.json", "w").write(json.dumps(MyModel.model_json_schema(), indent=2))
```

If the user provides field names + descriptions in prose, build the JSON Schema directly:

```json
{
  "type": "object",
  "title": "MySchema",
  "description": "<one-sentence description of the document type>",
  "properties": {
    "invoice_number": {
      "type": "string",
      "description": "Invoice number printed in the header."
    },
    "total_amount": {
      "anyOf": [{"type": "number"}, {"type": "null"}],
      "default": null,
      "description": "Total amount due in USD, raw dollars."
    }
  },
  "required": ["invoice_number"]
}
```

Schema constraints (enforced by LlamaExtract):
- Root must be `type: "object"`.
- Max nesting depth: 7. Max properties: 5000. Total description chars: 120,000. Raw schema chars: 150,000.
- Field `description` text is **passed to the extraction LLM at runtime** — treat each one as a one-sentence instruction. Specify units, location in the document, edge cases:

```python
# Weak — restates the name
revenue: float = Field(description="Revenue.")

# Strong — specifies source, units, edge cases
revenue: float = Field(description=(
    "Total revenue from continuing operations for the fiscal year, in USD raw "
    "dollars (not thousands). Look in the income statement, top section. Do not "
    "include non-operating items."
))
```

## Step 2 — Run the bundled CLI

```bash
python "${CLAUDE_SKILL_DIR}/scripts/extract.py" \
    --input ./input.pdf \
    --schema ./schema.json \
    --output ./output.json \
    --tier agentic \
    --parse-tier agentic \
    --verbose
```

The CLI:
- Validates `LLAMA_CLOUD_API_KEY` is set.
- Uploads the input file via `client.files.create(purpose="extract")`.
- Calls `client.extract.run(...)` with the configured tiers and polling.
- Writes the extracted JSON to `--output` and exits 0 on success.
- Exits non-zero on any error; the error message is on stderr.

Run `python "${CLAUDE_SKILL_DIR}/scripts/extract.py" --help` for the full flag reference.

## Step 3 — Read and validate the output

After the CLI exits successfully, the JSON result is at `./output.json`. Read it with the Read tool, validate against the schema if needed, and proceed with whatever the user asked for downstream.

## Tier selection (defaults are usually right)

| Flag | Default | When to override |
|---|---|---|
| `--tier` | `agentic` (15 credits/page) | Drop to `cost_effective` (5 credits/page) for short plain-text documents or draft mode. |
| `--parse-tier` | `agentic` (10 credits/page) | Use `cost_effective` (3) for clean digital-born PDFs with simple layout. Use `agentic_plus` (45) for scanned PDFs with handwriting / charts. |

Pricing: $1.25 per 1000 credits. Default (`agentic` + `agentic`) = 25 credits/page = $0.031/page.

## Optional CLI flags

| Flag | Purpose |
|---|---|
| `--system-prompt "<text>"` | Cross-cutting extraction rules (units, locale, null handling). |
| `--target-pages "1,3,5-7"` | Restrict extraction to specific pages. 1-indexed. |
| `--max-pages N` | Hard cap on pages parsed. |
| `--extraction-target {per_doc,per_page,per_table_row}` | Default `per_doc`. Use `per_table_row` for spreadsheets or ordered entity lists. |
| `--cite-sources` | Populates per-field source citations. Slower; works on docs of any size. |
| `--confidence-scores` | Populates per-field confidence. Slower; doc must be ≤ 100 pages. |
| `--polling-timeout 1800` | Max seconds to wait for the job. |

## Limits

| Limit | Value |
|---|---|
| Max file size | 100 MB |
| Max pages per extraction | 500 (enforced for files > 5MB) |
| Free-tier concurrent jobs | 5 |
| Free-tier monthly credits | 10,000 (≈ 400 pages at default tiers) |

## When NOT to use this skill

- The document is plain text or trivially structured (CSV, JSON, simple Markdown). Use direct file reading.
- The user wants raw text content, not a schema-fitted record. Use a parser (e.g. LiteParse).
- The user has no schema in mind and wants exploratory reading. Read the document first, then propose a schema, then come back to this skill.

## Reference: SDK direct call

If the bundled CLI fails or the user needs a configuration knob the CLI doesn't expose, the underlying SDK call pattern is documented at [developers.llamaindex.ai/llamaparse/extract/sdk/](https://developers.llamaindex.ai/llamaparse/extract/sdk/). The CLI's source is at `${CLAUDE_SKILL_DIR}/scripts/extract.py` — read it for the canonical call pattern.
