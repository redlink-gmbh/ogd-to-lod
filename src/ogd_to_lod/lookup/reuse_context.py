"""Data structures for SPARQL-based vocabulary reuse context."""

from dataclasses import dataclass, field


@dataclass
class MatchedProperty:
    """An existing cube.link property found in the SPARQL endpoint."""

    existing_uri: str
    label: str
    matched_column: str  # CSV column this was matched to


@dataclass
class MatchedDefinedTermSet:
    """An existing schema:DefinedTermSet found in the SPARQL endpoint."""

    term_set_uri: str
    uri_template: str  # e.g. "https://ld.stadt-zuerich.ch/statistics/code/$(col)~iri"
    matched_column: str
    coverage: float  # fraction of CSV sample values found (0.0–1.0)
    sample_matches: list[str] = field(default_factory=list)  # matched values from CSV sample


@dataclass
class ReuseContext:
    """Vocabulary reuse context built from SPARQL lookups.

    Carries existing property URIs and DefinedTermSet templates that should
    be used in the YARRRML mapping instead of generating fresh ex-property:/ex-code: URIs.
    """

    properties: list[MatchedProperty] = field(default_factory=list)
    defined_term_sets: list[MatchedDefinedTermSet] = field(default_factory=list)

    def has_matches(self) -> bool:
        """Return True if any reusable resources were found."""
        return bool(self.properties or self.defined_term_sets)

    def to_prompt_text(self) -> str:
        """Format reuse context for injection into AI prompts."""
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

        if self.defined_term_sets:
            lines.append("")
            lines.append("### DefinedTermSets")
            lines.append(
                "Use these URI templates for code values instead of ex-code:$(col)~iri:"
            )
            for d in self.defined_term_sets:
                lines.append(
                    f"- Column `{d.matched_column}` → use `{d.uri_template}` "
                    f"(DefinedTermSet: <{d.term_set_uri}>, "
                    f"coverage: {d.coverage:.0%})"
                )
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

        if self.defined_term_sets:
            lines.append(f"**{len(self.defined_term_sets)} existing DefinedTermSet(s):**")
            for d in self.defined_term_sets:
                lines.append(
                    f"  - `{d.matched_column}` → `<{d.term_set_uri}>` "
                    f"({d.coverage:.0%} of sample values matched)"
                )

        return "\n".join(lines)
