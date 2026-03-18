"""Node functions for the LangGraph conversation flow."""

import re
from pathlib import Path
from typing import Any

import yaml

from ogd_to_lod.ai import AIService
from ogd_to_lod.config import Config
from ogd_to_lod.github import GitHubService, PRCreationError
from ogd_to_lod.github.pr_template import (
    build_csv_preview_section,
    build_mapping_structure_section,
    build_rdf_preview_section,
    load_pr_template,
    render_pr_template,
)
from ogd_to_lod.logging import get_logger
from ogd_to_lod.parsers import (
    CSVParseError,
    ContextParseError,
    parse_context,
    parse_csv,
)
from ogd_to_lod.rml import RMLGenerationError, RMLGenerator
from ogd_to_lod.validation import RMLValidator, ValidationResult

from .state import (
    DimensionProposal,
    FlowState,
    GraphState,
    MappingProposal,
    MeasureProposal,
    UserIntent,
)

logger = get_logger(__name__)

# Maximum number of automatic syntax retries before escalating to the user
MAX_SYNTAX_RETRIES = 3


def init_node(state: GraphState, config: Config) -> GraphState:
    """Initialize the conversation flow.

    Validates that required inputs are provided.

    Args:
        state: Current graph state.
        config: Application configuration.

    Returns:
        Updated state.
    """
    logger.info("Entering INIT state")

    # Check required inputs
    if not state.csv_path:
        state.error_message = "CSV path is required"
        state.current_state = FlowState.ERROR
        return state

    # Set base URI from config if not provided
    if not state.base_uri:
        state.base_uri = config.rml.base_uri

    context_info = ""
    if state.context_paths:
        context_info = f" with context: {', '.join(state.context_paths)}"
    state.add_message(
        "system",
        f"Starting mapping for CSV: {state.csv_path}{context_info}",
    )

    # Transition to ANALYZE
    state.current_state = FlowState.ANALYZE
    logger.info("Transitioning to ANALYZE state")

    return state


def analyze_node(state: GraphState, config: Config, ai_service: Any | None = None) -> GraphState:
    """Analyze CSV and context inputs.

    Parses the CSV file and any provided context files (DCAT, freetext,
    markdown, JSON, etc.) via AI-based normalization.

    Args:
        state: Current graph state.
        config: Application configuration.
        ai_service: AI service for context normalization (injected by flow).

    Returns:
        Updated state with parsed data.
    """
    logger.info("Entering ANALYZE state")

    # Parse CSV
    try:
        csv_data = parse_csv(state.csv_path, sample_rows=20, max_rows=20)
        state.csv_schema = {
            "source": csv_data.source,
            "columns": [
                {
                    "name": col.name,
                    "type": col.detected_type.value,
                    "samples": col.sample_values[:3],
                }
                for col in csv_data.columns
            ],
            "total_rows": csv_data.total_rows,
            "sample_rows": csv_data.sample_rows[:20],
            "delimiter": csv_data.delimiter,
        }
        logger.debug(f"Parsed CSV with {len(csv_data.columns)} columns")
    except CSVParseError as e:
        state.error_message = f"Failed to parse CSV: {e}"
        state.current_state = FlowState.ERROR
        return state

    csv_column_names = [col.name for col in csv_data.columns]

    # Parse context files if provided
    if state.context_paths and ai_service is not None:
        try:
            dataset_context, raw_files, dcat_raw, dcat_fmt = parse_context(
                sources=state.context_paths,
                csv_column_names=csv_column_names,
                ai_service=ai_service,
            )
            state.dataset_context = _serialize_dataset_context(dataset_context)
            state.context_raw_files = raw_files
            state.dcat_raw_content = dcat_raw
            state.dcat_source_format = dcat_fmt
            logger.debug(
                "Parsed context from %d file(s): title=%s, columns=%d",
                len(state.context_paths),
                dataset_context.title,
                len(dataset_context.column_contexts),
            )
        except ContextParseError as e:
            logger.warning("Failed to parse context files: %s", e)
            # Context is optional — continue without it

    # Build summary
    state.parsed_summary = _build_summary(state.csv_schema, state.dataset_context)

    # Transition to PROPOSE
    state.current_state = FlowState.PROPOSE
    logger.info("Transitioning to PROPOSE state")

    return state


def propose_node(state: GraphState, ai_service: AIService) -> GraphState:
    """Have AI propose a mapping based on analyzed data.

    Args:
        state: Current graph state.
        ai_service: AI service for generating proposals.

    Returns:
        Updated state with mapping proposal.
    """
    logger.info("Entering PROPOSE state")

    # Build context for AI
    context = _build_ai_context(state)
    ai_service.add_context(context)

    # Ask AI for proposal with explicit YAML format example
    prompt = """Based on the CSV schema and metadata provided, please propose a mapping structure.

Identify:
1. Which columns should be dimensions (and their types: temporal, spatial, or categorical)
2. Which columns should be measures (with units if applicable)
3. Any hierarchies that should be created
4. Which columns should be skipped (not mapped)
5. Whether any dimension value comes from context metadata (not from a CSV column)
6. Whether any context field (label, description) is used to enrich a property definition

Provide your proposal in YAML format following this exact structure:

```yaml
dimensions:
  - column: <column_name or property name like RAUM/ZEIT>
    type: <temporal|spatial|categorical>
    granularity: <optional: year, month, day, etc.>
    hierarchy: <optional: hierarchy name>
    datatype: <optional: xsd:dateTime, xsd:date, xsd:string, etc.>
    source: <optional: csv (default) or context>
    static_value: <optional: the context field name (e.g. spatial_coverage) when source is context>
    context_label: <optional: context field providing the property label/definition>
measures:
  - column: <column_name>
    unit: <optional: unit of measurement>
    aggregation: <optional: sum, avg, count, etc.>
    context_label: <optional: context field providing the property label/definition>
skipped_columns:
  - <column_name>
```

Important:
- Use exactly the keys shown above (dimensions, measures, skipped_columns, column, type, unit, etc.)
- List ALL columns that should not be mapped under skipped_columns
- Use source: context and static_value when a property value is taken from context metadata (not from a CSV column)
- Use datatype for temporal columns that need a specific XSD type (e.g. xsd:dateTime)
- Use context_label to indicate which context field provides the human-readable label or definition for a property"""

    logger.debug("Sending proposal request to AI")
    response = ai_service.send_message(prompt)
    parsed = AIService.parse_response(response)

    state.add_message("assistant", response)

    # Parse YAML proposal with robust parsing
    yaml_blocks = parsed.get_yaml_blocks()
    proposal = None

    if yaml_blocks:
        # Try each YAML block until one parses successfully
        for i, yaml_content in enumerate(yaml_blocks):
            proposal = _robust_parse_yaml_proposal(yaml_content)
            if proposal and (proposal.dimensions or proposal.measures):
                logger.debug(
                    f"Successfully parsed YAML block {i + 1} with "
                    f"{len(proposal.dimensions)} dimensions, {len(proposal.measures)} measures"
                )
                break
            logger.debug(f"YAML block {i + 1} did not contain valid proposal data")

    if proposal is None or (not proposal.dimensions and not proposal.measures):
        # Try to extract proposal from raw response as fallback
        logger.warning("No valid YAML blocks found, attempting to parse from raw response")
        proposal = _extract_proposal_from_text(response)

    state.mapping_proposal = proposal if proposal else MappingProposal()

    if not state.mapping_proposal.dimensions and not state.mapping_proposal.measures:
        logger.warning(
            "Could not parse mapping proposal from AI response. "
            "User may need to provide explicit structure."
        )
        fallback_text = (
            f"I couldn't generate a structured proposal. Here's my analysis:\n\n{parsed.text}\n\n"
            "Could you help me understand which columns should be dimensions and which should be measures?"
        )
        state.proposal_text = fallback_text
        state.add_message("assistant", fallback_text)
    else:
        # Present the proposal in human-readable format
        proposal_summary = _format_proposal_summary(state.mapping_proposal)
        display_text = (
            f"{parsed.text}\n\n{proposal_summary}\n\n"
            "Does this mapping structure look correct? "
            "(You can approve, suggest changes, or ask questions)"
        ).lstrip()
        state.proposal_text = display_text
        state.add_message("assistant", display_text)

    # Wait for user confirmation
    state.awaiting_user_input = True
    logger.info("Awaiting user input on proposal")

    return state


def handle_user_input(state: GraphState, user_input: str, ai_service: AIService) -> GraphState:
    """Process user input and determine next state.

    Args:
        state: Current graph state.
        user_input: User's response.
        ai_service: AI service for interpreting intent.

    Returns:
        Updated state with interpreted intent and next state.
    """
    logger.info(f"Processing user input: {user_input[:50]}...")

    state.user_input = user_input
    state.awaiting_user_input = False
    state.add_message("user", user_input)

    # Use AI to interpret user intent
    intent_prompt = f"""The user responded to the mapping proposal with: "{user_input}"

Determine their intent. Respond with ONLY one of these words:
- APPROVE: if they accept the proposal (e.g., "yes", "looks good", "ok", "ship it")
- REJECT: if they completely reject it
- OVERRIDE: if they want to change specific aspects
- QUESTION: if they are asking a question

Your response (one word only):"""

    # Send intent detection as a separate context (don't pollute main conversation)
    response = ai_service.send_message(intent_prompt)
    intent_text = response.strip().upper()

    # Map response to intent
    intent_map = {
        "APPROVE": UserIntent.APPROVE,
        "REJECT": UserIntent.REJECT,
        "OVERRIDE": UserIntent.OVERRIDE,
        "QUESTION": UserIntent.QUESTION,
    }
    state.user_intent = intent_map.get(intent_text, UserIntent.UNKNOWN)
    logger.debug(f"Detected user intent: {state.user_intent}")

    # Determine next state based on intent
    if state.user_intent == UserIntent.APPROVE:
        state.current_state = FlowState.GENERATE
        if state.mapping_proposal:
            state.mapping_proposal.status = "approved"
        logger.info("User approved, transitioning to GENERATE")
    elif state.user_intent in (UserIntent.OVERRIDE, UserIntent.QUESTION, UserIntent.REJECT):
        state.current_state = FlowState.REFINE
        if state.mapping_proposal:
            state.mapping_proposal.status = "refining"
        logger.info("User wants changes, transitioning to REFINE")
    else:
        # Unknown intent, stay in current state and ask for clarification
        state.awaiting_user_input = True
        logger.info("Unknown intent, awaiting clarification")

    return state


def generate_node(state: GraphState, ai_service: AIService) -> GraphState:
    """Generate YARRRML mapping from approved proposal.

    Uses the AI service to generate YARRRML (YAML-based RML)
    based on the approved mapping proposal and CSV schema.

    Args:
        state: Current graph state with approved mapping proposal.
        ai_service: AI service for generating RML.

    Returns:
        Updated state with generated RML.
    """
    logger.info("Entering GENERATE state")

    # Validate prerequisites
    if not state.mapping_proposal:
        state.error_message = "No mapping proposal available for RML generation"
        state.current_state = FlowState.ERROR
        return state

    if state.mapping_proposal.status != "approved":
        state.error_message = "Mapping proposal must be approved before generating RML"
        state.current_state = FlowState.ERROR
        return state

    if not state.csv_schema:
        state.error_message = "CSV schema is required for RML generation"
        state.current_state = FlowState.ERROR
        return state

    if not state.csv_path:
        state.error_message = "CSV path is required for RML generation"
        state.current_state = FlowState.ERROR
        return state

    if not state.base_uri:
        state.error_message = "Base URI is required for RML generation"
        state.current_state = FlowState.ERROR
        return state

    # Generate RML
    try:
        generator = RMLGenerator(ai_service)
        rml_content = generator.generate(
            mapping_proposal=state.mapping_proposal.to_dict(),
            csv_schema=state.csv_schema,
            csv_path=state.csv_path,
            base_uri=state.base_uri,
            dataset_context=state.dataset_context,
        )

        state.generated_rml = rml_content
        state.add_message(
            "assistant",
            f"Generated YARRRML mapping:\n\n```yaml\n{rml_content}\n```"
        )

        logger.info(f"Successfully generated YARRRML ({len(rml_content)} characters)")

        # Transition to PREVIEW state
        state.current_state = FlowState.PREVIEW
        logger.info("Transitioning to PREVIEW state")

    except RMLGenerationError as e:
        logger.error(f"RML generation failed: {e}")
        state.error_message = f"Failed to generate RML: {e}"
        state.current_state = FlowState.ERROR

    return state


def suggest_mapping_name(state: GraphState) -> str:
    """Derive a mapping name from DCAT title (preferred) or CSV filename (fallback).

    Args:
        state: Current graph state.

    Returns:
        A slug-style mapping name suitable for branch and file names.
    """
    # Prefer dataset context title
    if state.dataset_context and state.dataset_context.get("title"):
        raw = state.dataset_context["title"]
    elif state.csv_path:
        csv_filename = state.csv_path.split("/")[-1].split("\\")[-1]
        raw = csv_filename.rsplit(".", 1)[0] if "." in csv_filename else csv_filename
    else:
        raw = "mapping"

    # Normalise: lowercase, replace non-alphanum with hyphens, collapse/strip
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    return slug or "mapping"


def confirm_name_node(state: GraphState) -> GraphState:
    """Ask the user to confirm or change the mapping name.

    Suggests a mapping name (from DCAT title or CSV filename) and waits
    for the user to accept it or type a different name.

    Args:
        state: Current graph state with generated RML.

    Returns:
        Updated state awaiting name confirmation.
    """
    logger.info("Entering CONFIRM_NAME state")

    if not state.generated_rml:
        state.error_message = "No RML generated for preview"
        state.current_state = FlowState.ERROR
        return state

    # Suggest mapping name if not already set (e.g. by user override)
    if not state.mapping_name:
        state.mapping_name = suggest_mapping_name(state)
        logger.debug("Suggested mapping name: %s", state.mapping_name)

    state.current_state = FlowState.CONFIRM_NAME
    state.awaiting_user_input = True
    state.add_message(
        "assistant",
        f"Suggested mapping name: '{state.mapping_name}'. "
        "Press Enter to accept or type a different name:"
    )

    logger.info("Awaiting name confirmation")

    return state


def preview_node(state: GraphState, ai_service: AIService | None = None) -> GraphState:
    """Build the PR description and show it for confirmation.

    Builds the full PR description from the template and stores it in
    ``state.pr_description``, then asks the user to confirm pushing.

    When *ai_service* is provided and ``state.mapping_decisions`` is not
    yet set, a short AI-generated summary of the key mapping decisions
    is produced and stored in the state.

    Args:
        state: Current graph state with confirmed mapping name.
        ai_service: Optional AI service for generating decision summaries.

    Returns:
        Updated state awaiting push confirmation.
    """
    logger.info("Entering PREVIEW state")

    if not state.generated_rml:
        state.error_message = "No RML generated for preview"
        state.current_state = FlowState.ERROR
        return state

    # Generate mapping decisions summary if AI service is available
    if ai_service is not None and state.mapping_decisions is None:
        try:
            prompt = (
                "Based on the conversation so far, write a brief summary (3-5 bullet points) "
                "of the key mapping decisions:\n"
                "- Which columns became dimensions vs measures and why\n"
                "- Any columns that were dropped and why\n"
                "- Hierarchy or aggregation choices\n"
                "Keep it concise — this will appear in a PR description."
            )
            decisions = ai_service.send_message(prompt)
            state.mapping_decisions = decisions.strip()
            logger.debug("Generated mapping decisions summary")
        except Exception:
            logger.warning("Failed to generate mapping decisions summary", exc_info=True)

    # Build and store PR description
    mapping_name = state.mapping_name or "mapping"
    state.pr_description = _build_pr_description(state, mapping_name)

    state.current_state = FlowState.PREVIEW
    state.awaiting_user_input = True
    state.add_message(
        "assistant",
        f"Here is the PR that will be created:\n\n{state.pr_description}\n\n"
        "Push to GitHub? (yes/no)"
    )

    logger.info("Awaiting push confirmation")

    return state


def create_pr_node(state: GraphState, config: Config) -> GraphState:
    """Create a GitHub PR with the generated RML mapping.

    Args:
        state: Current graph state with generated RML and mapping proposal.
        config: Application configuration with GitHub settings.

    Returns:
        Updated state with PR information.
    """
    logger.info("Entering CREATE_PR state")

    # Validate prerequisites
    if not state.generated_rml:
        state.error_message = "No RML generated for PR creation"
        state.current_state = FlowState.ERROR
        return state

    if not state.csv_path:
        state.error_message = "CSV path is required for PR creation"
        state.current_state = FlowState.ERROR
        return state

    # Use state.mapping_name if set, otherwise fall back to CSV filename
    if state.mapping_name:
        mapping_name = state.mapping_name
    else:
        csv_filename = state.csv_path.split("/")[-1].split("\\")[-1]
        mapping_name = csv_filename.rsplit(".", 1)[0] if "." in csv_filename else csv_filename

    # Build PR description
    pr_description = _build_pr_description(state, mapping_name)

    # Determine output folder (CLI param) and CSV details
    output_folder = state.output_folder or mapping_name
    csv_path_obj = Path(state.csv_path)
    csv_filename = csv_path_obj.name
    csv_content = csv_path_obj.read_text(encoding="utf-8")

    # Create the PR
    try:
        github_service = GitHubService(config.github)
        result = github_service.create_mapping_pr(
            mapping_name=mapping_name,
            rml_content=state.generated_rml,
            description=pr_description,
            output_folder=output_folder,
            csv_filename=csv_filename,
            csv_content=csv_content,
            mappings_folder=config.github.mappings_folder,
        )

        state.pr_url = result.pr_url
        state.pr_number = result.pr_number

        state.add_message(
            "assistant",
            f"PR created successfully!\n\nPR #{result.pr_number}: {result.pr_url}"
        )

        logger.info(f"PR created: #{result.pr_number}")

        # Transition to END state
        state.current_state = FlowState.END
        logger.info("Flow completed successfully")

    except PRCreationError as e:
        logger.error(f"PR creation failed: {e}")
        state.error_message = f"Failed to create PR: {e}"
        state.current_state = FlowState.ERROR

    return state


def _serialize_dataset_context(context: Any) -> dict[str, Any]:
    """Serialize a DatasetContext to a JSON-compatible dict for GraphState storage."""
    from ogd_to_lod.parsers.models import DatasetContext

    if not isinstance(context, DatasetContext):
        return {}

    column_contexts = {}
    for name, col_ctx in context.column_contexts.items():
        column_contexts[name] = {
            "header_name": col_ctx.header_name,
            "description": col_ctx.description,
            "comment": col_ctx.comment,
        }

    return {
        "sources": context.sources,
        "title": context.title,
        "description": context.description,
        "publisher": context.publisher,
        "keywords": context.keywords,
        "temporal_coverage": (
            {
                "start": context.temporal_coverage.start_date,
                "end": context.temporal_coverage.end_date,
            }
            if context.temporal_coverage
            else None
        ),
        "spatial_coverage": (
            {"location": context.spatial_coverage.location}
            if context.spatial_coverage
            else None
        ),
        "identifier": context.identifier,
        "issued": context.issued,
        "modified": context.modified,
        "language": context.language,
        "license": context.license,
        "access_rights": context.access_rights,
        "contact_point": context.contact_point,
        "column_contexts": column_contexts,
        "source_format": context.source_format,
    }


def _format_proposal_summary(proposal: MappingProposal) -> str:
    """Format a mapping proposal into a human-readable summary.

    Args:
        proposal: The mapping proposal to format.

    Returns:
        Formatted markdown summary of the proposal.
    """
    lines = ["## Proposed RDF Data Cube Mapping", ""]

    if proposal.dimensions:
        lines.append("### Dimensions (Key Dimensions)")
        lines.append("These columns will be mapped to properties with resource (URI) values:")
        lines.append("")
        for dim in proposal.dimensions:
            # Build dimension description
            dim_desc = f"- **`{dim.column}`** → "

            # Add property name
            if dim.dimension_type == "temporal":
                dim_desc += "`ex-property:ZEIT`"
            elif dim.dimension_type == "spatial":
                dim_desc += "`ex-property:RAUM`"
            else:
                dim_desc += f"`ex-property:{dim.column}`"

            # Add dimension type
            dim_desc += f" ({dim.dimension_type})"

            # Add additional details
            details = []
            if dim.granularity:
                details.append(f"granularity: {dim.granularity}")
            if dim.hierarchy:
                details.append(f"hierarchy: {dim.hierarchy}")
            if dim.datatype:
                details.append(f"datatype: `{dim.datatype}`")

            if details:
                dim_desc += f" — {', '.join(details)}"

            # Show data source
            if dim.source == "context":
                static_ref = f" (`{dim.static_value}`)" if dim.static_value else ""
                dim_desc += f"\n  - Source: static value from context metadata{static_ref}"
            else:
                # Add note about resource values from CSV
                dim_desc += f"\n  - Values: `ex-code:{{{{value}}}}` (resources of type `schema:DefinedTerm`)"

            # Show context label/definition enrichment
            if dim.context_label:
                dim_desc += f"\n  - Property definition from context: `{dim.context_label}`"

            lines.append(dim_desc)
        lines.append("")

    if proposal.measures:
        lines.append("### Measures")
        lines.append("These columns will be mapped to properties with literal values:")
        lines.append("")
        for measure in proposal.measures:
            measure_desc = f"- **`{measure.column}`** → `ex-property:{measure.column}`"

            # Add additional details
            details = []
            if measure.unit:
                details.append(f"unit: {measure.unit}")
            if measure.aggregation:
                details.append(f"aggregation: {measure.aggregation}")

            if details:
                measure_desc += f" — {', '.join(details)}"

            if measure.context_label:
                measure_desc += f"\n  - Property definition from context: `{measure.context_label}`"

            lines.append(measure_desc)
        lines.append("")

    if proposal.skipped_columns:
        lines.append("### Skipped Columns")
        lines.append("These columns will not be included in the mapping:")
        lines.append("")
        for col in proposal.skipped_columns:
            lines.append(f"- ~~`{col}`~~")
        lines.append("")

    # Add explanation
    lines.extend([
        "---",
        "**Note:** Each CSV row will become one `cube:Observation` resource.",
    ])

    return "\n".join(lines)


def _build_pr_description(state: GraphState, mapping_name: str) -> str:
    """Build a human-readable PR description using the external template.

    Args:
        state: Current graph state.
        mapping_name: Name of the mapping.

    Returns:
        Formatted PR description in markdown.
    """
    template_text = load_pr_template(Path("config/pr_template.md"))

    # Derive dataset name: prefer context title, fall back to mapping_name
    dataset_name = mapping_name
    if state.dataset_context and state.dataset_context.get("title"):
        dataset_name = state.dataset_context["title"]

    # Derive dataset description from context
    dataset_description = ""
    if state.dataset_context and state.dataset_context.get("description"):
        desc = state.dataset_context["description"]
        dataset_description = desc[:200] + "..." if len(desc) > 200 else desc

    # Build context files list for the PR template
    if state.context_paths:
        import pathlib
        context_files_str = ", ".join(
            f"`{pathlib.Path(p).name}`" for p in state.context_paths
        )
    else:
        context_files_str = "(none provided)"

    data = {
        "dataset_name": dataset_name,
        "dataset_description": dataset_description,
        "csv_source": state.csv_source_url or "(not provided)",
        "context_files": context_files_str,
        "base_uri": f"`{state.base_uri}`" if state.base_uri else "",
        "mapping_structure": build_mapping_structure_section(
            state.mapping_proposal, state.mapping_decisions
        ),
        "csv_preview": build_csv_preview_section(state.csv_schema),
        "rdf_preview": build_rdf_preview_section(state.rdf_preview),
    }

    return render_pr_template(template_text, data)


def _build_summary(csv_schema: dict[str, Any] | None, dataset_context: dict[str, Any] | None) -> str:
    """Build a human-readable summary of parsed data."""
    lines = []

    if csv_schema:
        lines.append("## CSV Schema")
        lines.append(f"Source: {csv_schema.get('source', 'Unknown')}")
        lines.append(f"Total rows: {csv_schema.get('total_rows', 0)}")
        lines.append("")
        lines.append("### Columns")
        column_contexts = (dataset_context or {}).get("column_contexts", {})
        for col in csv_schema.get("columns", []):
            samples = ", ".join(str(s)[:200] for s in col.get("samples", [])[:3])
            col_name = col["name"]
            line = f"- **{col_name}** ({col['type']}): {samples}"
            if ctx := column_contexts.get(col_name):
                if ctx.get("description"):
                    line += f"\n  _{ctx['description']}_"
            lines.append(line)

    if dataset_context:
        lines.append("")
        lines.append("## Dataset Context")
        if dataset_context.get("title"):
            lines.append(f"Title: {dataset_context['title']}")
        if dataset_context.get("description"):
            desc = dataset_context["description"][:200]
            lines.append(f"Description: {desc}...")
        if dataset_context.get("publisher"):
            lines.append(f"Publisher: {dataset_context['publisher']}")
        if dataset_context.get("keywords"):
            lines.append(f"Keywords: {', '.join(dataset_context['keywords'])}")
        if dataset_context.get("source_format"):
            lines.append(f"Format: {dataset_context['source_format']}")

    return "\n".join(lines)


def _build_ai_context(state: GraphState) -> str:
    """Build context string for AI from state."""
    lines = [
        "# Data Context for RML Mapping",
        "",
        f"Base URI: {state.base_uri}",
        "",
    ]

    if state.csv_schema:
        lines.append("## CSV Schema")
        lines.append(f"Total rows: {state.csv_schema.get('total_rows', 0)}")
        delimiter = state.csv_schema.get("delimiter", ",")
        lines.append(f"Delimiter: {repr(delimiter)}")
        lines.append("")
        lines.append("Columns:")
        for col in state.csv_schema.get("columns", []):
            samples = col.get("samples", [])
            samples_str = ", ".join(f'"{str(s)[:200]}"' for s in samples[:3])
            lines.append(f"- {col['name']} ({col['type']}): [{samples_str}]")

        lines.append("")
        lines.append("Sample rows:")
        for i, row in enumerate(state.csv_schema.get("sample_rows", [])[:3], 1):
            lines.append(f"  Row {i}: {row}")

    if state.dataset_context:
        lines.append("")
        lines.append("## Dataset Context")
        for key, value in state.dataset_context.items():
            if key == "column_contexts":
                continue  # shown per-column below
            if value:
                lines.append(f"- {key}: {value}")
        # Column-level context
        column_contexts = state.dataset_context.get("column_contexts", {})
        if column_contexts:
            lines.append("")
            lines.append("## Column Descriptions")
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


def _parse_proposal(data: dict[str, Any]) -> MappingProposal:
    """Parse YAML proposal data into MappingProposal.

    Handles various key name variations that AI might use.
    """
    proposal = MappingProposal()

    # Try various key names for dimensions
    dimensions_data = (
        data.get("dimensions")
        or data.get("dimension")
        or data.get("dims")
        or data.get("Dimensions")
        or []
    )

    # Parse dimensions
    for dim_data in dimensions_data:
        if not isinstance(dim_data, dict):
            continue
        column = (
            dim_data.get("column")
            or dim_data.get("name")
            or dim_data.get("col")
            or dim_data.get("field")
            or ""
        )
        dim_type = (
            dim_data.get("type")
            or dim_data.get("dimension_type")
            or dim_data.get("kind")
            or "categorical"
        )
        dim = DimensionProposal(
            column=str(column),
            dimension_type=str(dim_type).lower(),
            granularity=dim_data.get("granularity"),
            hierarchy=dim_data.get("hierarchy"),
            datatype=dim_data.get("datatype"),
            source=dim_data.get("source"),
            static_value=dim_data.get("static_value"),
            context_label=dim_data.get("context_label"),
        )
        if dim.column:  # Only add if column name exists
            proposal.dimensions.append(dim)

    # Try various key names for measures
    measures_data = (
        data.get("measures")
        or data.get("measure")
        or data.get("metrics")
        or data.get("metric")
        or data.get("Measures")
        or []
    )

    # Parse measures
    for measure_data in measures_data:
        if not isinstance(measure_data, dict):
            continue
        column = (
            measure_data.get("column")
            or measure_data.get("name")
            or measure_data.get("col")
            or measure_data.get("field")
            or ""
        )
        measure = MeasureProposal(
            column=str(column),
            unit=measure_data.get("unit"),
            aggregation=measure_data.get("aggregation") or measure_data.get("agg"),
            context_label=measure_data.get("context_label"),
        )
        if measure.column:  # Only add if column name exists
            proposal.measures.append(measure)

    # Parse skipped columns
    skipped_data = (
        data.get("skipped_columns")
        or data.get("skipped")
        or data.get("skip")
        or []
    )
    if isinstance(skipped_data, list):
        proposal.skipped_columns = [str(c) for c in skipped_data if c]

    return proposal


def syntax_check_node(state: GraphState) -> GraphState:
    """Tier 1: Check RML Turtle syntax using rdflib.

    Fast, cheap check that catches syntax errors. On failure, stores the
    error in state so the flow can auto-retry via re-generation.

    Args:
        state: Current graph state with generated RML.

    Returns:
        Updated state. On success, transitions to VALIDATE (Tier 2).
        On failure, sets validation_error with the syntax error.
    """
    logger.info("Entering SYNTAX_CHECK (Tier 1)")

    if not state.generated_rml:
        state.error_message = "No RML to validate"
        state.current_state = FlowState.ERROR
        return state

    validator = RMLValidator()
    result = validator.validate_syntax(state.generated_rml)

    if result.valid:
        logger.info("Tier 1 syntax check passed")
        state.validation_error = None
        state.current_state = FlowState.VALIDATE
    else:
        logger.warning(f"Tier 1 syntax check failed: {result.error_message}")
        state.validation_error = result.error_message
        state.add_message(
            "assistant",
            f"RML syntax error (auto-retrying):\n\n{result.error_message}",
        )

    return state


def regenerate_node(state: GraphState, ai_service: AIService) -> GraphState:
    """Regenerate YARRRML by sending the validation error back to the AI.

    Uses the error stored in ``state.validation_error`` (set by
    ``syntax_check_node``) to give the AI targeted feedback.  Updates
    ``state.generated_rml`` with the corrected output.

    Args:
        state: Current graph state with validation_error set.
        ai_service: AI service for regenerating RML.

    Returns:
        Updated state with corrected RML.
    """
    logger.info("Entering REGENERATE state (error-aware retry)")

    error_message = state.validation_error or "Unknown syntax error"

    try:
        generator = RMLGenerator(ai_service)
        rml_content = generator.regenerate_with_error(error_message)

        state.generated_rml = rml_content
        state.add_message(
            "assistant",
            f"Corrected YARRRML mapping:\n\n```yaml\n{rml_content}\n```",
        )

        logger.info(f"Regenerated YARRRML ({len(rml_content)} characters)")

        # Transition to PREVIEW so syntax_check_node can re-validate
        state.current_state = FlowState.PREVIEW

    except RMLGenerationError as e:
        logger.error(f"RML regeneration failed: {e}")
        state.error_message = f"Failed to regenerate RML: {e}"
        state.current_state = FlowState.ERROR

    return state


def validate_node(
    state: GraphState,
    rmlmapper_jar: str | None = None,
    use_docker: bool = False,
    yarrrml_parser_docker_image: str = "rmlio/yarrrml-parser:latest",
) -> GraphState:
    """Tier 2: Validate YARRRML mapping using Docker two-step pipeline.

    Converts YARRRML → Turtle via yarrrml-parser, then runs RMLMapper.
    On failure, escalates to the user (transitions to REFINE) since
    data-fit issues need human judgement.

    If Docker is not enabled, gracefully skips to PREVIEW with a note.

    Args:
        state: Current graph state with generated YARRRML.
        rmlmapper_jar: Path to RMLMapper JAR file (optional, unused when Docker enabled).
        use_docker: Whether to use Docker for validation.
        yarrrml_parser_docker_image: Docker image for yarrrml-parser.

    Returns:
        Updated state with validation result.
    """
    logger.info("Entering VALIDATE state (Tier 2 — yarrrml-parser + RMLMapper)")

    # Check prerequisites
    if not state.generated_rml:
        state.error_message = "No YARRRML to validate"
        state.current_state = FlowState.ERROR
        return state

    if not state.csv_path:
        state.error_message = "CSV path required for validation"
        state.current_state = FlowState.ERROR
        return state

    # Initialize validator
    validator = RMLValidator(
        rmlmapper_jar=rmlmapper_jar,
        use_docker=use_docker,
        yarrrml_parser_docker_image=yarrrml_parser_docker_image,
    )

    # Run Tier 2 validation with sample CSV
    logger.debug(f"Validating RML against sample from {state.csv_path}")
    result = validator.validate_with_rmlmapper(state.generated_rml, state.csv_path)

    if result.valid:
        logger.info("Tier 2 RMLMapper validation successful")

        # Store RDF preview
        state.rdf_preview = result.rdf_output
        state.validation_error = None

        # Log any warnings
        if result.warnings:
            for warning in result.warnings:
                logger.warning(f"Validation warning: {warning}")

        # Add success message
        if result.rdf_output:
            state.add_message(
                "assistant",
                f"RML validation successful! Here's a preview of the generated RDF:\n\n"
                f"```turtle\n{result.rdf_output}\n```",
            )
        else:
            # RMLMapper was skipped (not configured)
            note = ""
            if result.warnings:
                note = f" ({result.warnings[0]})"
            state.add_message(
                "assistant",
                f"RML syntax validation successful.{note}",
            )

        # Transition to PREVIEW
        state.current_state = FlowState.PREVIEW
        logger.info("Transitioning to PREVIEW state")

    else:
        logger.warning(f"Tier 2 validation failed: {result.error_message}")

        # Store validation error with category info
        error_desc = result.error_message
        if result.user_friendly_error:
            error_desc = result.user_friendly_error

        state.validation_error = result.error_message
        state.rdf_preview = None

        # Escalate to user (data-fit issues need human judgement)
        state.add_message(
            "assistant",
            f"RML validation failed (RMLMapper):\n\n{error_desc}\n\n"
            "Please review and suggest how to fix this issue.",
        )

        # Transition to REFINE — always escalate Tier 2 failures
        state.current_state = FlowState.REFINE
        if state.mapping_proposal:
            state.mapping_proposal.status = "refining"
        logger.info("Tier 2 validation failed, transitioning to REFINE state")

    return state
def _robust_parse_yaml_proposal(yaml_content: str) -> MappingProposal | None:
    """Robustly parse YAML content into a MappingProposal.

    Tries multiple parsing strategies:
    1. Standard YAML parsing
    2. Fix common YAML issues and retry
    3. Try parsing as different structures

    Args:
        yaml_content: Raw YAML string from AI response.

    Returns:
        MappingProposal if parsing succeeds, None otherwise.
    """
    # Strategy 1: Try standard parsing
    try:
        data = yaml.safe_load(yaml_content)
        if isinstance(data, dict):
            return _parse_proposal(data)
    except yaml.YAMLError as e:
        logger.debug(f"Standard YAML parsing failed: {e}")

    # Strategy 2: Try to fix common YAML issues
    fixed_content = _fix_common_yaml_issues(yaml_content)
    if fixed_content != yaml_content:
        try:
            data = yaml.safe_load(fixed_content)
            if isinstance(data, dict):
                logger.debug("YAML parsing succeeded after fixing common issues")
                return _parse_proposal(data)
        except yaml.YAMLError as e:
            logger.debug(f"Fixed YAML parsing failed: {e}")

    # Strategy 3: Try parsing line by line for simple structures
    try:
        proposal = _parse_yaml_line_by_line(yaml_content)
        if proposal and (proposal.dimensions or proposal.measures):
            logger.debug("Line-by-line YAML parsing succeeded")
            return proposal
    except Exception as e:
        logger.debug(f"Line-by-line parsing failed: {e}")

    return None


def _fix_common_yaml_issues(yaml_content: str) -> str:
    """Fix common YAML formatting issues from AI responses.

    Handles:
    - Trailing commas (JSON-style)
    - Inconsistent indentation
    - Missing colons
    - Smart quotes

    Args:
        yaml_content: Raw YAML string.

    Returns:
        Fixed YAML string.
    """
    content = yaml_content

    # Replace smart quotes with regular quotes using Unicode escapes
    # Left/right double quotes: U+201C, U+201D -> "
    content = content.replace('\u201c', '"').replace('\u201d', '"')
    # Left/right single quotes: U+2018, U+2019 -> '
    content = content.replace('\u2018', "'").replace('\u2019', "'")

    # Remove trailing commas (JSON-style)
    content = re.sub(r',(\s*\n)', r'\1', content)
    content = re.sub(r',(\s*])', r'\1', content)
    content = re.sub(r',(\s*})', r'\1', content)

    # Normalize indentation (convert tabs to spaces)
    content = content.replace('\t', '  ')

    # Fix common missing space after colon
    content = re.sub(r':([^\s\n])', r': \1', content)

    return content


def _parse_yaml_line_by_line(yaml_content: str) -> MappingProposal | None:
    """Parse YAML content line by line for simple list structures.

    This is a fallback parser for when standard YAML parsing fails.
    It looks for patterns like:
    - column: value
    - type: value

    Args:
        yaml_content: Raw YAML string.

    Returns:
        MappingProposal if patterns are found, None otherwise.
    """
    proposal = MappingProposal()
    lines = yaml_content.split('\n')

    current_section: str | None = None
    current_item: dict[str, Any] = {}

    def save_current_item() -> None:
        """Save the current item to the appropriate list."""
        nonlocal current_item
        if not current_item or not current_section:
            return

        if current_section == 'dimensions':
            dim = DimensionProposal(
                column=current_item.get('column', ''),
                dimension_type=current_item.get('type', 'categorical'),
                granularity=current_item.get('granularity'),
                hierarchy=current_item.get('hierarchy'),
                datatype=current_item.get('datatype'),
                source=current_item.get('source'),
                static_value=current_item.get('static_value'),
                context_label=current_item.get('context_label'),
            )
            if dim.column:
                proposal.dimensions.append(dim)
        elif current_section == 'measures':
            measure = MeasureProposal(
                column=current_item.get('column', ''),
                unit=current_item.get('unit'),
                aggregation=current_item.get('aggregation'),
                context_label=current_item.get('context_label'),
            )
            if measure.column:
                proposal.measures.append(measure)
        elif current_section == 'skipped_columns':
            col = current_item.get('column', '')
            if col:
                proposal.skipped_columns.append(col)
        current_item = {}

    for line in lines:
        stripped = line.strip()

        # Detect section headers - save current item before switching
        if stripped.startswith('dimensions:') or stripped.startswith('Dimensions:'):
            save_current_item()
            current_section = 'dimensions'
            continue
        elif stripped.startswith('measures:') or stripped.startswith('Measures:'):
            save_current_item()
            current_section = 'measures'
            continue
        elif stripped.startswith('skipped_columns:') or stripped.startswith('skipped:'):
            save_current_item()
            current_section = 'skipped_columns'
            continue

        # Skip empty lines
        if not stripped:
            continue

        # Detect list item start
        if stripped.startswith('- '):
            rest = stripped[2:].strip()
            if current_section == 'skipped_columns':
                # Plain list item — the value is the column name
                if rest and ':' not in rest:
                    proposal.skipped_columns.append(rest)
                    continue
            # Save previous item if exists
            save_current_item()
            current_item = {}

            # Parse inline key-value if present (e.g., "- column: year")
            if ':' in rest:
                key, value = rest.split(':', 1)
                current_item[key.strip()] = value.strip()
        elif ':' in stripped and current_section:
            # Parse key-value pair
            key, value = stripped.split(':', 1)
            current_item[key.strip()] = value.strip()

    # Don't forget the last item
    save_current_item()

    return proposal if (proposal.dimensions or proposal.measures) else None


def _extract_proposal_from_text(response: str) -> MappingProposal | None:
    """Extract proposal information from unstructured text response.

    This is a last-resort fallback that tries to identify dimensions
    and measures from natural language descriptions.

    Args:
        response: Full AI response text.

    Returns:
        MappingProposal if information can be extracted, None otherwise.
    """
    proposal = MappingProposal()

    # Look for dimension mentions with column names
    # Patterns like: "column 'year' as temporal dimension"
    # or "year - temporal dimension"
    # or "'year' should be a temporal dimension"
    dim_patterns = [
        # 'column' should be a temporal dimension
        r"[`'\"](\w+)[`'\"]\s+(?:should be|is|as)\s+(?:a\s+)?(\w+)\s+dimension",
        # Column 'year' ... temporal dimension
        r"[Cc]olumn\s+[`'\"](\w+)[`'\"].*?(\w+)\s+dimension",
        # year - temporal dimension
        r"[`'\"]?(\w+)[`'\"]?\s*[-–]\s*(\w+)\s+dimension",
        # dimension: year (temporal)
        r"dimension[:\s]+[`'\"]?(\w+)[`'\"]?\s*\((\w+)\)",
    ]

    for pattern in dim_patterns:
        matches = re.finditer(pattern, response, re.IGNORECASE)
        for match in matches:
            column = match.group(1)
            dim_type = match.group(2).lower()
            if dim_type in ('temporal', 'spatial', 'categorical'):
                dim = DimensionProposal(column=column, dimension_type=dim_type)
                # Avoid duplicates
                if not any(d.column == column for d in proposal.dimensions):
                    proposal.dimensions.append(dim)

    # Look for measure mentions
    # Patterns like: "column 'value' as measure" or "'count' measure"
    measure_patterns = [
        r"[`'\"](\w+)[`'\"]\s+(?:should be|is|as)\s+(?:a\s+)?measure",
        r"[Cc]olumn\s+[`'\"](\w+)[`'\"].*?(?:as\s+)?(?:a\s+)?measure",
        r"[`'\"]?(\w+)[`'\"]?\s*[-–]\s*measure",
        r"measure[:\s]+[`'\"]?(\w+)[`'\"]?",
    ]

    for pattern in measure_patterns:
        matches = re.finditer(pattern, response, re.IGNORECASE)
        for match in matches:
            column = match.group(1)
            measure = MeasureProposal(column=column)
            # Avoid duplicates
            if not any(m.column == column for m in proposal.measures):
                proposal.measures.append(measure)

    return proposal if (proposal.dimensions or proposal.measures) else None
