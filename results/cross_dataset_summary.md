# Cross-Dataset Batch-Mode Comparison

Generated 2026-05-29 by `scripts/render_cross_dataset_summary.py`.

Claude Code extracting a structured schema from a corpus of PDFs, one batch session per condition. **with_skill** loads the `llama-extract` skill (delegates extraction to a LlamaCloud parse+extract job); **no_skill** runs `--bare` (Claude reads the PDFs directly). Model `claude-opus-4-7`. Numbers below use the latest run per dataset (rerun where present).

## Accuracy

| Dataset | N | Acc (with) | Acc (no) | Δ (with−no) | Why |
|---|---|---|---|---|---|
| FFIEC Call Report | 15 | 85.1% | 80.9% | +4.2pp | Skill **edges ahead** — Call Report values are raw dollars on a fixed form grid; delegated extraction reads the schedule cells cleanly. |
| ClinicalTrials.gov protocol | 12 | 59.3% | 81.4% | -22.1pp | Skill **trails by ~20pp** — brittle exact-string matches on free-text `brief_title`/`sponsor`, plus nulls on categorical design fields (allocation/masking) that direct reading recovers from deep in the document. |
| IRS Form 990 | 11 | 94.2% | 94.8% | -0.6pp | **Near tie** — Form 990 amounts are raw dollars (no unit trap). Most remaining errors are `investment_income`, which mismatches the ground-truth line definition under *both* conditions. |
| SEC 10-Q (insurance segment) | 12 | 79.0% | 97.2% | -18.2pp | Skill **trails by ~18pp** — a systematic dropped unit conversion: on "(in millions)" filings llama-extract returns the table value verbatim (28 of 37 misses are off by exactly ×1,000,000). Direct reading sees the header and multiplies. |

## Cost & latency

| Dataset | Cost (with) | Cost (no) | Cost saving | Wall (with) | Wall (no) |
|---|---|---|---|---|---|
| FFIEC Call Report | $0.72 | $1.98 | 2.7× cheaper | 92s | 566s |
| ClinicalTrials.gov protocol | $0.37 | $3.04 | 8.1× cheaper | 63s | 327s |
| IRS Form 990 | $0.58 | $4.62 | 8.0× cheaper | 69s | 488s |
| SEC 10-Q (insurance segment) | $0.49 | $2.18 | 4.4× cheaper | 64s | 281s |

## What holds across every dataset

- **Cost: with_skill is 3–8× cheaper, consistently.** Cost tracks Claude tokens; the skill offloads document reading to LlamaCloud, so the Claude side does little.
- **Accuracy is deterministic and reproduces to the field.** The with-vs-no gap is set by whether correct extraction needs a *convention applied after reading* (SEC unit scaling) or *robust free-text matching* (ClinicalTrials) — things the delegated extractor doesn't do — versus face-value cell reads where it keeps pace (FFIEC, IRS).
- **Wall time for with_skill is dominated by a variable LlamaCloud tail, not Claude.** `extract.py` uploads each PDF and polls a remote job. In a fast run that tail is ~15s and with_skill finishes in ~63–69s across datasets (4–7× faster than no_skill); when LlamaCloud is under load the same runs took 196–805s. The Claude-side time is small and stable, so the swing is external I/O the cost meter never sees. For batch SLAs, watch that tail — not cost.

## Per-dataset reports

- **FFIEC Call Report** (original): [`ffiec_call_reports/report.html`](ffiec_call_reports/report.html)
- **ClinicalTrials.gov protocol** (2026-05-29 (rerun)): [`ctgov_protocols_rerun_2026-05-29/report.html`](ctgov_protocols_rerun_2026-05-29/report.html)
- **IRS Form 990** (2026-05-29 (rerun)): [`irs_form_990_rerun_2026-05-29/report.html`](irs_form_990_rerun_2026-05-29/report.html)
- **SEC 10-Q (insurance segment)** (2026-05-29 (rerun)): [`sec_10q_insurance_rerun_2026-05-29/report.html`](sec_10q_insurance_rerun_2026-05-29/report.html)
