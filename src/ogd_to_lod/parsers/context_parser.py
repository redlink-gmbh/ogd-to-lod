"""Multi-file context parser supporting any input format.

Accepts one or more files (DCAT, freetext, markdown, JSON, CSV-with-meta, etc.),
reads their content, and delegates to ContextNormalizer for AI-based extraction
into a unified DatasetContext.
"""

import json
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

from ogd_to_lod.ai import AIService
from ogd_to_lod.logging import get_logger

from .context_normalizer import ContextNormalizer
from .models import DatasetContext

logger = get_logger(__name__)


class ContextParseError(Exception):
    """Raised when a context file cannot be read."""


# Extensions that indicate a DCAT/RDF file — used for PR inclusion detection
_DCAT_EXTENSIONS = {".ttl", ".turtle", ".jsonld", ".rdf"}


def parse_context(
    sources: list[str],
    csv_column_names: list[str],
    ai_service: AIService,
) -> tuple[DatasetContext, list[dict], str | None, str | None]:
    """Parse one or more context files into a DatasetContext.

    Reads all source files, combines their content, and runs AI normalization
    to produce a unified DatasetContext (including per-column metadata).

    Args:
        sources: List of file paths or URLs to context files.
        csv_column_names: Actual CSV column headers for column matching.
        ai_service: AI service used for normalization.

    Returns:
        Tuple of (DatasetContext, raw_files, dcat_raw_content, dcat_source_format).
        raw_files is a list of dicts with keys "filename", "content", "format"
        for every context file — used for optional PR inclusion.
        dcat_raw_content / dcat_source_format are from the first DCAT-type file
        (kept for backward-compat with existing PR state fields).

    Raises:
        ContextParseError: If a source file cannot be read.
    """
    if not sources:
        return DatasetContext(), [], None, None

    parts: list[str] = []
    raw_files: list[dict] = []
    dcat_raw_content: str | None = None
    dcat_source_format: str | None = None

    for source in sources:
        content, fmt = _read_source(source)
        label = _label(source)
        parts.append(f"=== {label} ===\n{content}")
        raw_files.append({"filename": label, "content": content, "format": fmt})

        # Keep first DCAT file for backward-compat state fields
        if dcat_raw_content is None and _is_dcat_format(source, content, fmt):
            dcat_raw_content = content
            dcat_source_format = fmt
            logger.debug("Detected DCAT file for potential PR inclusion: %s", source)

    combined = "\n\n".join(parts)

    normalizer = ContextNormalizer(ai_service)
    context = normalizer.normalize(combined, sources, csv_column_names)

    return context, raw_files, dcat_raw_content, dcat_source_format


def _label(source: str) -> str:
    """Return a short label for a source (filename or URL)."""
    if _is_url(source):
        return source
    return Path(source).name


def _is_url(source: str) -> bool:
    try:
        r = urlparse(source)
        return r.scheme in ("http", "https")
    except Exception:
        return False


def _read_source(source: str) -> tuple[str, str]:
    """Read content from a file path or URL.

    Returns:
        Tuple of (content, format_hint).

    Raises:
        ContextParseError: If the source cannot be read.
    """
    if _is_url(source):
        return _read_url(source)
    return _read_file(source)


def _read_url(url: str) -> tuple[str, str]:
    try:
        with urlopen(url, timeout=30) as resp:
            content = resp.read().decode("utf-8", errors="replace")
            ct = resp.headers.get("Content-Type", "")
            fmt = _format_from_content_type(ct)
        return content, fmt
    except Exception as e:
        raise ContextParseError(f"Failed to fetch '{url}': {e}") from e


def _read_file(path_str: str) -> tuple[str, str]:
    path = Path(path_str)
    if not path.exists():
        raise ContextParseError(f"File not found: {path_str}")
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise ContextParseError(f"Failed to read '{path_str}': {e}") from e

    suffix = path.suffix.lower()
    fmt_map = {
        ".ttl": "turtle",
        ".turtle": "turtle",
        ".jsonld": "json-ld",
        ".rdf": "xml",
        ".json": "json",
        ".md": "markdown",
        ".markdown": "markdown",
        ".txt": "freetext",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".csv": "csv",
    }
    fmt = fmt_map.get(suffix, "freetext")
    return content, fmt


def _format_from_content_type(ct: str) -> str:
    if "turtle" in ct or "ttl" in ct:
        return "turtle"
    if "json" in ct:
        return "json-ld"
    if "rdf" in ct or "xml" in ct:
        return "xml"
    if "markdown" in ct:
        return "markdown"
    return "freetext"


def _is_dcat_format(source: str, content: str, fmt: str) -> bool:
    """Return True if the source looks like a DCAT/RDF file."""
    if fmt in ("turtle", "xml", "json-ld"):
        return True
    if not _is_url(source):
        suffix = Path(source).suffix.lower()
        if suffix in _DCAT_EXTENSIONS:
            return True
    # Heuristic: JSON file with a DCAT @context
    if fmt == "json":
        try:
            data = json.loads(content)
            ctx = data.get("@context", {})
            ctx_str = json.dumps(ctx) if isinstance(ctx, (dict, list)) else str(ctx)
            if "dcat" in ctx_str or "purl.org/dc" in ctx_str:
                return True
        except Exception:
            pass
    return False
