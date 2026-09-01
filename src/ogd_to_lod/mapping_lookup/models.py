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
class ReuseContext:
    template_matches: list[TemplateMatch] = field(default_factory=list)

    def build_llm_prompt(self) -> str:
        """Context section for the LLM prompt: for each candidate template, the
        cube structure, all known properties, and which CSV column was matched
        to which property"""
        if not self.template_matches:
            return ""

        sections = []
        for tm in self.template_matches:
            template = tm.template
            lines = [
                f"Mapping template (dataset {template.dsnr}:",
                f"  Cube shape: {template.cube_shape}",
                "  Known properties:",
            ]
            for prop in template.properties:
                lines.append(
                    f"    - {prop.property_uri} "
                    f"(label={prop.label!r}, role={prop.role}, datatype={prop.datatype})"
                )

            if tm.column_matches:
                lines.append("  Matches against columns in the current CSV schema:")
                for match in tm.column_matches:
                    lines.append(
                        f"    - Column '{match.column_name}' -> {match.property.property_uri} "
                        f"(label={match.property.label!r}, role={match.property.role}, "
                        f"score={match.score:.2f})"
                    )
            else:
                lines.append("No fitting mapping template was found.")

            sections.append("\n".join(lines))

        return "\n\n".join(sections)