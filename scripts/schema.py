"""DEPRECATED shim. Use scripts.datasets.ffiec_call_reports directly.

Kept temporarily so any stale `from scripts.schema import ...` imports keep
working during the multi-dataset migration. New code should import from the
dataset module (scripts.datasets.ffiec_call_reports) or go through the dataset
registry (scripts.datasets.get_dataset).
"""
from scripts.datasets.ffiec_call_reports import (  # noqa: F401
    CallReportExtraction,
    DOLLAR_FIELDS,
    FDIC_FIELD_MAP,
    RATIO_FIELDS,
    fdic_to_schema_value,
)
