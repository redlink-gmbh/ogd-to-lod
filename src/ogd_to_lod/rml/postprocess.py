"""Post-processing utilities for generated RML mappings."""

import re

from ogd_to_lod.logging import get_logger

logger = get_logger(__name__)

PLACEHOLDER = "{{FILE_URL}}"


def replace_csv_source_with_placeholder(
    rml_content: str,
    csv_filename: str,
    source_comment: str | None = None,
) -> str:
    """Replace CSV source references with a ``{{FILE_URL}}`` placeholder.

    Handles both plain ``rml:source "file.csv"`` (comma-delimited CSV)
    and ``csvw:url "file.csv"`` (non-comma delimiter via CSVW Table).
    A comment is inserted after the last ``@prefix`` line to record
    where the original data can be found.

    Args:
        rml_content: The generated RML Turtle string.
        csv_filename: The CSV filename (or path) to replace.
        source_comment: Free-form comment inserted after the prefix
            block (e.g. a download URI, dataset page URL, or plain
            description).  Defaults to ``Original source: <csv_filename>``
            when *None*.

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
    comment_text = source_comment if source_comment is not None else f"Original source: {csv_filename}"
    result = _insert_comment_after_prefixes(result, comment_text)

    return result


def _insert_comment_after_prefixes(rml_content: str, comment_text: str) -> str:
    """Insert a ``#``-prefixed comment after the last ``@prefix`` line.

    Args:
        rml_content: RML Turtle string.
        comment_text: Free-form text to insert as a Turtle comment.

    Returns:
        RML string with the comment inserted.
    """
    comment = f"# {comment_text}"

    # Find the last @prefix line
    last_prefix_end = -1
    for match in re.finditer(r"^@prefix\s+.+\.\s*$", rml_content, re.MULTILINE):
        last_prefix_end = match.end()

    if last_prefix_end == -1:
        # No @prefix found — prepend the comment
        return comment + "\n" + rml_content

    return rml_content[:last_prefix_end] + "\n" + comment + rml_content[last_prefix_end:]
