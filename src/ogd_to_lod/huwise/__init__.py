"""Huwise (OpenDataSoft) integration: download inputs and (later) upload results.

Optional wrapper around the core ogd-to-lod pipeline. Exposed via the
`ogd-to-lod-huwise` console command (ogd_to_lod.huwise.cli:main).
"""

from ogd_to_lod.huwise.download import (
    DatasetSetupError,
    DatasetSetupResult,
    prepare_dataset_inputs,
    resolve_base_url,
)
from ogd_to_lod.huwise.upload import HuwiseUploadError, upload_results

__all__ = [
    "DatasetSetupError",
    "DatasetSetupResult",
    "prepare_dataset_inputs",
    "resolve_base_url",
    "HuwiseUploadError",
    "upload_results",
]
