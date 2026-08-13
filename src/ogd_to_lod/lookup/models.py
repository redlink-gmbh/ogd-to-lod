"""Data structures for SPARQL-based vocabulary reuse context.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class MatchedProperty:
    """An existing cube.link property found in the SPARQL endpoint."""

    existing_uri: str
    label: str
    matched_column: str
    usage_count: int = 0
    source: str = "term"


@dataclass
class ColumnReuse:
    """Per-column reuse result: term set, coverage, template, property."""

    column: str
    term_set_uri: str
    coverage: float              # matched rows / total rows — exact
    distinct_coverage: float     # matched distinct values / distinct values
    uri_template: str | None           # verified YARRRML template; None if unrepresentable
    template_verified: bool = False
    value_to_term: dict[str, str] = field(default_factory=dict)
    unmatched_values: list[str] = field(default_factory=list)
    truncated: bool = False      # distinct-value cap hit → coverage is a lower bound
    property: MatchedProperty | None = None

def build_enriched_table(
        column: str,
        sample_rows: list[dict[str, str]],
        value_to_term: dict[str, str],
        limit: int = 10,
) -> list[dict[str, str]]:
    """Attach a `<column>_uri` column to sample rows via known term matches.

    Args:
        column: Name of the CSV column being enriched.
        sample_rows: Rows from `state.csv_schema["sample_rows"]`.
        value_to_term: Raw value -> term URI, from the term-matching pass.
        limit: Max number of rows to include.

    Returns:
        Up to `limit` rows, each with an added `<column>_uri` field
        (`"(no match)"` where the raw value has no known term).
    """
    uri_column = f"{column}_uri"
    enriched: list[dict[str, str]] = []
    for row in sample_rows[:limit]:
        raw_value = row.get(column, "")
        enriched_row = dict(row)
        enriched_row[uri_column] = value_to_term.get(raw_value, "(no match)")
        enriched.append(enriched_row)
    return enriched


def _format_enriched_table(rows: list[dict[str, str]]) -> str:
    """Render enriched rows as a markdown table."""
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


@dataclass
class ReuseContext:
    """Vocabulary reuse context built from SPARQL lookups.

    Carries existing property URIs and per-column DefinedTermSet reuse
    (term set, verified URI template) that should be used in the YARRRML
    mapping instead of generating fresh ex-property:/ex-code: URIs.
    """

    columns: list[ColumnReuse] = field(default_factory=list)
    properties: list[MatchedProperty] = field(default_factory=list)  # flat, all sources

    def has_matches(self) -> bool:
        """Return True if any reusable resources were found."""
        return bool(self.properties or self.columns)

    def enriched_table(
            self, sample_rows: list[dict[str, str]]
    ) -> dict[str, list[dict[str, str]]]:
        """Build the enriched sample table for every accepted column.

        Args:
            sample_rows: Rows from `state.csv_schema["sample_rows"]`.

        Returns:
            column name -> enriched rows.
        """
        return {
            col.column: build_enriched_table(col.column, sample_rows, col.value_to_term)
            for col in self.columns
        }

    def drop_columns(self, names: list[str] | set[str]) -> "ReuseContext":
        """Return a new context with the named columns' term/template reuse removed.

        Used by the per-column confirmation gate when the user excludes
        specific columns by name. Matched properties are left untouched —
        excluding a column's code-term reuse is not the same as rejecting
        its property reuse.

        Args:
            names: Column names to drop.

        Returns:
            A new `ReuseContext` without the named columns.
        """
        names_set = set(names)
        return ReuseContext(
            columns=[c for c in self.columns if c.column not in names_set],
            properties=list(self.properties),
        )

    def to_prompt_text(
            self,
            sample_rows: list[dict[str, str]] | None = None,
    ) -> str:
        """Format reuse context for injection into AI prompts.

        Args:
            sample_rows: Optional CSV sample rows; when given, an enriched
                sample table is appended per accepted column.
        """
        if not self.has_matches():
            return ""

        lines = ["## Existing Vocabulary (reuse from SPARQL endpoint)"]
        lines.append(
            "The following existing resources were found and MUST be reused in the mapping:"
        )

        if self.properties:
            lines.append("")
            lines.append("### Properties")
            lines.append(
                "Use these existing URIs as predicates instead of generating ex-property: names:"
            )
            for p in self.properties:
                lines.append(
                    f"- Column `{p.matched_column}` → use `<{p.existing_uri}>` "
                    f"(label: {p.label})"
                )

        if self.columns:
            lines.append("")
            lines.append("### DefinedTermSets")
            lines.append(
                "Use these URI templates for code values instead of ex-code:$(col)~iri:"
            )
            for c in self.columns:
                if c.template_verified and c.uri_template:
                    template_line = f"use `{c.uri_template}`"
                else:
                    template_line = (
                        "term set matched but no representable template; "
                        "generate fresh ex-code: URIs for this column"
                    )
                lines.append(
                    f"- Column `{c.column}` → {template_line} "
                    f"(DefinedTermSet: <{c.term_set_uri}>, "
                    f"coverage: {c.coverage:.0%})"
                )

                if sample_rows is not None:
                    enriched = build_enriched_table(c.column, sample_rows, c.value_to_term)
                    lines.append("")
                    lines.append(_format_enriched_table(enriched))
                    lines.append("")
            lines.append(
                "Do NOT generate a separate mapping for schema:DefinedTerm resources "
                "for columns listed above — the DefinedTerms already exist."
            )

        return "\n".join(lines)

    def to_display_text(self) -> str:
        """Format reuse context for display to the user."""
        if not self.has_matches():
            return ""

        lines = []

        if self.properties:
            lines.append(f"**{len(self.properties)} existing property/properties:**")
            for p in self.properties:
                lines.append(f"  - `{p.matched_column}` → `<{p.existing_uri}>`")

        if self.columns:
            lines.append(f"**{len(self.columns)} existing DefinedTermSet(s):**")
            for c in self.columns:
                status = "template verified" if c.template_verified else "no verified template"
                lines.append(
                    f"  - `{c.column}` → `<{c.term_set_uri}>` "
                    f"({c.coverage:.0%} row coverage, {status})"
                )

        return "\n".join(lines)