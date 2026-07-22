"""AI-based normalization of arbitrary dataset context into DatasetContext."""

import json
import re

from ogd_to_lod.ai import AIService
from ogd_to_lod.logging import get_logger

from .models import ColumnContext, DatasetContext, SpatialCoverage, TemporalCoverage

logger = get_logger(__name__)

CONTEXT_NORMALIZATION_PROMPT = """\
You are extracting structured metadata from dataset documentation.

The documentation below may be in any format (DCAT/RDF, plain text, Markdown, JSON, etc.)
and may cover multiple files separated by "=== filename ===" headers.

Extract all available information and return it as JSON with this exact structure:

```json
{{
  "title": "dataset title or null",
  "description": "dataset description or null",
  "publisher": "publisher name or null",
  "keywords": ["keyword1", "keyword2"],
  "issued": "ISO date string or null",
  "modified": "ISO date string or null",
  "language": "language code or null",
  "license": "license URI or name or null",
  "access_rights": "access rights description or null",
  "contact_point": "contact email or name or null",
  "temporal_coverage": {{"start": "date or null", "end": "date or null"}},
  "spatial_coverage": {{"location": "place name or URI or null"}},
  "columns": [
    {{
      "header_name": "exact CSV column header",
      "description": "human-readable description of this column or null",
      "comment": "additional remarks, data quality notes, or null"
    }}
  ]
}}
```

Rules:
- For "columns", only include entries for columns that are explicitly documented.
  Use the exact header names from the CSV schema (listed below) where possible.
- If a piece of information is not present, use null (not an empty string).
- Return ONLY the JSON block, no other text.

## CSV column names (for matching column documentation)
{csv_column_names}

## Documentation to extract from
{raw_content}
"""


class ContextNormalizer:
    """Extracts structured DatasetContext from arbitrary text using AI."""

    def __init__(self, ai_service: AIService) -> None:
        self._ai = ai_service

    def normalize(
        self,
        raw_content: str,
        sources: list[str],
        csv_column_names: list[str],
    ) -> DatasetContext:
        """Extract DatasetContext from raw documentation content via AI.

        Args:
            raw_content: Combined raw content of all context files.
            sources: List of source file paths/URLs.
            csv_column_names: Actual CSV column headers for column matching.

        Returns:
            Populated DatasetContext.
        """
        logger.info("Normalizing context from %d source(s) via AI", len(sources))

        columns_str = "\n".join(f"- {name}" for name in csv_column_names) if csv_column_names else "(not provided)"
        prompt = CONTEXT_NORMALIZATION_PROMPT.format(
            csv_column_names=columns_str,
            raw_content=raw_content[:8000],  # Limit to avoid token overuse
        )

        try:
            response = self._ai.send_message(prompt)
            data = _extract_json(response)
        except Exception as e:
            logger.warning("Context normalization AI call failed: %s", e)
            return DatasetContext(sources=sources, raw_content=raw_content)

        return _build_dataset_context(data, sources, raw_content, csv_column_names)


def _extract_json(response: str) -> dict:
    """Extract JSON from AI response, handling markdown code blocks."""
    # Try to find a JSON code block first
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.DOTALL)
    if match:
        json_str = match.group(1)
    else:
        # Try to find raw JSON object
        match = re.search(r"\{.*\}", response, re.DOTALL)
        if match:
            json_str = match.group(0)
        else:
            logger.warning("No JSON found in normalization response")
            return {}

    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse JSON from normalization response: %s", e)
        return {}


def _build_dataset_context(
    data: dict,
    sources: list[str],
    raw_content: str,
    csv_column_names: list[str],
) -> DatasetContext:
    """Build DatasetContext from parsed JSON data."""
    # Temporal coverage
    temporal = None
    if tc := data.get("temporal_coverage"):
        if tc.get("start") or tc.get("end"):
            temporal = TemporalCoverage(
                start_date=tc.get("start"),
                end_date=tc.get("end"),
            )

    # Spatial coverage
    spatial = None
    if sc := data.get("spatial_coverage"):
        if sc.get("location"):
            spatial = SpatialCoverage(location=sc.get("location"))

    # Column contexts — fuzzy-match to actual CSV headers
    column_contexts: dict[str, ColumnContext] = {}
    csv_headers_lower = {name.lower().strip(): name for name in csv_column_names}

    for col_data in data.get("columns") or []:
        if not isinstance(col_data, dict):
            continue
        doc_name = str(col_data.get("header_name") or "").strip()
        if not doc_name:
            continue

        # Try exact match first, then case-insensitive
        matched_header = None
        if doc_name in csv_column_names:
            matched_header = doc_name
        elif doc_name.lower() in csv_headers_lower:
            matched_header = csv_headers_lower[doc_name.lower()]
        else:
            # Log unmatched column and still store under the documented name
            logger.warning(
                "Column context for '%s' has no matching CSV header (available: %s)",
                doc_name,
                csv_column_names,
            )
            matched_header = doc_name

        column_contexts[matched_header] = ColumnContext(
            header_name=matched_header,
            description=col_data.get("description") or None,
            comment=col_data.get("comment") or None,
        )

    return DatasetContext(
        sources=sources,
        title=data.get("title") or None,
        description=data.get("description") or None,
        publisher=data.get("publisher") or None,
        keywords=[k for k in (data.get("keywords") or []) if k],
        temporal_coverage=temporal,
        spatial_coverage=spatial,
        identifier=data.get("identifier") or None,
        issued=data.get("issued") or None,
        modified=data.get("modified") or None,
        language=data.get("language") or None,
        license=data.get("license") or None,
        access_rights=data.get("access_rights") or None,
        contact_point=data.get("contact_point") or None,
        column_contexts=column_contexts,
        source_format="mixed" if len(sources) > 1 else _guess_format(sources[0]) if sources else None,
        raw_content=raw_content,
    )


def _guess_format(source: str) -> str:
    """Guess a human-readable format label from a file path."""
    suffix = source.rsplit(".", 1)[-1].lower() if "." in source else ""
    format_map = {
        "ttl": "dcat",
        "turtle": "dcat",
        "jsonld": "dcat",
        "rdf": "dcat",
        "md": "markdown",
        "markdown": "markdown",
        "txt": "freetext",
        "json": "json",
        "yaml": "yaml",
        "yml": "yaml",
        "csv": "csv",
    }
    return format_map.get(suffix, "freetext")
