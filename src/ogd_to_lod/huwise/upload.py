"""Upload seam for pushing generated results back to Huwise.

NOT IMPLEMENTED. This is a wired-but-inert hook so the upload step is a
localized addition later. The intended target/API is not yet defined.

When implemented, upload_results should consume the artifacts produced by the
mapping session. For a local run (--local) those live in the folder returned by
`session.flow.get_local_output_path()` (mapping.yarrrml.yaml, data.csv,
metadata.ttl); otherwise the created PR is available via
`session.flow.get_pr_url()`. Note the fully materialized RDF (observations.ttl)
is not produced by the Python pipeline today — an implementation would need to
run the RMLMapper conversion (see validation/validator.py) or consume the
results folder.
"""

from __future__ import annotations

from ogd_to_lod.config import Config
from ogd_to_lod.runner import MappingSessionResult


class HuwiseUploadError(Exception):
    """Raised when uploading results to Huwise fails."""


def upload_results(
    session: MappingSessionResult,
    config: Config,
    dataset_id: str,
) -> None:
    """Upload the generated mapping results back to Huwise.

    Args:
        session: The completed mapping session (holds the flow + its outputs).
        config: Loaded application configuration.
        dataset_id: The Huwise dataset the results belong to.

    Raises:
        NotImplementedError: always, until an upload target is defined.
    """
    raise NotImplementedError("Huwise upload is not implemented yet")
