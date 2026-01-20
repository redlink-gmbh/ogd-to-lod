"""Node functions for the LangGraph conversation flow."""

from typing import Any

import yaml

from ogd_to_lod.ai import AIService
from ogd_to_lod.config import Config
from ogd_to_lod.github import GitHubService, PRCreationError
from ogd_to_lod.logging import get_logger
from ogd_to_lod.parsers import CSVParseError, DCATParseError, parse_csv, parse_dcat
from ogd_to_lod.rml import RMLGenerationError, RMLGenerator

from .state import (
    DimensionProposal,
    FlowState,
    GraphState,
    MappingProposal,
    MeasureProposal,
    UserIntent,
)

logger = get_logger(__name__)


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

    state.add_message(
        "system",
        f"Starting mapping for CSV: {state.csv_path}"
        + (f" with DCAT: {state.dcat_path}" if state.dcat_path else ""),
    )

    # Transition to ANALYZE
    state.current_state = FlowState.ANALYZE
    logger.info("Transitioning to ANALYZE state")

    return state


def analyze_node(state: GraphState, config: Config) -> GraphState:
    """Analyze CSV and DCAT inputs.

    Parses the CSV file and optional DCAT metadata.

    Args:
        state: Current graph state.
        config: Application configuration.

    Returns:
        Updated state with parsed data.
    """
    logger.info("Entering ANALYZE state")

    # Parse CSV
    try:
        csv_data = parse_csv(state.csv_path)
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
            "sample_rows": csv_data.sample_rows[:3],
        }
        logger.debug(f"Parsed CSV with {len(csv_data.columns)} columns")
    except CSVParseError as e:
        state.error_message = f"Failed to parse CSV: {e}"
        state.current_state = FlowState.ERROR
        return state

    # Parse DCAT if provided
    if state.dcat_path:
        try:
            dcat_data = parse_dcat(state.dcat_path)
            state.dcat_metadata = {
                "title": dcat_data.title,
                "description": dcat_data.description,
                "publisher": dcat_data.publisher,
                "keywords": dcat_data.keywords,
                "temporal_coverage": (
                    {
                        "start": dcat_data.temporal_coverage.start_date,
                        "end": dcat_data.temporal_coverage.end_date,
                    }
                    if dcat_data.temporal_coverage
                    else None
                ),
                "spatial_coverage": (
                    {"location": dcat_data.spatial_coverage.location}
                    if dcat_data.spatial_coverage
                    else None
                ),
            }
            logger.debug(f"Parsed DCAT: {dcat_data.title}")
        except DCATParseError as e:
            logger.warning(f"Failed to parse DCAT: {e}")
            # DCAT is optional, so we continue

    # Build summary
    state.parsed_summary = _build_summary(state.csv_schema, state.dcat_metadata)

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

    # Ask AI for proposal
    prompt = """Based on the CSV schema and metadata provided, please propose a mapping structure.

Identify:
1. Which columns should be dimensions (and their types: temporal, spatial, or categorical)
2. Which columns should be measures (with units if applicable)
3. Any hierarchies that should be created

Provide your proposal in YAML format with your explanation."""

    logger.debug("Sending proposal request to AI")
    response = ai_service.send_message(prompt)
    parsed = AIService.parse_response(response)

    # Store response
    state.proposal_text = parsed.text
    state.add_message("assistant", response)

    # Parse YAML proposal if present
    yaml_blocks = parsed.get_yaml_blocks()
    if yaml_blocks:
        try:
            proposal_data = yaml.safe_load(yaml_blocks[0])
            state.mapping_proposal = _parse_proposal(proposal_data)
            num_dims = len(state.mapping_proposal.dimensions)
            logger.debug(f"Parsed proposal with {num_dims} dimensions")
        except yaml.YAMLError as e:
            logger.warning(f"Failed to parse YAML proposal: {e}")
            state.mapping_proposal = MappingProposal()
    else:
        state.mapping_proposal = MappingProposal()

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
    """Generate RML mapping from approved proposal.

    Uses the AI service to generate RML (RDF Mapping Language) in Turtle format
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
        )

        state.generated_rml = rml_content
        state.add_message(
            "assistant",
            f"Generated RML mapping:\n\n```turtle\n{rml_content}\n```"
        )

        logger.info(f"Successfully generated RML ({len(rml_content)} characters)")

        # Transition to PREVIEW state
        state.current_state = FlowState.PREVIEW
        logger.info("Transitioning to PREVIEW state")

    except RMLGenerationError as e:
        logger.error(f"RML generation failed: {e}")
        state.error_message = f"Failed to generate RML: {e}"
        state.current_state = FlowState.ERROR

    return state


def preview_node(state: GraphState) -> GraphState:
    """Display RML preview and wait for user confirmation to create PR.

    Args:
        state: Current graph state with generated RML.

    Returns:
        Updated state awaiting user confirmation.
    """
    logger.info("Entering PREVIEW state")

    if not state.generated_rml:
        state.error_message = "No RML generated for preview"
        state.current_state = FlowState.ERROR
        return state

    # The CLI will display the RML, we just need to wait for confirmation
    state.awaiting_user_input = True
    state.add_message(
        "assistant",
        "RML mapping has been generated. Would you like to create a PR with this mapping?"
    )

    logger.info("Awaiting user confirmation for PR creation")

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

    # Derive mapping name from CSV filename
    csv_filename = state.csv_path.split("/")[-1].split("\\")[-1]
    mapping_name = csv_filename.rsplit(".", 1)[0] if "." in csv_filename else csv_filename

    # Build PR description
    pr_description = _build_pr_description(state, mapping_name)

    # Create the PR
    try:
        github_service = GitHubService(config.github)
        result = github_service.create_mapping_pr(
            mapping_name=mapping_name,
            rml_content=state.generated_rml,
            description=pr_description,
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


def _build_pr_description(state: GraphState, mapping_name: str) -> str:
    """Build a human-readable PR description.

    Args:
        state: Current graph state.
        mapping_name: Name of the mapping.

    Returns:
        Formatted PR description in markdown.
    """
    lines = [
        f"## RML Mapping: {mapping_name}",
        "",
    ]

    # Add data source info
    if state.csv_path:
        lines.append(f"**CSV Source:** `{state.csv_path}`")
    if state.dcat_path:
        lines.append(f"**DCAT Metadata:** `{state.dcat_path}`")
    if state.base_uri:
        lines.append(f"**Base URI:** `{state.base_uri}`")
    lines.append("")

    # Add DCAT metadata if available
    if state.dcat_metadata:
        if state.dcat_metadata.get("title"):
            lines.append(f"**Dataset Title:** {state.dcat_metadata['title']}")
        if state.dcat_metadata.get("description"):
            desc = state.dcat_metadata["description"]
            if len(desc) > 200:
                desc = desc[:200] + "..."
            lines.append(f"**Description:** {desc}")
        lines.append("")

    # Add mapping summary
    if state.mapping_proposal:
        lines.append("### Mapping Structure")
        lines.append("")

        if state.mapping_proposal.dimensions:
            lines.append("**Dimensions:**")
            for dim in state.mapping_proposal.dimensions:
                dim_str = f"- `{dim.column}` ({dim.dimension_type})"
                if dim.granularity:
                    dim_str += f" - granularity: {dim.granularity}"
                if dim.hierarchy:
                    dim_str += f" - hierarchy: {dim.hierarchy}"
                lines.append(dim_str)
            lines.append("")

        if state.mapping_proposal.measures:
            lines.append("**Measures:**")
            for measure in state.mapping_proposal.measures:
                measure_str = f"- `{measure.column}`"
                if measure.unit:
                    measure_str += f" ({measure.unit})"
                if measure.aggregation:
                    measure_str += f" - aggregation: {measure.aggregation}"
                lines.append(measure_str)
            lines.append("")

    # Add footer
    lines.extend([
        "---",
        "_Generated by [OGD to LOD](https://github.com/redlink-gmbh/ogd-to-lod)_",
    ])

    return "\n".join(lines)


def _build_summary(csv_schema: dict[str, Any] | None, dcat_metadata: dict[str, Any] | None) -> str:
    """Build a human-readable summary of parsed data."""
    lines = []

    if csv_schema:
        lines.append("## CSV Schema")
        lines.append(f"Source: {csv_schema.get('source', 'Unknown')}")
        lines.append(f"Total rows: {csv_schema.get('total_rows', 0)}")
        lines.append("")
        lines.append("### Columns")
        for col in csv_schema.get("columns", []):
            samples = ", ".join(str(s) for s in col.get("samples", [])[:3])
            lines.append(f"- **{col['name']}** ({col['type']}): {samples}")

    if dcat_metadata:
        lines.append("")
        lines.append("## DCAT Metadata")
        if dcat_metadata.get("title"):
            lines.append(f"Title: {dcat_metadata['title']}")
        if dcat_metadata.get("description"):
            desc = dcat_metadata["description"][:200]
            lines.append(f"Description: {desc}...")
        if dcat_metadata.get("publisher"):
            lines.append(f"Publisher: {dcat_metadata['publisher']}")
        if dcat_metadata.get("keywords"):
            lines.append(f"Keywords: {', '.join(dcat_metadata['keywords'])}")

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
        lines.append("")
        lines.append("Columns:")
        for col in state.csv_schema.get("columns", []):
            samples = col.get("samples", [])
            samples_str = ", ".join(f'"{s}"' for s in samples[:3])
            lines.append(f"- {col['name']} ({col['type']}): [{samples_str}]")

        lines.append("")
        lines.append("Sample rows:")
        for i, row in enumerate(state.csv_schema.get("sample_rows", [])[:3], 1):
            lines.append(f"  Row {i}: {row}")

    if state.dcat_metadata:
        lines.append("")
        lines.append("## DCAT Metadata")
        for key, value in state.dcat_metadata.items():
            if value:
                lines.append(f"- {key}: {value}")

    return "\n".join(lines)


def _parse_proposal(data: dict[str, Any]) -> MappingProposal:
    """Parse YAML proposal data into MappingProposal."""
    proposal = MappingProposal()

    # Parse dimensions
    for dim_data in data.get("dimensions", []):
        dim = DimensionProposal(
            column=dim_data.get("column", ""),
            dimension_type=dim_data.get("type", "categorical"),
            granularity=dim_data.get("granularity"),
            hierarchy=dim_data.get("hierarchy"),
        )
        proposal.dimensions.append(dim)

    # Parse measures
    for measure_data in data.get("measures", []):
        measure = MeasureProposal(
            column=measure_data.get("column", ""),
            unit=measure_data.get("unit"),
            aggregation=measure_data.get("aggregation"),
        )
        proposal.measures.append(measure)

    return proposal
