# Cross-Dataset Comparison — Batch & Per-File

Generated 2026-05-31 by `scripts/render_cross_dataset_summary.py`.

Claude Code extracting a structured schema from a corpus of PDFs. **with_skill** loads the `llama-extract` skill (delegates extraction to a LlamaCloud parse+extract job); **no_skill** is the full agent without the `llama-extract` skill (Claude reads the PDFs directly). Model `claude-opus-4-7`. Results are shown in two execution modes: **batch** (one session per corpus) and **per-file** (one session per document). Numbers use the latest run per dataset (rerun where present).

## Batch mode

### Accuracy

| Dataset | N | Acc (with) | Acc (no) | Δ (with−no) | Why |
|---|---|---|---|---|---|
| FFIEC Call Report | 15 | 85.1% | 85.6% | -0.5pp | Skill **edges ahead** — Call Report values are raw dollars on a fixed form grid; delegated extraction reads the schedule cells cleanly. |
| ClinicalTrials.gov protocol | 12 | 59.3% | 73.8% | -14.5pp | Skill **trails by ~20pp** — brittle exact-string matches on free-text `brief_title`/`sponsor`, plus nulls on categorical design fields (allocation/masking) that direct reading recovers from deep in the document. |
| IRS Form 990 | 11 | 93.5% | 92.9% | +0.6pp | **Near tie** — Form 990 amounts are raw dollars (no unit trap). Most remaining errors are `investment_income`, which mismatches the ground-truth line definition under *both* conditions. |
| SEC 10-Q (insurance segment) | 12 | 87.5% | 96.6% | -9.1pp | Skill **trails by ~18pp** — a systematic dropped unit conversion: on "(in millions)" filings llama-extract returns the table value verbatim (28 of 37 misses are off by exactly ×1,000,000). Direct reading sees the header and multiplies. |

### Cost & latency

Total cost = Claude tokens + LlamaCloud credits (parse + extract, billed per page at $0.00125/credit). `no_skill` never calls LlamaCloud, so its credit cost is $0 and its total equals its token cost.

| Dataset | Token (with) | Credit (with) | Total (with) | Total (no) | Total vs no | Pages | Wall cold (with) | Wall warm (with) | Wall (no) |
|---|---|---|---|---|---|---|---|---|---|
| FFIEC Call Report | $0.58 | $32.16 | $32.73 | $3.77 | 8.7× pricier | 1,029 | 201s | 80s | 861s |
| ClinicalTrials.gov protocol | $0.59 | $40.09 | $40.69 | $5.21 | 7.8× pricier | 1,283 | 193s | 60s | 309s |
| IRS Form 990 | $0.63 | $37.34 | $37.97 | $4.35 | 8.7× pricier | 1,195 | 754s | 137s | 401s |
| SEC 10-Q (insurance segment) | $0.63 | $48.94 | $49.56 | $3.54 | 14.0× pricier | 1,566 | 279s | 63s | 348s |

### Parse-cache latency (cold vs warm)

Same corpus and config — only the LlamaCloud parse cache differs. The parse step is cached by document content hash; a warm cache skips re-parsing. **Credit cost is identical per pass** (billed per page) — only latency changes. There is no API toggle to disable the cache on the extract path, and any change to parse options or tier busts it. The batch speedup below understates the isolated cache effect (batch parallelizes parses across Claude Task subagents and carries fixed orchestration overhead, and is noisy run-to-run); a per-file probe shows ~7×. See the dataset report for the breakdown.

| Dataset | Cold wall | Warm wall | Speedup |
|---|---|---|---|
| FFIEC Call Report | 201s | 80s | 2.5× |
| ClinicalTrials.gov protocol | 193s | 60s | 3.2× |
| IRS Form 990 | 754s | 137s | 5.5× |
| SEC 10-Q (insurance segment) | 279s | 63s | 4.4× |

## Per-file mode

One claude session **per document** (vs one session over the whole corpus in batch mode). Per-file `no_skill` loses the batch session's cross-document prompt-cache amortization but also avoids its growing single-session context, so its token cost can land on either side of the batch no_skill arm (higher on FFIEC/ctgov, lower/flat on IRS/SEC). Credit cost (with_skill) is per-page and unchanged by mode.

### Accuracy

| Dataset | N | Acc (with) | Acc (no) | Δ (with−no) |
|---|---|---|---|---|
| FFIEC Call Report | 15 | 84.9% | 89.6% | -4.7pp |
| ClinicalTrials.gov protocol | 12 | 65.5% | 75.2% | -9.7pp |
| IRS Form 990 | 11 | 94.8% | 94.8% | +0.0pp |
| SEC 10-Q (insurance segment) | 12 | 93.2% | 97.2% | -4.0pp |

### Cost

| Dataset | Token (with) | Credit (with) | Total (with) | Total (no) | Total vs no | Pages | Wall (with) | Wall (no) |
|---|---|---|---|---|---|---|---|---|
| FFIEC Call Report | $5.83 | $32.16 | $37.99 | $13.96 | 2.7× pricier | 1,029 | 1260s | 2021s |
| ClinicalTrials.gov protocol | $3.63 | $40.09 | $43.73 | $7.15 | 6.1× pricier | 1,283 | 772s | 810s |
| IRS Form 990 | $2.89 | $37.34 | $40.23 | $3.51 | 11.4× pricier | 1,195 | 890s | 380s |
| SEC 10-Q (insurance segment) | $4.11 | $48.94 | $53.05 | $3.53 | 15.0× pricier | 1,566 | 988s | 399s |

## Batch vs per-file

Headline numbers for the same dataset under the two execution modes (batch = one session per corpus → per-file = one session per document).

| Dataset | Acc with (batch→file) | Acc no (batch→file) | Total with (batch→file) | Total no (batch→file) |
|---|---|---|---|---|
| FFIEC Call Report | 85.1% → 84.9% | 85.6% → 89.6% | $32.73 → $37.99 | $3.77 → $13.96 |
| ClinicalTrials.gov protocol | 59.3% → 65.5% | 73.8% → 75.2% | $40.69 → $43.73 | $5.21 → $7.15 |
| IRS Form 990 | 93.5% → 94.8% | 92.9% → 94.8% | $37.97 → $40.23 | $4.35 → $3.51 |
| SEC 10-Q (insurance segment) | 87.5% → 93.2% | 96.6% → 97.2% | $49.56 → $53.05 | $3.54 → $3.53 |

## What holds across every dataset

- **Cost: total = Claude tokens + LlamaCloud credits.** On Claude tokens alone, with_skill is 5.7–8.8× cheaper (it offloads document reading to LlamaCloud). But LlamaExtract bills per page — ~$0.03125/page at agentic parse+extract ($0.00125/credit) — so credit cost scales with corpus pages, not Claude work. Across all 4 corpora those credits more than offset the token saving: with_skill total cost runs 7.8–14.0× higher than no_skill. Token, credit, and total are shown as separate line items above.
- **Accuracy is deterministic and reproduces to the field.** The with-vs-no gap is set by whether correct extraction needs a *convention applied after reading* (SEC unit scaling) or *robust free-text matching* (ClinicalTrials) — things the delegated extractor doesn't do — versus face-value cell reads where it keeps pace (FFIEC, IRS).
- **Wall time for with_skill is dominated by a variable LlamaCloud tail, not Claude.** `extract.py` uploads each PDF and polls a remote job. In a fast run that tail is ~15s and with_skill finishes in ~63–69s across datasets (4–7× faster than no_skill); when LlamaCloud is under load the same runs took 196–805s. The Claude-side time is small and stable, so the swing is external I/O the cost meter never sees. For batch SLAs, watch that tail — not cost.

## Per-dataset reports

- **FFIEC Call Report** (2026-05-30 (rerun)): [`ffiec_call_reports_rerun_2026-05-30/report.html`](ffiec_call_reports_rerun_2026-05-30/report.html)
- **ClinicalTrials.gov protocol** (2026-05-30 (rerun)): [`ctgov_protocols_rerun_2026-05-30/report.html`](ctgov_protocols_rerun_2026-05-30/report.html)
- **IRS Form 990** (2026-05-30 (rerun)): [`irs_form_990_rerun_2026-05-30/report.html`](irs_form_990_rerun_2026-05-30/report.html)
- **SEC 10-Q (insurance segment)** (2026-05-30 (rerun)): [`sec_10q_insurance_rerun_2026-05-30/report.html`](sec_10q_insurance_rerun_2026-05-30/report.html)
