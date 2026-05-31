# Agent Extraction Experiments

A benchmark measuring whether giving a **Claude Code agent a bundled `llama-extract` skill** (a thin CLI wrapper around [LlamaIndex's LlamaExtract](https://developers.llamaindex.ai/llamaparse/extract/sdk/)) beats Claude doing structured extraction **itself** with its native `Read`/`Bash` tools.

For each document, the harness runs `claude --print` (headless) twice under identical settings except one lever:

- **`with_skill`** — the `llama-extract` skill is staged into the run, so Claude delegates parse + extract to LlamaCloud (billed as per-page **credits**).
- **`no_skill`** — the *same* full agent with the skill **not** staged, so Claude reads the PDFs and extracts itself (Claude **tokens** only). A stripped 3-tool `--bare` agent is an opt-in variant (`--no-skill-agent bare`).

Each run populates a fixed Pydantic/JSON schema, scored against **authoritative non-PDF ground truth** (regulator/registry APIs). We measure **accuracy**, **cost** — split into Claude **token cost** and LlamaCloud **credit cost** (`total = token + credit`) — and **latency**.

## Results

Four public-record datasets, batch mode (`claude-opus-4-7`, one session per condition), scored on **accuracy**, **cost** (token + credit + total), and **latency**. Once LlamaCloud credits are counted, `with_skill` total cost is a large multiple of `no_skill`, while accuracy is domain-dependent. Numbers, per-field breakdowns, and the mechanism analysis:

- [`results/cross_dataset_summary.md`](results/cross_dataset_summary.md) — cross-dataset comparison (also `.html`)
- `results/<slug>_rerun_2026-05-30/report.html` — per-dataset, field-level reports

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
results/              Per-dataset scored CSVs + summary JSON + report.html, plus cross_dataset_summary.{md,html}
runs/, runs_batch/     Raw session transcripts (gitignored — reproducible build artifacts)
plans/, research/     Design docs and findings (gitignored — local working notes)
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

Both conditions run the **same full agent** under identical flags — model, prompt, an empty MCP config, disallowed `WebFetch`/`WebSearch`, and `--setting-sources project,local` — differing only by whether the `llama-extract` skill is staged into the run. The harness verifies the skill loaded (or didn't) by reading the `skills` array from each session's `system/init` trace event, so a contaminated run is caught rather than silently scored. (A stripped `--bare` no_skill agent is available via `--no-skill-agent bare` to isolate agent-scaffolding cost.)

## License

[MIT](LICENSE) © 2026 Jerry Liu. The bundled `llama-extract` skill is authored by LlamaIndex and is likewise MIT-licensed.
