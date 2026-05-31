"""DatasetConfig: the shape every dataset must implement.

A DatasetConfig binds a slug to its schema, ground-truth builder, PDF fetcher,
prompt content, and document-key convention. All path helpers derive from the
slug so that data/, runs/, runs_batch/, results/ are partitioned per dataset.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel


@dataclass(frozen=True)
class DatasetConfig:
    slug: str
    display_name: str

    # The Pydantic schema agents must populate.
    schema_cls: type[BaseModel]

    # Given a manifest record (dict), produce the stable document key string used
    # for filenames and per-run dirs (e.g., "207872_2024-09-30" for FFIEC).
    doc_key_fn: Callable[[dict[str, Any]], str]

    # Field-level comparison overrides. Defaults to numeric for every schema
    # field; entries here override with "string" or "ignore" ("ignore" = scored
    # as "na" regardless of presence).
    field_compare_overrides: dict[str, str]

    # Long-form domain intro inserted into the extraction prompts. Should explain
    # what the document is, what's in it, and any field-locating hints.
    domain_intro: str

    # Unit-conversion rules block inserted after the schema dump.
    unit_conventions: str

    # Synchronously fetch ground-truth values for one manifest record. Returns a
    # dict with at least {"values": {field: value or null}}; conventionally also
    # carries identifying keys plus "populated_count"/"total_fields".
    gt_builder: Callable[[dict[str, Any]], dict[str, Any]]

    # Fetch PDFs for the dataset. Implementations either download programmatically
    # or write a DOWNLOAD_INSTRUCTIONS.md (like FFIEC CDR). Receives the manifest
    # list and the target dir. Returns (n_present, n_total).
    pdf_fetcher: Callable[[list[dict[str, Any]], Path], tuple[int, int]]

    # ---- Path helpers — sane defaults cover every dataset. ----
    def data_dir(self) -> Path:
        return Path("data") / self.slug

    def pdf_dir(self) -> Path:
        # BENCH_PDF_DIR lets experiments (e.g. cold-cache latency runs) point the
        # benchmark at an alternate set of <doc_key>.pdf files without touching the
        # source data. Set it only for the experiment subprocess.
        override = os.environ.get("BENCH_PDF_DIR")
        if override:
            return Path(override)
        return self.data_dir() / "pdfs"

    def gt_dir(self) -> Path:
        return self.data_dir() / "ground_truth"

    def manifest_path(self) -> Path:
        return self.data_dir() / "manifest.json"

    def runs_dir(self) -> Path:
        return Path("runs") / self.slug

    def runs_batch_dir(self) -> Path:
        return Path("runs_batch") / self.slug

    def results_dir(self) -> Path:
        return Path("results") / self.slug

    def pdf_path(self, record: dict[str, Any]) -> Path:
        return self.pdf_dir() / f"{self.doc_key_fn(record)}.pdf"

    def gt_path(self, record: dict[str, Any]) -> Path:
        return self.gt_dir() / f"{self.doc_key_fn(record)}.json"

    def run_dir(self, record: dict[str, Any], condition: str) -> Path:
        return self.runs_dir() / f"{self.doc_key_fn(record)}_{condition}"

    def batch_session_dir(self, condition: str, tag: str = "") -> Path:
        name = f"{condition}__{tag}" if tag else condition
        return self.runs_batch_dir() / name

    def batch_fanout_dir(self, record: dict[str, Any], condition: str, tag: str = "") -> Path:
        name = f"{condition}__{tag}" if tag else condition
        return self.runs_batch_dir() / f"{self.doc_key_fn(record)}_{name}"
