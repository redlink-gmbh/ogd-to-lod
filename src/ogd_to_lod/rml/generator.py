"""RML generation using AI service."""

from typing import Any

from ogd_to_lod.ai import AIService
from ogd_to_lod.logging import get_logger
from ogd_to_lod.rml.prompts import RML_GENERATION_PROMPT

logger = get_logger(__name__)


class RMLGenerationError(Exception):
    """Error during RML generation."""

    pass


class RMLGenerator:
    """Generates RML mappings using AI service.

    Uses the AI service to generate RML (RDF Mapping Language) configurations
    in Turtle format based on approved mapping proposals and CSV schemas.
    """

    def __init__(self, ai_service: AIService):
        """Initialize the RML generator.

        Args:
            ai_service: AI service instance for generating RML.
        """
        self._ai_service = ai_service

    def generate(
        self,
        mapping_proposal: dict[str, Any],
        csv_schema: dict[str, Any],
        csv_path: str,
        base_uri: str,
    ) -> str:
        """Generate RML mapping from approved proposal.

        Args:
            mapping_proposal: The approved mapping proposal dictionary.
            csv_schema: The CSV schema dictionary with column info.
            csv_path: Path to the CSV file.
            base_uri: Base URI for generated resources.

        Returns:
            Generated RML in Turtle format.

        Raises:
            RMLGenerationError: If generation fails or no valid RML is produced.
        """
        logger.info("Generating RML from mapping proposal")

        # Format the mapping proposal for the prompt
        proposal_text = self._format_proposal(mapping_proposal)
        schema_text = self._format_schema(csv_schema)

        # Build the prompt
        prompt = RML_GENERATION_PROMPT.format(
            base_uri=base_uri,
            csv_path=csv_path,
            mapping_proposal=proposal_text,
            csv_schema=schema_text,
        )

        logger.debug("Sending RML generation prompt to AI")

        try:
            response = self._ai_service.send_message(prompt)
            parsed = AIService.parse_response(response)

            # Extract Turtle code blocks
            turtle_blocks = parsed.get_turtle_blocks()

            if not turtle_blocks:
                logger.warning("No Turtle code block found in AI response")
                raise RMLGenerationError(
                    "AI did not generate valid RML Turtle output. "
                    "Response did not contain a turtle code block."
                )

            rml_content = turtle_blocks[0]
            logger.info(f"Generated RML with {len(rml_content)} characters")

            return rml_content

        except RMLGenerationError:
            raise
        except Exception as e:
            logger.error(f"Failed to generate RML: {e}")
            raise RMLGenerationError(f"Failed to generate RML: {e}") from e

    def _format_proposal(self, proposal: dict[str, Any]) -> str:
        """Format mapping proposal for AI prompt.

        Args:
            proposal: Mapping proposal dictionary.

        Returns:
            Formatted string representation.
        """
        lines = []

        # Format dimensions
        dimensions = proposal.get("dimensions", [])
        if dimensions:
            lines.append("### Dimensions:")
            for dim in dimensions:
                dim_type = dim.get("type", "categorical")
                granularity = dim.get("granularity", "")
                hierarchy = dim.get("hierarchy", "")

                line = f"- {dim['column']}: {dim_type}"
                if granularity:
                    line += f" (granularity: {granularity})"
                if hierarchy:
                    line += f" (hierarchy: {hierarchy})"
                lines.append(line)

        # Format measures
        measures = proposal.get("measures", [])
        if measures:
            lines.append("")
            lines.append("### Measures:")
            for measure in measures:
                unit = measure.get("unit", "")
                aggregation = measure.get("aggregation", "")

                line = f"- {measure['column']}"
                if unit:
                    line += f" (unit: {unit})"
                if aggregation:
                    line += f" (aggregation: {aggregation})"
                lines.append(line)

        return "\n".join(lines)

    def _format_schema(self, schema: dict[str, Any]) -> str:
        """Format CSV schema for AI prompt.

        Args:
            schema: CSV schema dictionary.

        Returns:
            Formatted string representation.
        """
        lines = []

        lines.append(f"Source: {schema.get('source', 'Unknown')}")
        lines.append(f"Total rows: {schema.get('total_rows', 0)}")
        lines.append("")
        lines.append("### Columns:")

        for col in schema.get("columns", []):
            samples = col.get("samples", [])
            samples_str = ", ".join(f'"{s}"' for s in samples[:3])
            lines.append(f"- {col['name']} ({col['type']}): [{samples_str}]")

        return "\n".join(lines)


def generate_rml(
    ai_service: AIService,
    mapping_proposal: dict[str, Any],
    csv_schema: dict[str, Any],
    csv_path: str,
    base_uri: str,
) -> str:
    """Convenience function to generate RML mapping.

    Args:
        ai_service: AI service instance.
        mapping_proposal: The approved mapping proposal dictionary.
        csv_schema: The CSV schema dictionary with column info.
        csv_path: Path to the CSV file.
        base_uri: Base URI for generated resources.

    Returns:
        Generated RML in Turtle format.

    Raises:
        RMLGenerationError: If generation fails.
    """
    generator = RMLGenerator(ai_service)
    return generator.generate(mapping_proposal, csv_schema, csv_path, base_uri)
