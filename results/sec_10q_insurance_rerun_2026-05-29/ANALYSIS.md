# SEC 10-Q (insurance) batch run — deep dive + rerun

Two independent batch runs of the same 12-document corpus, both conditions
(`with_skill` = llama-extract loaded; `no_skill` = `--bare`). Model: `claude-opus-4-7`.

- **Run 1 (original)** — preserved at `results/sec_10q_insurance/report.html`
- **Run 2 (rerun, this dir)** — `results/sec_10q_insurance_rerun_2026-05-29/report.html`

## Headline numbers

| | Acc (with) | Acc (no) | Cost (with) | Cost (no) | Wall (with) | Wall (no) |
|---|---|---|---|---|---|---|
| Run 1 | 79.0% | 97.2% | $0.64 | $1.98 | **355s** | 234s |
| Run 2 | 79.0% | 97.2% | $0.49 | $2.18 | **64s** | 281s |

**Accuracy is identical across runs** (139 correct / 37 wrong with_skill; 171 / 5 no_skill).
**Cost is stable** (with_skill ~3–4× cheaper). **Wall time for with_skill swung 5.5×** (355s → 64s).

---

## Q1 — Why is accuracy *lower* with the skill?

It is one systematic error, reproduced bit-for-bit across both runs: **dropped unit conversion.**

- Of 37 with_skill misses, **28 are off by exactly ×1,000,000** (extracted ≈ truth × 1e-6).
- Every filing producing these errors has an **"(in millions)" header** on its statements
  (verified: CIK 80661 `($ in millions)`, 86312 `(in millions, except per share amounts)`,
  914208 `(in millions, except per share data)`, 1099219, 899051, 20286).
- llama-extract (LlamaCloud) reads the table cell literally — `22,188` → `22188.0` — and does
  **not** apply the schema's instruction *"convert to raw USD: multiply by 1,000,000 for millions."*

Why the conventions are dropped: the unit guidance lives in the prompt the **main Claude agent**
sees, but the actual field-by-field extraction is delegated to the LlamaCloud extract job, which
keys off the JSON-Schema *field descriptions* and applies its own literal table reading. Three
filings denominated entirely in millions account for ~26 of the 28 scaling misses.

The remaining ~9 with_skill errors are wrong-line / subtotal picks (ratios ≈ 0.81×, 1.02×, 1.24×,
and two `0.0`s — e.g. net-vs-gross insurance reserves), a smaller and different error class.

`no_skill` runs `pdftotext`, *sees* the "(in millions)" header in context, applies ×1e6 itself,
and lands at 97.2% with only 5 scattered errors.

**Conclusion:** the accuracy gap is a *unit-normalization* problem specific to financial statements
reported in millions/thousands — not a parsing-quality problem. It would likely close if the field
descriptions encoded scale explicitly, or if post-processing multiplied by the detected header unit.

## Q2 — Why is latency *higher* with the skill, even though cost is lower?

Cost and wall time measure different things, and the skill trades one for the other.

**Cost tracks Claude tokens.** with_skill offloads document reading to LlamaCloud, so the Claude side
does little: run 1 = 2 turns / 62s API / 3.9k output tokens; run 2 = 20 turns / 49s API / 4.3k tokens.
no_skill reads all 12 PDFs itself: 45–49 turns, ~16–18k output tokens, ~233–277s API → ~3–4× the cost.

**Wall time tracks the critical path**, which for with_skill is dominated by work that is *not* Claude
tokens. The main session spawns ~12 background Task subagents, each running `extract.py`, which
**uploads the PDF to LlamaCloud and polls a remote parse-then-extract job** (`polling_interval=2s`,
timeout 1800s). That remote latency is pure I/O wait, invisible to the cost meter.

The decisive evidence is the run-to-run swing:

| | Claude API time | LlamaCloud wait (wall − API) | Total wall |
|---|---|---|---|
| with_skill run 1 | 62s | **~293s** | 355s |
| with_skill run 2 | 49s | **~15s** | 64s |

The Claude-side time is small and stable (~50–62s). **All the variance is the LlamaCloud queue +
processing tail** — ~293s when the service was loaded (run 1), ~15s when it was fast (run 2). In
run 2 with_skill was actually **4.4× faster than no_skill** (64s vs 281s), which is the expected
ordering given it does far less Claude work.

**Conclusion:** run 1's "skill is slower" was a LlamaCloud server-side load artifact, not an
intrinsic property of the skill. The skill's intrinsic profile is *cheaper and usually faster*, but
its wall time carries a long, variable tail set by external extraction-service latency. For
SLA-sensitive batch jobs, that tail — not the Claude cost — is the thing to watch.

## What did NOT change between runs

- Accuracy (deterministic — the unit bug is systematic).
- Skill fired 1/1; 12 extract.py CLI calls; 0 `.py` wrapper files written (clean delegation).
- A/B integrity verified (`llama-extract` present in with_skill init, absent in no_skill).
