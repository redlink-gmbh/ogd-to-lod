"""Node functions for the LangGraph conversation flow."""

import re
from typing import Any

import yaml

from ogd_to_lod.ai import AIService
from ogd_to_lod.config import Config
from ogd_to_lod.github import GitHubService, PRCreationError
from ogd_to_lod.logging import get_logger
from ogd_to_lod.parsers import CSVParseError, DCATParseError, parse_csv, parse_dcat
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

    # Ask AI for proposal with explicit YAML format example
    prompt = """Based on the CSV schema and metadata provided, please propose a mapping structure.

Identify:
1. Which columns should be dimensions (and their types: temporal, spatial, or categorical)
2. Which columns should be measures (with units if applicable)
3. Any hierarchies that should be created

Provide your proposal in YAML format following this exact structure:

```yaml
dimensions:
  - column: <column_name>
    type: <temporal|spatial|categorical>
    granularity: <optional: year, month, day, etc.>
    hierarchy: <optional: hierarchy name>
measures:
  - column: <column_name>
    unit: <optional: unit of measurement>
    aggregation: <optional: sum, avg, count, etc.>
```

Important: Use exactly the keys shown above (dimensions, measures, column, type, unit, etc.)."""

    logger.debug("Sending proposal request to AI")
    response = ai_service.send_message(prompt)
    parsed = AIService.parse_response(response)

    # Store response
    state.proposal_text = parsed.text
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
        )
        if measure.column:  # Only add if column name exists
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
            )
            if dim.column:
                proposal.dimensions.append(dim)
        elif current_section == 'measures':
            measure = MeasureProposal(
                column=current_item.get('column', ''),
                unit=current_item.get('unit'),
                aggregation=current_item.get('aggregation'),
            )
            if measure.column:
                proposal.measures.append(measure)
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

        # Skip empty lines
        if not stripped:
            continue

        # Detect list item start
        if stripped.startswith('- '):
            # Save previous item if exists
            save_current_item()
            current_item = {}

            # Parse inline key-value if present (e.g., "- column: year")
            rest = stripped[2:].strip()
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
