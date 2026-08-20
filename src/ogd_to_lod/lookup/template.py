"""URI template proposal (LLM) and local verification.

The LLM only *proposes* a YARRRML template per column
`verify_template` re-derives every matched value's URI from the
proposed template in plain Python and only accepts the template if it
reproduces `value_to_term` exactly.
"""

from __future__ import annotations

import yaml

from ogd_to_lod.ai.service import AIService
from ogd_to_lod.logging import get_logger
from ogd_to_lod.lookup.models import ColumnReuse, build_enriched_table

logger = get_logger(__name__)

_TEMPLATE_SYSTEM_HINT = """\
You are proposing YARRRML URI templates for CSV code columns.

For each column below you are given a small sample of raw CSV rows plus the \
already-known mapping from raw value to its existing term URI (a "<column>_uri" \
column; "(no match)" means no term was found for that row's value).

Find the literal prefix (and suffix, if any) that the known URIs share and \
express it as a YARRRML template of the form `<prefix>$(<column>)<suffix>~iri`, \
where `$(<column>)` is replaced by the raw CSV value for that row. The \
template must contain exactly one `$(<column>)` placeholder and must end \
with `~iri`. Do not guess a template for a column if the sample gives no \
clear, consistent pattern — omit it instead.

Answer ONLY with a single fenced YAML block of this exact shape, no prose:

```yaml
templates:
  - column: <column name>
    template: "<prefix>$(<column name>)<suffix>~iri"
```

Data:

{tables}
"""


def _format_enriched_table(rows: list[dict[str, str]]) -> str:
    """Render enriched rows as a markdown table for the prompt."""
    if not rows:
        return "*(no sample rows available)*"
    headers = list(rows[0].keys())
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
        ]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(h, "")) for h in headers) + " |")
    return "\n".join(lines)


def _build_prompt(
        columns: list[ColumnReuse],
        sample_rows_by_column: dict[str, list[dict[str, str]]],
) -> str:
    """Build the single batched prompt covering every accepted column."""
    sections = []
    for col in columns:
        rows = sample_rows_by_column.get(col.column, [])
        enriched = build_enriched_table(col.column, rows, col.value_to_term)
        sections.append(f"### Column `{col.column}`\n\n{_format_enriched_table(enriched)}")
    return _TEMPLATE_SYSTEM_HINT.format(tables="\n\n".join(sections))


def propose_templates(
        ai_service: AIService,
        columns: list[ColumnReuse],
        sample_rows_by_column: dict[str, list[dict[str, str]]],
) -> dict[str, str]:
    """Ask the LLM for a template per column, in one isolated batched call.

    Uses `AIService.ask_once` so this Q&A never enters the conversation
    history consumed by GENERATE.

    Args:
        ai_service: The AI service to call.
        columns: Accepted columns from the term-matching pass.
        sample_rows_by_column: column -> CSV sample rows (`csv_schema["sample_rows"]`).

    Returns:
        column -> proposed template, for columns the LLM answered for with a
        well-formed YAML entry. Missing/malformed entries are simply absent;
        `propose_and_verify_templates` treats an absent proposal as "no
        template", not as an error.
    """
    if not columns:
        return {}

    prompt = _build_prompt(columns, sample_rows_by_column)
    response = ai_service.ask_once(prompt)
    parsed = ai_service.parse_response(response)

    proposals: dict[str, str] = {}
    for block in parsed.get_yaml_blocks():
        try:
            data = yaml.safe_load(block)
        except yaml.YAMLError as e:
            logger.warning(f"Could not parse template proposal YAML block: {e}")
            continue

        for entry in (data or {}).get("templates") or []:
            column = entry.get("column")
            template = entry.get("template")
            if not column or not template:
                logger.warning(f"Skipping malformed template entry: {entry!r}")
                continue
            proposals[column] = template

    return proposals


def verify_template(column: str, template: str, value_to_term: dict[str, str]) -> bool:
    """Locally verify a proposed template against known term matches.

    Accepted only when all hold:
      1. exactly one `$(<column>)` placeholder, for its own column;
      2. the template ends with `~iri`;
      3. substituting each matched raw value reproduces that value's term URI
         for 100% of `value_to_term`.

    Args:
        column: The column the template is for.
        template: The LLM-proposed YARRRML template.
        value_to_term: Raw value -> term URI, from the term-matching pass.

    Returns:
        True if the template reproduces every known mapping exactly.
    """
    placeholder = f"$({column})"

    if template.count("$(") != 1 or placeholder not in template:
        logger.info(
            f"Column {column!r}: template rejected, wrong/missing placeholder: {template!r}"
        )
        return False

    if not template.endswith("~iri"):
        logger.info(f"Column {column!r}: template rejected, missing ~iri suffix: {template!r}")
        return False

    if not value_to_term:
        logger.info(f"Column {column!r}: template rejected, no known value_to_term to verify against.")
        return False

    body = template[: -len("~iri")]
    for raw_value, expected_uri in value_to_term.items():
        candidate = body.replace(placeholder, raw_value)
        if candidate != expected_uri:
            logger.info(
                f"Column {column!r}: template rejected, mismatch for value {raw_value!r}: "
                f"expected {expected_uri!r}, got {candidate!r}"
            )
            return False

    return True


def propose_and_verify_templates(
        ai_service: AIService,
        columns: list[ColumnReuse],
        sample_rows_by_column: dict[str, list[dict[str, str]]],
) -> None:
    """Propose templates via one batched LLM subagent call, verify locally,
    and mutate each `ColumnReuse` in place.

    Per D2: a column whose template fails verification keeps its `property`
    but loses code reuse (`uri_template = None`, `template_verified = False`).
    This function never touches `column.property`.

    Args:
        ai_service: The AI service to call (isolated subagent, via ask_once).
        columns: Accepted columns from the term-matching pass, mutated in place.
        sample_rows_by_column: column -> CSV sample rows.
    """
    proposals = propose_templates(ai_service, columns, sample_rows_by_column)

    for col in columns:
        template = proposals.get(col.column)

        if template is None:
            logger.info(f"Column {col.column!r}: no template proposed by LLM.")
            col.uri_template = None
            col.template_verified = False
            continue

        if verify_template(col.column, template, col.value_to_term):
            col.uri_template = template
            col.template_verified = True
        else:
            col.uri_template = None
            col.template_verified = False