# Cross-Dataset Batch-Mode Comparison

Generated 2026-05-30 by `scripts/render_cross_dataset_summary.py`.

Claude Code extracting a structured schema from a corpus of PDFs, one batch session per condition. **with_skill** loads the `llama-extract` skill (delegates extraction to a LlamaCloud parse+extract job); **no_skill** runs `--bare` (Claude reads the PDFs directly). Model `claude-opus-4-7`. Numbers below use the latest run per dataset (rerun where present).

## Accuracy

| Dataset | N | Acc (with) | Acc (no) | Δ (with−no) | Why |
|---|---|---|---|---|---|
| FFIEC Call Report | 15 | 84.9% | 80.9% | +4.0pp | Skill **edges ahead** — Call Report values are raw dollars on a fixed form grid; delegated extraction reads the schedule cells cleanly. |
| ClinicalTrials.gov protocol | 12 | 60.7% | 79.3% | -18.6pp | Skill **trails by ~20pp** — brittle exact-string matches on free-text `brief_title`/`sponsor`, plus nulls on categorical design fields (allocation/masking) that direct reading recovers from deep in the document. |
| IRS Form 990 | 11 | 94.2% | 95.5% | -1.3pp | **Near tie** — Form 990 amounts are raw dollars (no unit trap). Most remaining errors are `investment_income`, which mismatches the ground-truth line definition under *both* conditions. |
| SEC 10-Q (insurance segment) | 12 | 69.9% | 97.2% | -27.3pp | Skill **trails by ~18pp** — a systematic dropped unit conversion: on "(in millions)" filings llama-extract returns the table value verbatim (28 of 37 misses are off by exactly ×1,000,000). Direct reading sees the header and multiplies. |

## Cost & latency

Total cost = Claude tokens + LlamaCloud credits (parse + extract, billed per page at $0.00125/credit). `no_skill` never calls LlamaCloud, so its credit cost is $0 and its total equals its token cost.

| Dataset | Token (with) | Credit (with) | Total (with) | Total (no) | Total vs no | Pages | Wall cold (with) | Wall warm (with) | Wall (no) |
|---|---|---|---|---|---|---|---|---|---|
| FFIEC Call Report | $0.65 | $32.16 | $32.80 | $1.98 | 16.6× pricier | 1,029 | 201s | 80s | 566s |
| ClinicalTrials.gov protocol | $0.66 | $40.09 | $40.75 | $4.06 | 10.0× pricier | 1,283 | 193s | 60s | 302s |
| IRS Form 990 | $0.82 | $37.34 | $38.16 | $2.74 | 13.9× pricier | 1,195 | 754s | 137s | 327s |
| SEC 10-Q (insurance segment) | $0.70 | $48.94 | $49.64 | $1.98 | 25.1× pricier | 1,566 | 279s | 63s | 235s |

## Parse-cache latency (cold vs warm)

Same corpus and config — only the LlamaCloud parse cache differs. The parse step is cached by document content hash; a warm cache skips re-parsing. **Credit cost is identical per pass** (billed per page) — only latency changes. There is no API toggle to disable the cache on the extract path, and any change to parse options or tier busts it. The batch speedup below understates the isolated cache effect (batch parallelizes parses across Claude Task subagents and carries fixed orchestration overhead, and is noisy run-to-run); a per-file probe shows ~7×. See the dataset report for the breakdown.

| Dataset | Cold wall | Warm wall | Speedup |
|---|---|---|---|
| FFIEC Call Report | 201s | 80s | 2.5× |
| ClinicalTrials.gov protocol | 193s | 60s | 3.2× |
| IRS Form 990 | 754s | 137s | 5.5× |
| SEC 10-Q (insurance segment) | 279s | 63s | 4.4× |

## What holds across every dataset

- **Cost: total = Claude tokens + LlamaCloud credits.** On Claude tokens alone, with_skill is 2.8–6.2× cheaper (it offloads document reading to LlamaCloud). But LlamaExtract bills per page — ~$0.03125/page at agentic parse+extract ($0.00125/credit) — so credit cost scales with corpus pages, not Claude work. Across all 4 corpora those credits more than offset the token saving: with_skill total cost runs 10.0–25.1× higher than no_skill. Token, credit, and total are shown as separate line items above.
- **Accuracy is deterministic and reproduces to the field.** The with-vs-no gap is set by whether correct extraction needs a *convention applied after reading* (SEC unit scaling) or *robust free-text matching* (ClinicalTrials) — things the delegated extractor doesn't do — versus face-value cell reads where it keeps pace (FFIEC, IRS).
- **Wall time for with_skill is dominated by a variable LlamaCloud tail, not Claude.** `extract.py` uploads each PDF and polls a remote job. In a fast run that tail is ~15s and with_skill finishes in ~63–69s across datasets (4–7× faster than no_skill); when LlamaCloud is under load the same runs took 196–805s. The Claude-side time is small and stable, so the swing is external I/O the cost meter never sees. For batch SLAs, watch that tail — not cost.

## Per-dataset reports

- **FFIEC Call Report** (2026-05-30 (rerun)): [`ffiec_call_reports_rerun_2026-05-30/report.html`](ffiec_call_reports_rerun_2026-05-30/report.html)
- **ClinicalTrials.gov protocol** (2026-05-30 (rerun)): [`ctgov_protocols_rerun_2026-05-30/report.html`](ctgov_protocols_rerun_2026-05-30/report.html)
- **IRS Form 990** (2026-05-30 (rerun)): [`irs_form_990_rerun_2026-05-30/report.html`](irs_form_990_rerun_2026-05-30/report.html)
- **SEC 10-Q (insurance segment)** (2026-05-30 (rerun)): [`sec_10q_insurance_rerun_2026-05-30/report.html`](sec_10q_insurance_rerun_2026-05-30/report.html)
