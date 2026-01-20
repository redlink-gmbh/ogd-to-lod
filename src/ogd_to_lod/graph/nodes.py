"""Node functions for the LangGraph conversation flow."""

import yaml
from typing import Any

from ogd_to_lod.ai import AIService, ParsedResponse
from ogd_to_lod.config import Config
from ogd_to_lod.logging import get_logger
from ogd_to_lod.parsers import parse_csv, parse_dcat, CSVParseError, DCATParseError
from ogd_to_lod.rml import RMLGenerator, RMLGenerationError
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
            logger.debug(f"Parsed proposal with {len(state.mapping_proposal.dimensions)} dimensions")
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


def validate_node(
    state: GraphState,
    rmlmapper_jar: str | None = None,
    use_docker: bool = False,
) -> GraphState:
    """Validate the generated RML mapping.

    Executes the RML mapping against the source CSV to verify it produces
    valid RDF output. If validation fails, the error is stored and the flow
    can loop back for refinement.

    Args:
        state: Current graph state with generated RML.
        rmlmapper_jar: Path to RMLMapper JAR file (optional).
        use_docker: Whether to use Docker for RMLMapper.

    Returns:
        Updated state with validation result.
    """
    logger.info("Entering VALIDATE state")

    # Check prerequisites
    if not state.generated_rml:
        state.error_message = "No RML to validate"
        state.current_state = FlowState.ERROR
        return state

    if not state.csv_path:
        state.error_message = "CSV path required for validation"
        state.current_state = FlowState.ERROR
        return state

    # Initialize validator
    validator = RMLValidator(rmlmapper_jar=rmlmapper_jar, use_docker=use_docker)

    # Run validation
    logger.debug(f"Validating RML against {state.csv_path}")
    result = validator.validate(state.generated_rml, state.csv_path)

    if result.valid:
        logger.info("RML validation successful")

        # Store RDF preview
        state.rdf_preview = result.rdf_output
        state.validation_error = None

        # Log any warnings
        if result.warnings:
            for warning in result.warnings:
                logger.warning(f"Validation warning: {warning}")

        # Add success message
        if result.rdf_output:
            # Show a sample of the output (first 1000 chars)
            preview_sample = result.rdf_output[:1000]
            if len(result.rdf_output) > 1000:
                preview_sample += "\n... (truncated)"

            state.add_message(
                "assistant",
                f"RML validation successful! Here's a preview of the generated RDF:\n\n"
                f"```turtle\n{preview_sample}\n```",
            )
        else:
            state.add_message(
                "assistant",
                "RML syntax validation successful. "
                "(Full RDF preview unavailable - RMLMapper not configured)",
            )

        # Transition to PREVIEW
        state.current_state = FlowState.PREVIEW
        logger.info("Transitioning to PREVIEW state")

    else:
        logger.warning(f"RML validation failed: {result.error_message}")

        # Store validation error
        state.validation_error = result.error_message
        state.rdf_preview = None

        # Add error message to conversation
        state.add_message(
            "assistant",
            f"RML validation failed:\n\n{result.error_message}\n\n"
            "I'll adjust the mapping to fix this issue.",
        )

        # Transition back to REFINE for correction
        state.current_state = FlowState.REFINE
        if state.mapping_proposal:
            state.mapping_proposal.status = "refining"
        logger.info("Validation failed, transitioning to REFINE state")

    return state
