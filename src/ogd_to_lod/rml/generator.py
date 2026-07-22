"""RML generation using AI service."""

import re
from typing import Any

from ogd_to_lod._slug import slugify
from ogd_to_lod.ai import AIService
from ogd_to_lod.lookup import ReuseContext
from ogd_to_lod.logging import get_logger
from ogd_to_lod.rml.prompts import RML_CORRECTION_PROMPT, RML_GENERATION_PROMPT

logger = get_logger(__name__)

# Placeholder for CSV source path in generated YARRRML
# This should be replaced with the actual CSV path at deployment time
CSV_SOURCE_PLACEHOLDER = "{CSV_SOURCE}"

# Matches a bare <http(s)://…> IRI used as an object inside a po: shorthand
# flow sequence: `[predicate, <iri>]` or `[predicate, <iri>, …]`.
# Subject lines like `s: <iri>` are left untouched because they are not
# inside a `[ … ]` flow sequence.
_BARE_IRI_OBJECT_RE = re.compile(
    r"(\[\s*[^,\[\]]+,\s*)<(https?://[^>\s]+)>(\s*[,\]])"
)

# Matches an angle-bracket IRI used as a subject, in either form:
#     s: <https://example.org/foo>      (bare)
#     s: "<https://example.org/foo>"    (quoted)
# Neither form is valid YARRRML for a constant IRI subject — yarrrml-parser
# treats the whole `<…>` string as a template and RMLMapper URL-encodes the
# `<` and `>` into the IRI's path, producing `<%3Chttps://…%3E>`. The
# universally-working alternative is the YARRRML long form:
#     s:
#       value: https://example.org/foo
#       type: iri
_IRI_SUBJECT_RE = re.compile(
    r'^(?P<indent>[ \t]+)s:[ \t]*"?<(?P<iri>https?://[^>\s"]+)>"?[ \t]*$',
    re.MULTILINE,
)


def _fix_bare_iri_objects(yarrrml: str) -> str:
    """Rewrite bare <https://…> IRIs in po: shorthand objects.

    The AI sometimes emits a bare angle-bracketed IRI as the object of a
    ``po:`` shorthand list, e.g.::

        - ["cube:dataSet", <https://example.org/observation-set>]

    yarrrml-parser treats that as a plain string and RMLMapper URL-encodes
    the angle brackets into the IRI, producing ``<%3Chttps://…%3E>``. The
    correct form is a quoted IRI string with the ``~iri`` suffix::

        - ["cube:dataSet", "https://example.org/observation-set~iri"]

    Returns the YARRRML with every such occurrence rewritten. Logs a
    warning per rewrite so the underlying prompt drift stays visible.
    """
    matches = list(_BARE_IRI_OBJECT_RE.finditer(yarrrml))
    if not matches:
        return yarrrml
    for m in matches:
        logger.warning(
            "Rewriting bare angle-bracket IRI object in YARRRML: <%s> → "
            '"%s~iri"',
            m.group(2),
            m.group(2),
        )
    return _BARE_IRI_OBJECT_RE.sub(r'\1"\2~iri"\3', yarrrml)


def _fix_iri_subject(yarrrml: str) -> str:
    """Rewrite `s: <iri>` / `s: "<iri>"` to YARRRML's long form.

    Angle-bracket IRIs are not valid in YARRRML ``s:`` lines — both the
    quoted and bare forms get treated as plain templates and end up
    URL-encoded by RMLMapper. The long form is the documented way to
    assert a constant IRI subject::

        s:
          value: https://example.org/foo
          type: iri

    The rewrite preserves the line's indentation and inserts two extra
    spaces for the nested keys. Logs a warning per rewrite so the
    underlying prompt drift stays visible.
    """
    matches = list(_IRI_SUBJECT_RE.finditer(yarrrml))
    if not matches:
        return yarrrml

    def _replace(m: re.Match[str]) -> str:
        indent = m.group("indent")
        iri = m.group("iri")
        logger.warning(
            "Rewriting angle-bracket IRI subject in YARRRML to long form: "
            "s: <%s> → s: { value: %s, type: iri }",
            iri,
            iri,
        )
        return (
            f"{indent}s:\n"
            f"{indent}  value: {iri}\n"
            f"{indent}  type: iri"
        )

    return _IRI_SUBJECT_RE.sub(_replace, yarrrml)


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
        dataset_context: dict[str, Any] | None = None,
        reuse_context: ReuseContext | None = None,
        output_folder: str | None = None,
    ) -> str:
        """Generate YARRRML mapping from approved proposal.

        Args:
            mapping_proposal: The approved mapping proposal dictionary.
            csv_schema: The CSV schema dictionary with column info.
            csv_path: Path to the CSV file.
            base_uri: Base URI for generated resources.
            dataset_context: Optional normalized dataset context with column descriptions.
            reuse_context: Optional SPARQL-based reuse context with existing URIs.
            output_folder: Optional dataset slug (typically the CLI
                ``--output-folder``). When provided, all generated resources
                (``ex:``, ``ex-obs:``, ``ex-property:``, ``ex-code:``, and the
                ObservationSet IRI) live under ``<base_uri><slug>/`` so
                multiple datasets sharing the same base URI stay isolated.

        Returns:
            Generated YARRRML mapping.

        Raises:
            RMLGenerationError: If generation fails or no valid YARRRML is produced.
        """
        logger.info("Generating YARRRML from mapping proposal")

        # Format the mapping proposal for the prompt
        proposal_text = self._format_proposal(mapping_proposal)
        schema_text = self._format_schema(csv_schema)
        column_desc_text = self._format_column_descriptions(dataset_context)

        # Format reuse context (empty string when no matches)
        reuse_text = ""
        if reuse_context and reuse_context.has_matches():
            reuse_text = "\n" + reuse_context.to_prompt_text() + "\n"
            logger.info(
                "Injecting reuse context: %d properties, %d DefinedTermSets",
                len(reuse_context.properties),
                len(reuse_context.defined_term_sets),
            )

        # Two URI scopes:
        # - dataset_uri (with slug): cube, observation-set and observations
        #   (ex:*, ex-obs:*) are dataset-specific and must stay isolated when
        #   multiple datasets share the same base URI.
        # - base (slug-free): properties and code/DefinedTerm values
        #   (ex-property:*, ex-code:*) are shared concepts and live under the
        #   bare base URI so they can be reused across datasets.
        base_with_slash = base_uri if base_uri.endswith("/") else base_uri + "/"
        slug = slugify(output_folder) if output_folder else ""
        dataset_uri = base_with_slash + slug + "/" if slug else base_uri

        # Build the prompt — use a placeholder for the CSV path so that the
        # generated YARRRML is portable and can be deployed with different CSV sources.
        prompt = RML_GENERATION_PROMPT.format(
            base_uri=base_with_slash,
            dataset_uri=dataset_uri,
            mapping_proposal=proposal_text,
            csv_schema=schema_text,
            column_descriptions=column_desc_text,
            reuse_context=reuse_text,
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

            rml_content = _fix_iri_subject(
                _fix_bare_iri_objects(yaml_blocks[0])
            )
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

            rml_content = _fix_iri_subject(
                _fix_bare_iri_objects(yaml_blocks[0])
            )
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

    def _format_column_descriptions(self, dataset_context: dict[str, Any] | None) -> str:
        """Format column descriptions from dataset context for the AI prompt.

        Args:
            dataset_context: Serialized DatasetContext dict or None.

        Returns:
            Formatted string, or a note that no descriptions are available.
        """
        if not dataset_context:
            return "(no column descriptions provided)"

        column_contexts = dataset_context.get("column_contexts") or {}
        if not column_contexts:
            return "(no column descriptions provided)"

        lines = []
        for col_name, ctx in column_contexts.items():
            desc = ctx.get("description") or ""
            comment = ctx.get("comment") or ""
            line = f"- {col_name}"
            if desc:
                line += f": {desc}"
            if comment:
                line += f" ({comment})"
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
    reuse_context: ReuseContext | None = None,
    output_folder: str | None = None,
) -> str:
    """Convenience function to generate YARRRML mapping.

    Args:
        ai_service: AI service instance.
        mapping_proposal: The approved mapping proposal dictionary.
        csv_schema: The CSV schema dictionary with column info.
        csv_path: Path to the CSV file.
        base_uri: Base URI for generated resources.
        reuse_context: Optional SPARQL-based reuse context with existing URIs.
        output_folder: Optional dataset slug (CLI ``--output-folder``).

    Returns:
        Generated YARRRML mapping.

    Raises:
        RMLGenerationError: If generation fails.
    """
    generator = RMLGenerator(ai_service)
    return generator.generate(
        mapping_proposal,
        csv_schema,
        csv_path,
        base_uri,
        reuse_context=reuse_context,
        output_folder=output_folder,
    )
