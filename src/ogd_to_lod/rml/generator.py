"""RML generation using AI service."""

from typing import Any

from ogd_to_lod.ai import AIService
from ogd_to_lod.logging import get_logger
from ogd_to_lod.rml.prompts import RML_CORRECTION_PROMPT, RML_GENERATION_PROMPT

logger = get_logger(__name__)

# Placeholder for CSV source path in generated YARRRML
# This should be replaced with the actual CSV path at deployment time
CSV_SOURCE_PLACEHOLDER = "{{CSV_SOURCE}}"


class RMLGenerationError(Exception):
    """Error during RML generation."""

    pass


class RMLGenerator:
    """Generates YARRRML mappings using AI service.

    Uses the AI service to generate YARRRML (YAML-based RML) configurations
    based on approved mapping proposals and CSV schemas.
    """

    def __init__(self, ai_service: AIService):
        """Initialize the RML generator.

        Args:
            ai_service: AI service instance for generating YARRRML.
        """
        self._ai_service = ai_service

    def generate(
        self,
        mapping_proposal: dict[str, Any],
        csv_schema: dict[str, Any],
        csv_path: str,
        base_uri: str,
    ) -> str:
        """Generate YARRRML mapping from approved proposal.

        Args:
            mapping_proposal: The approved mapping proposal dictionary.
            csv_schema: The CSV schema dictionary with column info.
            csv_path: Path to the CSV file.
            base_uri: Base URI for generated resources.

        Returns:
            Generated YARRRML mapping.

        Raises:
            RMLGenerationError: If generation fails or no valid YARRRML is produced.
        """
        logger.info("Generating YARRRML from mapping proposal")

        # Format the mapping proposal for the prompt
        proposal_text = self._format_proposal(mapping_proposal)
        schema_text = self._format_schema(csv_schema)

        # Build the prompt — use a placeholder for the CSV path so that the
        # generated YARRRML is portable and can be deployed with different CSV sources.
        csv_delimiter = csv_schema.get("delimiter", ",")
        prompt = RML_GENERATION_PROMPT.format(
            base_uri=base_uri,
            csv_path=CSV_SOURCE_PLACEHOLDER,
            mapping_proposal=proposal_text,
            csv_schema=schema_text,
            csv_delimiter=csv_delimiter,
        )

        logger.debug("Sending YARRRML generation prompt to AI")

        try:
            response = self._ai_service.send_message(prompt)
            parsed = AIService.parse_response(response)

            # Extract YAML code blocks
            yaml_blocks = parsed.get_yaml_blocks()

            if not yaml_blocks:
                logger.warning("No YAML code block found in AI response")
                raise RMLGenerationError(
                    "AI did not generate valid YARRRML output. "
                    "Response did not contain a yaml code block."
                )

            rml_content = yaml_blocks[0]
            logger.info(f"Generated YARRRML with {len(rml_content)} characters")

            return rml_content

        except RMLGenerationError:
            raise
        except Exception as e:
            logger.error(f"Failed to generate YARRRML: {e}")
            raise RMLGenerationError(f"Failed to generate YARRRML: {e}") from e

    def regenerate_with_error(self, error_message: str) -> str:
        """Re-generate YARRRML by sending the validation error back to the AI.

        The AI's conversation history already contains the previous YARRRML output,
        so we only need to send the correction prompt with the error.

        Args:
            error_message: The validation error from Tier 1 syntax check.

        Returns:
            Corrected YARRRML mapping.

        Raises:
            RMLGenerationError: If regeneration fails or no valid YARRRML is produced.
        """
        logger.info("Regenerating YARRRML with error context")

        prompt = RML_CORRECTION_PROMPT.format(error_message=error_message)

        logger.debug("Sending YARRRML correction prompt to AI")

        try:
            response = self._ai_service.send_message(prompt)
            parsed = AIService.parse_response(response)

            yaml_blocks = parsed.get_yaml_blocks()

            if not yaml_blocks:
                logger.warning("No YAML code block found in AI correction response")
                raise RMLGenerationError(
                    "AI did not return corrected YARRRML output. "
                    "Response did not contain a yaml code block."
                )

            rml_content = yaml_blocks[0]
            logger.info(f"Regenerated YARRRML with {len(rml_content)} characters")

            return rml_content

        except RMLGenerationError:
            raise
        except Exception as e:
            logger.error(f"Failed to regenerate YARRRML: {e}")
            raise RMLGenerationError(f"Failed to regenerate YARRRML: {e}") from e

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
        delimiter = schema.get("delimiter", ",")
        lines.append(f"Delimiter: {repr(delimiter)}")
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
    """Convenience function to generate YARRRML mapping.

    Args:
        ai_service: AI service instance.
        mapping_proposal: The approved mapping proposal dictionary.
        csv_schema: The CSV schema dictionary with column info.
        csv_path: Path to the CSV file.
        base_uri: Base URI for generated resources.

    Returns:
        Generated YARRRML mapping.

    Raises:
        RMLGenerationError: If generation fails.
    """
    generator = RMLGenerator(ai_service)
    return generator.generate(mapping_proposal, csv_schema, csv_path, base_uri)
