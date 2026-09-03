from dataclasses import dataclass, field


@dataclass
class Property:
    property_uri: str
    label: str | None          # rdfs:label
    role: str                  # "dimension" | "measure" | "attribute"
    datatype: str | None       # xsd:decimal, xsd:string,

@dataclass
class MappingTemplate:
    branch: str
    dsnr: str  # dataset number
    properties: list[Property]
    cube_shape: str  # single-measure, multi-measure


@dataclass
class ColumnMatch:
    """A CSV column that was matched to a property of a MappingTemplate."""
    column_name: str
    property: Property
    score: float


@dataclass
class TemplateMatch:
    """A MappingTemplate candidate including its column matches and overall score."""
    template: MappingTemplate
    column_matches: list[ColumnMatch]
    score: float


@dataclass
class ReuseMappingTemplate:
    template_matches: list[TemplateMatch] = field(default_factory=list)

    def build_llm_prompt(self) -> str:
        """Context section for the LLM prompt: for each candidate template, the
        cube shape, which CSV columns resemble which known property,
        and which known properties had no matching column"""
        if not self.template_matches:
            return ""

        intro = (
            "The following are structurally similar mapping templates from datasets "
            "mapped previously. They are REFERENCE EXAMPLES for structural patterns "
            "(cube shape, dimension/measure split, context-derived dimensions) - they "
            "are NOT the mapping for the current dataset. Their property URIs belong "
            "to the referenced dataset; do not reuse them literally for the current "
            "dataset unless it is in fact the same dataset. Column matches below are "
            "based on naive name/type similarity and may be wrong - verify semantically."
        )

        sections = [intro]
        for tm in self.template_matches:
            template = tm.template
            matched_uris = {match.property.property_uri for match in tm.column_matches}
            unmatched_properties = [
                prop for prop in template.properties if prop.property_uri not in matched_uris
            ]

            lines = [
                f"Reference template (dataset {template.dsnr}, branch {template.branch}):",
                f"  Cube shape: {template.cube_shape}",
            ]

            if tm.column_matches:
                lines.append("  Columns in the current CSV schema resembling a known property:")
                for match in tm.column_matches:
                    lines.append(
                        f"    - Column '{match.column_name}' resembles "
                        f"{match.property.property_uri} "
                        f"(label={match.property.label!r}, role={match.property.role}, "
                        f"datatype={match.property.datatype}, "
                    )
            else:
                lines.append("  No column in the current CSV schema resembled a known property.")

            if unmatched_properties:
                lines.append(
                    "  Known properties from the reference template with no matching column "
                )
                for prop in unmatched_properties:
                    lines.append(
                        f"    - {prop.property_uri} "
                        f"(label={prop.label!r}, role={prop.role}, datatype={prop.datatype})"
                    )

            sections.append("\n".join(lines))

        return "\n\n".join(sections)

    def build_humanreadable_discription(self) -> str:
        """Context section for the human-readable discription: for each
        candidate, a short plain-language summary for the user"""
        if not self.template_matches:
            return "No similar previous mappings were found for this dataset."

        paragraphs = []
        for tm in self.template_matches:
            template = tm.template
            shape_text = (
                "multiple measures (multi-measure cube)"
                if template.cube_shape == "multi-measure"
                else "a single measure (single-measure cube)"
            )

            lines = [
                f"Found a similar previous mapping for dataset '{template.dsnr}', "
                f"structured with {shape_text}."
            ]

            if tm.column_matches:
                matched_names = ", ".join(m.column_name for m in tm.column_matches)
                lines.append(
                    f"{len(tm.column_matches)} of your column(s) look similar to "
                    f"properties already used there: {matched_names}."
                )
            else:
                lines.append("None of your columns closely resembled its known properties.")

            matched_uris = {m.property.property_uri for m in tm.column_matches}
            unmatched = [p for p in template.properties if p.property_uri not in matched_uris]
            if unmatched:
                unmatched_names = ", ".join(
                    p.label if p.label else p.property_uri.rstrip("/").rsplit("/", 1)[-1]
                    for p in unmatched
                )
                plural = "y" if len(unmatched) == 1 else "ies"
                lines.append(
                    f"{len(unmatched)} propert{plural} from that mapping had no matching "
                    f"column ({unmatched_names})"
                )

            paragraphs.append("\n".join(lines))

        return "\n\n".join(paragraphs)