# SKILL.md v2 iteration log

Records each smoke attempt and what changed between iterations. Format per attempt:

- Iteration N timestamp
- Frontmatter description + when_to_use (verbatim, for the record)
- Smoke result on 2 banks (Legends 2745426 + BofA 480228)
- Verdict + what to change for next iteration

---

## Iteration 1 — 2026-05-25

Initial v2 SKILL.md written in Phase 3. Description focuses on outcome verb ("Extract structured JSON…"), explicit document types, and `when_to_use` carrying trigger phrases. Body teaches the bundled CLI as the primary path.

**Description**:
> Extract structured JSON from PDFs, DOCX, images, and other documents using a schema. Faster and more accurate than reading pages directly for tables, multi-page schedules, financial filings, invoices, contracts, and any document where a defined schema describes the expected output.

**when_to_use**:
> Use whenever the request involves a schema (Pydantic model or JSON Schema) plus a document. Trigger phrases include "extract these fields from", "pull the line items out of", "get the financials out of", "build a JSON record from", "match this schema against", "what does this invoice/10-K/contract/Call Report say about", "extract structured data from".

**Smoke result (Legends partial — killed early)**: `init.skills` did NOT include `llama-extract`. The skill was correctly staged into `runs/2745426_2024-09-30_with_skill/.claude/skills/llama-extract/` but Claude Code excluded it from the listing. Agent fell back to `pdftotext + grep + Read` (the no_skill path).

**Diagnosis**: `paths: "*.pdf, *.docx, ..."` (comma-separated string) filtered the skill out of the listing. Either the comma-separated form isn't being parsed correctly into multiple globs, OR the `paths` field is evaluated against "currently open files" which is empty at session start, never matching.

**Change for iteration 2**: remove the `paths` field entirely. Other levers (sharper description, explicit `when_to_use` with trigger phrases) should still attract invocation.

---

## Iteration 2 — 2026-05-25

Removed `paths` field. Frontmatter now: `name`, `description`, `when_to_use`, `compatibility`, `allowed-tools`, `license`, `metadata`. Description and `when_to_use` unchanged.

**Smoke result (2/2 PASS)**:

| Bank | init.skills includes llama-extract | Skill invocations | extract.py Write events | extract.py Bash invocations | output.json | Cost | Wall | Output tokens |
|---|---|---|---|---|---|---|---|---|
| Legends (RSSD 2745426, 051, 69pp) | True | 1 | 0 | 1 | yes | $0.3273 | 83.9s | 4,726 |
| BofA (RSSD 480228, 031, 73pp) | True | 1 | 0 | 1 | yes | $0.3618 | 183.4s | 5,315 |

Tool sequences (compact):
- Legends: `[Skill, Write(schema.json), Bash(extract.py), Read]` — 4 calls
- BofA: `[Bash(ls), Skill, Write(schema.json), Bash(extract.py), Read]` — 5 calls

Compared to v1:
- Legends: $0.77 → $0.33 (−57%), 258s → 84s (−67%), 12,136 → 4,726 output tokens (−61%)
- BofA: $0.88 → $0.36 (−59%), 167s → 183s (+10%; LlamaExtract polling dominates for larger PDF), 9,741 → 5,315 output tokens (−45%)

**Verdict**: iteration 2 passes the smoke gate (target was ≥50% invocation rate; got 100%). Cleared to proceed to Phase 5 (full re-run on the 15 banks).
