# Llama-extract skill v1 → v2 comparison

**Date**: 2026-05-27
**Plan**: [`plans/2026-05-25-llama-extract-skill-v2-cli-and-invocation.md`](../plans/2026-05-25-llama-extract-skill-v2-cli-and-invocation.md)
**Inputs**: [`results/summary.json`](summary.json), [`results/summary-baseline-v1.json`](summary-baseline-v1.json), [`results/invocation_stats.json`](invocation_stats.json)

## Headline

| Metric | v1 with_skill | v2 with_skill | no_skill (unchanged) |
|---|---|---|---|
| **Skill invocation rate** | **1/15 (7%)** | **15/15 (100%)** | n/a |
| CLI call rate | 1/15 | 15/15 | n/a |
| `*.py` Write events | 1/15 | **0/15** | 0/15 |
| Accuracy (correct / scored) | 89.83% (362/403) | 86.85% (350/403) | 90.07% (363/403) |
| Total cost (15 runs) | $11.83 | **$5.46** | $11.17 |
| Mean cost / run | $0.79 | **$0.36** | $0.74 |
| Mean wall time | 147.3s | 175.0s | 158.9s |
| Mean API time | 139.6s | 174.6s | 158.3s |
| Mean Opus output tokens | 8,369 | **5,353** | 8,879 |
| Mean turns | 16.3 | ~9-10 | 16.1 |

## What changed in v2

1. **Bundled CLI** ships at [`llama-extract/scripts/extract.py`](../llama-extract/scripts/extract.py) (180 lines). Handles upload + extract + poll + result-write so the agent doesn't transcribe Python on every invocation.
2. **Rewritten SKILL.md** at [`llama-extract/SKILL.md`](../llama-extract/SKILL.md) (139 lines vs v1's 400). Outcome-first description, explicit `when_to_use` trigger phrases, action-oriented body that teaches the bundled CLI as the primary path.
3. **Removed the conversational "Initial Setup" block** that asked the user to provide files / schema / preferences — this read as a Q&A loop in headless context and likely contributed to the agent skipping the skill in v1.

## What we expected vs what we got

| Plan target | Result |
|---|---|
| Invocation rate ≥ 12/15 | **15/15 ✓** |
| `*.py` Write events = 0 | **0/15 ✓** |
| Mean cost ≤ $0.50 | **$0.36 ✓** |
| Mean wall ≤ 130s | 175s ✗ — see below |
| Accuracy within ±2pp of v1's 89.83% | 86.85% (Δ −2.98pp) — just outside ±2pp |
| Skill triggers cleanly (init.skills lists llama-extract) | ✓ all 15 runs |
| Bundled CLI works standalone | ✓ verified in Phase 2 |

## Findings the cleaner A/B exposes

### 1. Invocation rate was the dominant signal in v1's "tied accuracy" result

v1's `with_skill` condition only invoked the skill in 1 of 15 runs. The other 14 fell back to native [`Read`](https://code.claude.com/docs/en/cli-reference) (which has built-in page-aware PDF reading). So v1 was effectively measuring **14×Read + 1×LlamaExtract** vs **15×Read** (no_skill) — near-identical conditions, so near-identical accuracy.

v2 invokes the skill in 15/15 runs. The new A/B is **15×LlamaExtract** vs **15×Read+Bash**. Now we're actually measuring the two paths.

### 2. Pure LlamaExtract is ~3pp less accurate than pure Read on this task

When forced to actually use the LlamaExtract path on every run, accuracy drops from no_skill's 90.07% to with_skill's 86.85% (Δ −3.22pp). v1's 89.83% was *because* v1 was mostly Read-fallback runs; the one real LlamaExtract run brought the average down a hair.

A note about ground truth: the [research doc on the v1 benchmark](../research/2026-05-24-claude-vs-llamaextract-skill-experiment.md) and the [v1 plan's "Findings" section](../plans/2026-05-24-llamaextract-skill-benchmark.md) documented that 70 of 79 v1 "wrong" calls were actually FDIC vs MDRM definition gaps that affect both conditions identically. Those gaps still apply here, so the *underlying* extraction-on-the-line-item accuracy is much higher than the raw numbers. The 3pp delta likely comes from a smaller residual set of fields where the two paths really do disagree.

### 3. Cost cut in half; wall time slightly worse

- Mean cost: $0.79 → $0.36 = **−54%**. The bundled CLI eliminates the ~5,800 tokens v1 spent transcribing extract.py on the one Skill-invoking run, and more importantly, the 14 Read-fallback runs are replaced with much-shorter Skill-invoking runs (~5,353 output tokens vs Read-fallback's 8,369).
- Mean wall: 147s → 175s = **+19%**. The LlamaExtract polling time (~110-160s/run) dominates v2's wall time. v1's Read path was actually faster on average per run because Read+Write doesn't block on a cloud job.

### 4. Skill description quality is the *only* lever for autonomous invocation

Phase 4 required iteration. **Iteration 1** included `paths: "*.pdf, *.docx, ..."` in the frontmatter; Claude Code excluded the skill entirely from `init.skills` (likely because the [`paths` field is evaluated against open files at session start](https://code.claude.com/docs/en/skills), which is empty). **Iteration 2** removed the `paths` field; the skill listed cleanly and the agent invoked it on 2/2 smoke runs and 15/15 full-run banks.

The trigger phrases in the new description + `when_to_use` were sufficient to attract the agent on every run. We did not need [hooks](https://code.claude.com/docs/en/hooks) or other escalation.

### 5. The bundled-script pattern is reusable

[`research-docs`](~/.claude/skills/research-docs/SKILL.md) was the precedent — ships a `scripts/` directory and references it via `${CLAUDE_SKILL_DIR}`. The same template applies to any skill where the work is a deterministic CLI flow: write `--help`-style args + flags, ship a single Python file, and have SKILL.md teach the one-line invocation.

## Per-bank correct-count comparison

| Bank (RSSD) | v1 correct | v2 correct | Δ |
|---|---|---|---|
| (read from `results/scored.csv` after rendering) | | | |

(see `results/report.html` for the full per-bank table with heatmaps.)

## Caveats

- **FDIC ground-truth definition gaps** documented in the v1 plan still apply. The biggest systematically-wrong fields (`total_loans_net`, `nonaccrual_loans_total`, `total_securities`) are FDIC field-code aggregations that don't cleanly map to the single MDRM line item the schema directs at. Both conditions hit the same gap, so the relative comparison is meaningful even if absolute accuracy is ceilinged.
- **One model, one schema, one report date.** No claim about generalization to other documents, models, or extraction tasks.
- **Wall time is sensitive to LlamaExtract polling.** A faster polling interval or `parse_tier=cost_effective` would reduce v2 wall time at the cost of either credits or extraction quality.

## What's next (out of scope here)

- Fix the FDIC ground-truth field map for the 3 systematically-wrong fields. Should lift both conditions to ~98%+ effective accuracy and let us see a cleaner v2-vs-no_skill comparison.
- Strip MDRM hints from the shared prompt and re-run. The hints currently lift both conditions to "near-equal" performance; without them, LlamaExtract's schema-driven approach may differentiate more.
- Test on a harder document type (NAIC Schedule P, scanned PDFs, handwriting-heavy forms) where LlamaExtract's layout strength matters more.
