"""Dataset registry. Each dataset is a DatasetConfig.

Add a new dataset by creating scripts/datasets/<slug>.py exporting a CONFIG
object of type DatasetConfig, then registering it here.
"""
from __future__ import annotations

from scripts.datasets.base import DatasetConfig
from scripts.datasets import (
    ctgov_protocols,
    ffiec_call_reports,
    irs_form_990,
    sec_10q_insurance,
)

REGISTRY: dict[str, DatasetConfig] = {
    "ffiec_call_reports": ffiec_call_reports.CONFIG,
    "ctgov_protocols": ctgov_protocols.CONFIG,
    "irs_form_990": irs_form_990.CONFIG,
    "sec_10q_insurance": sec_10q_insurance.CONFIG,
}

DEFAULT_SLUG = "ffiec_call_reports"


def get_dataset(slug: str) -> DatasetConfig:
    if slug not in REGISTRY:
        raise KeyError(f"Unknown dataset slug: {slug}. Known: {sorted(REGISTRY)}")
    return REGISTRY[slug]


def all_slugs() -> list[str]:
    return list(REGISTRY)
