"""Post-processing utilities for generated RML mappings."""

import re

from ogd_to_lod.logging import get_logger

logger = get_logger(__name__)

PLACEHOLDER = "{{FILE_URL}}"


def replace_csv_source_with_placeholder(rml_content: str, csv_filename: str) -> str:
    """Replace CSV source references with a ``{{FILE_URL}}`` placeholder.

    Handles both plain ``rml:source "file.csv"`` (comma-delimited CSV)
    and ``csvw:url "file.csv"`` (non-comma delimiter via CSVW Table).
    A comment recording the original filename is inserted after the
    last ``@prefix`` line.

    Args:
        rml_content: The generated RML Turtle string.
        csv_filename: The CSV filename (or path) to replace.

    Returns:
        The RML string with CSV source references replaced by the
        placeholder.  If no match is found, the original content is
        returned unchanged and a warning is logged.
    """
    escaped = re.escape(csv_filename)

    # Match rml:source "filename" and csvw:url "filename"
    pattern = rf'((?:rml:source|csvw:url)\s+)"({escaped})"'
    result, count = re.subn(pattern, rf'\1"{PLACEHOLDER}"', rml_content)

    if count == 0:
        logger.warning(
            "No CSV source reference found for '%s' — RML left unchanged",
            csv_filename,
        )
        return rml_content

    logger.debug("Replaced %d CSV source reference(s) with %s", count, PLACEHOLDER)

    # Insert a comment after the last @prefix line
    result = _insert_source_comment(result, csv_filename)

    return result


def _insert_source_comment(rml_content: str, csv_filename: str) -> str:
    """Insert an ``# Original CSV source:`` comment after the last ``@prefix`` line.

    Args:
        rml_content: RML Turtle string.
        csv_filename: Original CSV filename to record.

    Returns:
        RML string with the comment inserted.
    """
    comment = f"# Original CSV source: {csv_filename}"

    # Find the last @prefix line
    last_prefix_end = -1
    for match in re.finditer(r"^@prefix\s+.+\.\s*$", rml_content, re.MULTILINE):
        last_prefix_end = match.end()

    if last_prefix_end == -1:
        # No @prefix found — prepend the comment
        return comment + "\n" + rml_content

    return rml_content[:last_prefix_end] + "\n" + comment + rml_content[last_prefix_end:]
