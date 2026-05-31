"""Shared extraction prompts. Datasets supply their domain intro + unit conventions.

build_extraction_prompt(dataset)            — single PDF at ./input.pdf → ./output.json
build_batch_prompt(dataset, doc_keys)       — N PDFs at ./inputs/<key>.pdf → ./outputs/<key>.json

Both builders compose the dataset's JSON Schema dump (dataset.schema_cls), the
dataset's domain_intro, and the dataset's unit_conventions, so per-file and batch
prompts stay in sync.
"""
from __future__ import annotations

import json

from scripts.datasets.base import DatasetConfig


def _tier_directive(extract_tier: str | None, parse_tier: str | None) -> str:
    """A with_skill-only tool-usage instruction naming the exact tiers to pass the
    llama-extract CLI. Returns "" when no tiers are given (the no_skill case), so
    the no_skill prompt is unaffected. This configures *how the tool is invoked*,
    not *what to extract* — the extraction task is identical across conditions."""
    if not extract_tier or not parse_tier:
        return ""
    return f"""
# Extraction tool configuration

When you invoke the bundled llama-extract CLI (`scripts/extract.py`), pass exactly \
`--tier {extract_tier} --parse-tier {parse_tier}`. Use these tiers for every \
document; do not substitute other tiers.
"""


SYSTEM_PROMPT_APPEND = (
    "You are running a one-shot structured-extraction task. "
    "Write only the final JSON output to the file ./output.json. "
    "Do not print the JSON to your assistant message — write it to disk only. "
    "After you have written ./output.json successfully, stop. "
    "Do not ask follow-up questions."
)


SYSTEM_PROMPT_APPEND_BATCH = (
    "You are running a one-shot batch structured-extraction task over multiple PDFs. "
    "Write each final JSON output to its own file under ./outputs/ — do not print "
    "JSON to your assistant messages. After all expected output files exist and are "
    "valid JSON, stop. Do not ask follow-up questions."
)


def build_extraction_prompt(dataset: DatasetConfig, extract_tier: str | None = None,
                            parse_tier: str | None = None) -> str:
    """Return the per-file user prompt. extract_tier/parse_tier add a with_skill-only
    tool-usage directive; leave them None (no_skill) for the unchanged prompt."""
    schema_json = json.dumps(dataset.schema_cls.model_json_schema(), indent=2)
    return f"""\
# Task

Extract structured data from a {dataset.display_name} PDF and write the result as a single JSON object to `./output.json` in the current working directory.

The PDF is at `./input.pdf` in your current working directory.
{_tier_directive(extract_tier, parse_tier)}
{dataset.domain_intro}

# Output schema

Write a single JSON object to `./output.json` matching this JSON Schema (the `description` fields tell you exactly where to look for each value and how to convert units):

```json
{schema_json}
```

{dataset.unit_conventions}

# Required behavior

1. Read `./input.pdf`.
2. Locate each schema field's value in the document using the field descriptions.
3. Convert units per the rules above.
4. Write the resulting JSON object to `./output.json`. Validate that the file is valid JSON and conforms to the schema before stopping.
5. Stop. Do not ask follow-up questions. Do not print the JSON to your assistant output.

Begin now.
"""


def build_batch_prompt(dataset: DatasetConfig, doc_keys: list[str],
                       extract_tier: str | None = None, parse_tier: str | None = None) -> str:
    """Return the batch-mode user prompt (N PDFs in one Claude session). extract_tier/
    parse_tier add a with_skill-only tool-usage directive; None (no_skill) = unchanged."""
    schema_json = json.dumps(dataset.schema_cls.model_json_schema(), indent=2)
    file_list = "\n".join(f"- ./inputs/{k}.pdf  →  ./outputs/{k}.json" for k in doc_keys)
    return f"""\
# Task

Extract structured data from {len(doc_keys)} {dataset.display_name} PDFs in the `./inputs/` directory. Write one JSON object per document to `./outputs/<doc_key>.json` in the current working directory. A pre-written copy of the schema as JSON Schema is already at `./schema.json` in the working directory.
{_tier_directive(extract_tier, parse_tier)}
# Input → output mapping

{file_list}

# Efficiency

Process these efficiently. If you can issue several extract operations in a single assistant turn — e.g., multiple `Bash` tool calls grouped in one response — prefer that over one PDF per turn. Otherwise, proceed sequentially. Respect any rate limits your tools document.

{dataset.domain_intro}

# Output schema (also at ./schema.json)

Write each JSON object matching this JSON Schema (the `description` fields tell you exactly where to look for each value and how to convert units):

```json
{schema_json}
```

{dataset.unit_conventions}

# Required behavior

1. For each PDF in `./inputs/`, locate each schema field's value in the document using the field descriptions.
2. Convert units per the rules above.
3. Write the resulting JSON to `./outputs/<doc_key>.json`. Validate each file is valid JSON and conforms to the schema before moving on (or in parallel).
4. When all {len(doc_keys)} output files exist and are valid, stop. Do not ask follow-up questions. Do not print JSON to your assistant output.

Begin now.
"""


if __name__ == "__main__":
    from scripts.datasets import get_dataset, DEFAULT_SLUG
    print(build_extraction_prompt(get_dataset(DEFAULT_SLUG)))
