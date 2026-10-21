"""Upload seam for pushing generated results back to Huwise.

"""

from __future__ import annotations

import json
import os

import requests
from ogd_to_lod.config import Config
from ogd_to_lod.huwise import DatasetSetupError
from ogd_to_lod.huwise.prepare_mapping_for_huwise import prepare_mapping
from ogd_to_lod.runner import MappingSessionResult
from ogd_to_lod.logging import get_logger

logger = get_logger(__name__)


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
    rml_mapping = session.flow.get_generated_rml()
    if rml_mapping is None:
        return

    api_key = config.huwise.api_key
    if not api_key:
        raise HuwiseUploadError("HUWISE_API_KEY is not configured")


    huwise_domain = os.environ.get("HUWISE_DOMAIN", "").strip()
    automation_base = resolve_base_url(huwise_domain)
    headers = {"Authorization": f"ApiKey {api_key}"}

    dataset_uid = _resolve_dataset_uid(huwise_domain, dataset_id, headers)

    prepared_mapping, unknown_fields = prepare_mapping(
        rml_mapping, domain=huwise_domain, dataset_id=dataset_id, headers=headers
    )
    if unknown_fields:
        logger.warning(
            "Unmapped field references in generated mapping for dataset %s: %s",
            dataset_id,
            ", ".join(unknown_fields),
        )

    meta_base = f"{automation_base}/datasets/{dataset_uid}/metadata/semantic"
    _put_metadata_field(headers, f"{meta_base}/rml_mapping/", prepared_mapping)

    publish_url = f"{automation_base}/datasets/{dataset_uid}/publish_metadata/"
    try:
        resp = requests.post(publish_url, headers=headers, timeout=60)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise HuwiseUploadError(f"Failed to publish metadata: {exc}") from exc


def resolve_base_url(domain: str) -> str:
    """Derive the OpenDataSoft Automatation API base URL from a Huwise domain.
    """
    normalized = (domain or "").strip()
    if not normalized:
        raise DatasetSetupError("HUWISE_DOMAIN is empty")
    if normalized.startswith("https://"):
        normalized = normalized[len("https://"):]
    elif normalized.startswith("http://"):
        normalized = normalized[len("http://"):]
    normalized = normalized.strip("/")
    if not normalized:
        raise DatasetSetupError("HUWISE_DOMAIN (normalized) is empty")
    return f"https://{normalized}/api/automation/v1.0"

def _resolve_dataset_uid(domain: str, dataset_id: str, headers: dict[str, str]) -> str:
    url = f"https://{domain}/api/explore/v2.1/catalog/datasets/{dataset_id}"
    try:
        resp = requests.get(url, headers={**headers, "Content-Type": "application/json"},timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise HuwiseUploadError(
            f"Failed to resolve dataset_uid for dataset_id={dataset_id}: {exc}"
        ) from exc
    try:
        return resp.json()["dataset_uid"]
    except (KeyError, ValueError) as exc:
        raise HuwiseUploadError(
            f"Unexpected response resolving dataset_uid for {dataset_id}: {exc}"
        ) from exc


def _put_metadata_field(headers: dict[str, str], url: str, value: str) -> None:
    try:
        resp = requests.put(
            url,
            headers={**headers, "Content-Type": "application/json"},
            data=json.dumps({"value": value}),
            timeout=60,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise HuwiseUploadError(f"Failed to PUT {url}: {exc}") from exc
