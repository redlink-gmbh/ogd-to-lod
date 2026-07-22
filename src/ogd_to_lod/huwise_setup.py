"""Dataset bootstrap utilities for Huwise/OpenDataSoft sources."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import urlopen

from ogd_to_lod.logging import get_logger

logger = get_logger(__name__)


class DatasetSetupError(Exception):
    """Raised when dataset bootstrap fails."""


@dataclass
class DatasetSetupResult:
    """Resolved local inputs for dataset-id mode."""

    csv_path: str
    context_paths: list[str]
    setup_dir: str


def prepare_dataset_inputs(dataset_id: str, base_url: str) -> DatasetSetupResult:
    """Fetch dataset artifacts and materialize local inputs for the mapping flow."""
    if not dataset_id.strip():
        raise DatasetSetupError("Dataset id is empty")
    if not base_url.strip():
        raise DatasetSetupError("HUWISE_DOMAIN (normalized) is empty")

    dataset_id = dataset_id.strip()
    base_url = base_url.rstrip("/")

    metadata_url = f"{base_url}/catalog/datasets/{quote(dataset_id)}"
    csv_url = f"{base_url}/catalog/datasets/{quote(dataset_id)}/exports/csv?" + urlencode(
        {"delimiter": ",", "use_labels": "true"}
    )
    ttl_where = f'dataset_id="{dataset_id}"'
    ttl_url = f"{base_url}/catalog/exports/ttl?" + urlencode({"where": ttl_where})

    metadata = _fetch_json(metadata_url, dataset_id)
    csv_content = _fetch_text(csv_url, dataset_id)
    ttl_content = _fetch_text(ttl_url, dataset_id)

    setup_dir = _create_setup_dir(dataset_id)
    csv_path = setup_dir / "data.csv"
    ttl_path = setup_dir / "dcat.ttl"
    metadata_path = setup_dir / "dataset_metadata.json"
    fields_path = setup_dir / "fields.json"

    csv_path.write_text(csv_content, encoding="utf-8")
    logger.info("Saved CSV export: %s", csv_path)
    ttl_path.write_text(ttl_content, encoding="utf-8")
    logger.info("Saved TTL metadata export: %s", ttl_path)
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Saved dataset metadata JSON: %s", metadata_path)

    fields_payload = _build_fields_payload(metadata)
    fields_path.write_text(json.dumps(fields_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Generated fields.json: %s", fields_path)

    return DatasetSetupResult(
        csv_path=str(csv_path),
        context_paths=[str(ttl_path), str(fields_path)],
        setup_dir=str(setup_dir),
    )


def _create_setup_dir(dataset_id: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    setup_dir = Path.cwd() / ".work" / "dataset_setup" / f"{timestamp}-{dataset_id}"
    setup_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Created setup directory: %s", setup_dir)
    return setup_dir


def _fetch_text(url: str, dataset_id: str) -> str:
    try:
        with urlopen(url, timeout=30) as response:
            content = response.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        raise DatasetSetupError(
            f"Dataset setup failed for '{dataset_id}': GET {url} returned HTTP {e.code}"
        ) from e
    except URLError as e:
        raise DatasetSetupError(
            f"Dataset setup failed for '{dataset_id}': GET {url} failed ({e.reason})"
        ) from e
    except Exception as e:
        raise DatasetSetupError(
            f"Dataset setup failed for '{dataset_id}': GET {url} failed ({e})"
        ) from e

    logger.info("Fetched endpoint successfully: %s", url)
    return content


def _fetch_json(url: str, dataset_id: str) -> dict:
    content = _fetch_text(url, dataset_id)
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise DatasetSetupError(
            f"Dataset setup failed for '{dataset_id}': invalid JSON from {url} ({e})"
        ) from e


def _build_fields_payload(metadata: dict) -> dict:
    fields = metadata.get("fields") or []
    default_metas = (metadata.get("metas") or {}).get("default") or {}

    columns: list[dict] = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        label = field.get("label") or field.get("name")
        columns.append(
            {
                "header_name": label,
                "name": field.get("name"),
                "type": field.get("type"),
                "description": field.get("description"),
                "comment": _build_field_comment(field),
            }
        )

    return {
        "dataset_id": metadata.get("dataset_id"),
        "title": default_metas.get("title"),
        "description": default_metas.get("description"),
        "publisher": default_metas.get("publisher"),
        "records_count": default_metas.get("records_count"),
        "columns": columns,
    }


def _build_field_comment(field: dict) -> str | None:
    annotations = field.get("annotations")
    if not isinstance(annotations, dict):
        return None
    unit = annotations.get("unit")
    if unit:
        return f"unit: {unit}"
    return None
