# Agent Extraction Experiments

A benchmark measuring whether giving a **Claude Code agent a bundled `llama-extract` skill** (a thin CLI wrapper around [LlamaIndex's LlamaExtract](https://developers.llamaindex.ai/llamaparse/extract/sdk/)) beats Claude doing structured extraction **itself** with its native `Read`/`Bash` tools.

For each document, the harness runs `claude --print` (headless) twice under identical settings except one lever:

- **`with_skill`** — a project-local `.claude/skills/llama-extract/` is staged into the run, so Claude delegates parse + extract to LlamaCloud.
- **`no_skill`** — `--bare`, so Claude reads the PDF pages directly and extracts on its own.

Each run is asked to populate a fixed Pydantic/JSON schema, and the output is scored against **authoritative non-PDF ground truth** (regulator/registry APIs), measuring **accuracy, cost, and latency**.

## Headline results

Across four public-record datasets in batch mode (`claude-opus-4-7`, one session per condition), the skill is **dramatically cheaper everywhere**, but accuracy is **domain-dependent**:

| Dataset | N | `with_skill` | `no_skill` | Δ accuracy | Cost advantage (skill) |
|---|---:|---:|---:|---:|---:|
| FFIEC Call Reports | 15 | 85.1% | 80.9% | **+4.2pp** | ~3–8× cheaper |
| ClinicalTrials.gov protocols | 12 | 59.3% | 81.4% | −22.1pp | ~8× cheaper |
| IRS Form 990 | 11 | 94.2% | 94.8% | −0.6pp | cheaper |
| SEC 10-Q (insurance) | 12 | 79.0% | 97.2% | −18.2pp | cheaper |

The cost win is consistent; the accuracy gaps are deterministic and mechanism-specific (e.g. SEC misses are dominated by a dropped "(in millions)" unit conversion). Full write-up: [`results/cross_dataset_summary.md`](results/cross_dataset_summary.md) (and `.html`).

## Datasets

All source documents are **public records**. Ground truth comes from each domain's authoritative structured API, never from the PDF:

| Slug | Documents | Ground-truth source |
|---|---|---|
| `ffiec_call_reports` | Bank Call Reports (2024-09-30) | FDIC BankFind financials API |
| `ctgov_protocols` | Clinical-trial protocols / SAPs | ClinicalTrials.gov v2 API |
| `irs_form_990` | Nonprofit Form 990 returns | ProPublica Nonprofit Explorer |
| `sec_10q_insurance` | Insurance-issuer 10-Q filings | SEC XBRL CompanyFacts |

## Repository layout

```
llama-extract/        The Claude Code skill under test (SKILL.md + bundled extract.py CLI)
scripts/              Benchmark harness
  run_benchmark.py    Orchestrator — runs claude --print per condition (per-file or batch)
  score.py            Scores output.json vs ground truth
  prompt.py           Builds the extraction prompts
  datasets/           DatasetConfig registry (one module per dataset)
  build_ground_truth.py, fetch_pdfs.py, seed_*_manifest.py, select_banks.py
  compute_invocation_stats.py, render_report.py, render_cross_dataset_summary.py
  run_phase5.sh       Convenience driver: run -> score -> stats -> report
data/<slug>/          manifest.json, pdfs/, ground_truth/ per dataset
results/<slug>/        Scored CSVs, summary JSON, and HTML reports
runs/, runs_batch/     Raw session transcripts (gitignored — reproducible build artifacts)
plans/, research/     Design docs and findings from each phase of the project
```

> `runs/` and `runs_batch/` hold the full `stream-json` session transcripts (~347 MB, mostly base64 PDF imagery in `no_skill` traces). They are **gitignored** and regenerable — see below.

## Setup

Requires **Python 3.9+** and the **`claude` CLI** on your `PATH`.

```bash
pip install -r requirements.txt
# Optional: needed only to auto-render SEC 10-Q PDFs from iXBRL
playwright install chromium

export ANTHROPIC_API_KEY=...      # for the claude CLI runs
export LLAMA_CLOUD_API_KEY=...    # for the llama-extract skill (with_skill condition)
export PYTHONPATH=.               # scripts import the `scripts.*` package
```

## Running the benchmark

```bash
# Default dataset (FFIEC), per-file mode, both conditions:
python scripts/run_benchmark.py

# A specific dataset in batch mode (one session per condition over the whole corpus):
python scripts/run_benchmark.py --dataset ctgov_protocols --mode batch

# Inspect the exact claude invocations without executing anything:
python scripts/run_benchmark.py --mode batch --limit 2 --dry-run
```

Then score and render:

```bash
python scripts/score.py --dataset ctgov_protocols --mode batch
python scripts/render_report.py --dataset ctgov_protocols
python scripts/render_cross_dataset_summary.py
```

The full pipeline for the three newer datasets is wrapped in `scripts/run_phase5.sh`.

To (re)build a dataset's inputs from scratch: `scripts/seed_*_manifest.py` (or `select_banks.py`) → `fetch_pdfs.py` → `build_ground_truth.py`.

## How the A/B stays clean

Both conditions share identical flags — model, prompt, an empty MCP config, and disallowed `WebFetch`/`WebSearch` — differing only by `--setting-sources project,local` (loads the staged skill) vs `--bare`. The harness verifies the skill loaded (or didn't) by reading the `skills` array from each session's `system/init` trace event, so a contaminated run is caught rather than silently scored.

## License

[MIT](LICENSE) © 2026 Jerry Liu. The bundled `llama-extract` skill is authored by LlamaIndex and is likewise MIT-licensed.
