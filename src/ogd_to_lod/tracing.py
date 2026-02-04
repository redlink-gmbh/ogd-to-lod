"""LangSmith tracing configuration for LLM call observability."""

import os

from ogd_to_lod.logging import get_logger, get_session_id

logger = get_logger(__name__)

EU_ENDPOINT = "https://eu.api.smith.langchain.com"


def configure_tracing() -> bool:
    """Configure LangSmith tracing if an API key is available.

    When LANGSMITH_API_KEY is set in the environment:
    - Enables tracing by setting LANGSMITH_TRACING=true
    - Defaults LANGSMITH_ENDPOINT to the EU endpoint unless already set

    Returns:
        True if tracing is enabled, False otherwise.
    """
    api_key = os.environ.get("LANGSMITH_API_KEY")

    if not api_key:
        logger.info("LangSmith tracing disabled (LANGSMITH_API_KEY not set)")
        return False

    os.environ["LANGSMITH_TRACING"] = "true"

    if not os.environ.get("LANGSMITH_ENDPOINT"):
        os.environ["LANGSMITH_ENDPOINT"] = EU_ENDPOINT

    endpoint = os.environ["LANGSMITH_ENDPOINT"]
    logger.info(f"LangSmith tracing enabled (endpoint: {endpoint})")
    return True


def get_trace_metadata(
    *,
    csv_path: str | None = None,
    base_uri: str | None = None,
    flow_state: str | None = None,
) -> dict[str, str]:
    """Build metadata dict for attaching to LangSmith runs.

    Args:
        csv_path: Path to the CSV file being processed.
        base_uri: Base URI for generated resources.
        flow_state: Current flow state name.

    Returns:
        Dictionary of metadata key-value pairs.
    """
    metadata: dict[str, str] = {}

    session_id = get_session_id()
    if session_id:
        metadata["session_id"] = session_id

    if csv_path:
        metadata["csv_path"] = csv_path

    if base_uri:
        metadata["base_uri"] = base_uri

    if flow_state:
        metadata["flow_state"] = flow_state

    return metadata
