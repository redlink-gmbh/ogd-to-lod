"""LangGraph flow definition for the mapping conversation."""

from typing import Any

from langgraph.graph import END, StateGraph

from ogd_to_lod.ai import AIService
from ogd_to_lod.config import Config
from ogd_to_lod.logging import get_logger

from .nodes import (
    MAX_SYNTAX_RETRIES,
    analyze_node,
    confirm_name_node,
    create_pr_node,
    generate_node,
    handle_user_input,
    init_node,
    lookup_node,
    preview_node,
    propose_node,
    regenerate_node,
    syntax_check_node,
    validate_node,
)
from .state import FlowState, GraphState, UserIntent

logger = get_logger(__name__)


class MappingFlow:
    """Orchestrates the mapping conversation flow using LangGraph.

    This class manages the state machine for the CSV to RML mapping process,
    handling state transitions and user interactions.
    """

    def __init__(self, config: Config, ai_service: AIService | None = None):
        """Initialize the mapping flow.

        Args:
            config: Application configuration.
            ai_service: Optional AI service instance. If not provided,
                one will be created from config.
        """
        self._config = config
        self._ai_service = ai_service or AIService(config.azure)
        self._graph = self._build_graph()
        self._state = GraphState()

    def _build_graph(self) -> StateGraph:
        """Build the LangGraph state machine.

        Returns:
            Configured StateGraph instance.
        """
        # Create state graph
        graph = StateGraph(dict)

        # Add nodes
        graph.add_node("init", self._wrap_init)
        graph.add_node("analyze", self._wrap_analyze)
        graph.add_node("lookup", self._wrap_lookup)
        graph.add_node("wait_for_lookup", self._wait_for_lookup)
        graph.add_node("propose", self._wrap_propose)
        graph.add_node("wait_for_input", self._wait_for_input)
        graph.add_node("process_input", self._wrap_process_input)
        graph.add_node("generate", self._wrap_generate)
        graph.add_node("validate", self._wrap_validate)
        graph.add_node("confirm_name", self._wrap_confirm_name)
        graph.add_node("wait_for_name", self._wait_for_name)
        graph.add_node("process_name", self._wrap_process_name)
        graph.add_node("preview", self._wrap_preview)
        graph.add_node("wait_for_pr_confirmation", self._wait_for_pr_confirmation)
        graph.add_node("process_pr_confirmation", self._wrap_process_pr_confirmation)
        graph.add_node("create_pr", self._wrap_create_pr)
        graph.add_node("error", self._handle_error)

        # Set entry point
        graph.set_entry_point("init")

        # Add edges with conditional routing
        graph.add_conditional_edges(
            "init",
            self._route_from_init,
            {
                "analyze": "analyze",
                "error": "error",
            },
        )

        graph.add_conditional_edges(
            "analyze",
            self._route_from_analyze,
            {
                "lookup": "lookup",
                "error": "error",
            },
        )

        graph.add_conditional_edges(
            "lookup",
            self._route_from_lookup,
            {
                "propose": "propose",
                "wait_for_lookup": "wait_for_lookup",
                "error": "error",
            },
        )

        graph.add_edge("propose", "wait_for_input")

        graph.add_conditional_edges(
            "process_input",
            self._route_from_process_input,
            {
                "generate": "generate",  # Transition to generate node
                "refine": "propose",  # Loop back for refinement
                "wait": "wait_for_input",  # Wait for more input
                "error": "error",
            },
        )

        graph.add_conditional_edges(
            "generate",
            self._route_from_generate,
            {
                "validate": "validate",  # Proceed to validation
                "error": "error",
            },
        )

        graph.add_conditional_edges(
            "validate",
            self._route_from_validate,
            {
                "confirm_name": "confirm_name",  # Validation passed, ask for name
                "refine": "propose",  # Validation failed, loop back for refinement
                "error": "error",
            },
        )

        graph.add_edge("confirm_name", "wait_for_name")

        graph.add_conditional_edges(
            "process_name",
            self._route_from_process_name,
            {
                "preview": "preview",  # Name confirmed, show PR preview
                "wait": "wait_for_name",  # Ask again
            },
        )

        graph.add_edge("preview", "wait_for_pr_confirmation")

        graph.add_conditional_edges(
            "process_pr_confirmation",
            self._route_from_pr_confirmation,
            {
                "create_pr": "create_pr",
                "end": END,
                "wait": "wait_for_pr_confirmation",
                "error": "error",
            },
        )

        graph.add_conditional_edges(
            "create_pr",
            self._route_from_create_pr,
            {
                "end": END,
                "error": "error",
            },
        )

        graph.add_edge("error", END)

        return graph.compile()

    def _wrap_init(self, state_dict: dict[str, Any]) -> dict[str, Any]:
        """Wrapper for init node."""
        self._state = init_node(self._state, self._config)
        return self._state.to_dict()

    def _wrap_analyze(self, state_dict: dict[str, Any]) -> dict[str, Any]:
        """Wrapper for analyze node."""
        self._state = analyze_node(self._state, self._config, self._ai_service)
        return self._state.to_dict()

    def _wrap_lookup(self, state_dict: dict[str, Any]) -> dict[str, Any]:
        """Wrapper for lookup node."""
        self._state = lookup_node(self._state, self._config)
        return self._state.to_dict()

    def _wait_for_lookup(self, state_dict: dict[str, Any]) -> dict[str, Any]:
        """Node that waits for user confirmation of vocabulary reuse."""
        self._state.awaiting_user_input = True
        return self._state.to_dict()

    def _wrap_propose(self, state_dict: dict[str, Any]) -> dict[str, Any]:
        """Wrapper for propose node."""
        self._state = propose_node(self._state, self._ai_service)
        return self._state.to_dict()

    def _wait_for_input(self, state_dict: dict[str, Any]) -> dict[str, Any]:
        """Node that waits for user input."""
        self._state.awaiting_user_input = True
        return self._state.to_dict()

    def _wrap_process_input(self, state_dict: dict[str, Any]) -> dict[str, Any]:
        """Wrapper for processing user input."""
        if self._state.user_input:
            self._state = handle_user_input(
                self._state, self._state.user_input, self._ai_service
            )
        return self._state.to_dict()

    def _wrap_generate(self, state_dict: dict[str, Any]) -> dict[str, Any]:
        """Wrapper for generate node."""
        self._state = generate_node(self._state, self._ai_service)
        return self._state.to_dict()

    def _wrap_validate(self, state_dict: dict[str, Any]) -> dict[str, Any]:
        """Wrapper for validate node."""
        # Get RMLMapper configuration from config
        rmlmapper_jar = self._config.rml.rmlmapper_jar
        use_docker = self._config.rml.rmlmapper_use_docker
        yarrrml_parser_image = self._config.rml.yarrrml_parser_docker_image
        self._state = validate_node(
            self._state,
            rmlmapper_jar=rmlmapper_jar,
            use_docker=use_docker,
            yarrrml_parser_docker_image=yarrrml_parser_image,
        )
        return self._state.to_dict()

    def _wrap_confirm_name(self, state_dict: dict[str, Any]) -> dict[str, Any]:
        """Wrapper for confirm_name node."""
        self._state = confirm_name_node(self._state)
        return self._state.to_dict()

    def _wait_for_name(self, state_dict: dict[str, Any]) -> dict[str, Any]:
        """Node that waits for name confirmation input."""
        self._state.awaiting_user_input = True
        return self._state.to_dict()

    def _wrap_process_name(self, state_dict: dict[str, Any]) -> dict[str, Any]:
        """Process the name confirmation input."""
        if self._state.user_input is not None:
            name_input = self._state.user_input.strip()
            if name_input:
                self._state.mapping_name = name_input
                logger.info("User provided custom mapping name: %s", self._state.mapping_name)
            else:
                logger.info("User accepted suggested name: %s", self._state.mapping_name)
            self._state.current_state = FlowState.PREVIEW
        return self._state.to_dict()

    def _wrap_preview(self, state_dict: dict[str, Any]) -> dict[str, Any]:
        """Wrapper for preview node."""
        self._state = preview_node(self._state, self._ai_service)
        return self._state.to_dict()

    def _wait_for_pr_confirmation(self, state_dict: dict[str, Any]) -> dict[str, Any]:
        """Node that waits for PR creation confirmation."""
        self._state.awaiting_user_input = True
        return self._state.to_dict()

    def _wrap_process_pr_confirmation(self, state_dict: dict[str, Any]) -> dict[str, Any]:
        """Wrapper for processing PR confirmation input (yes/no only)."""
        if self._state.user_input:
            user_input_lower = self._state.user_input.lower().strip()
            if user_input_lower in ("yes", "y", "ok", "create", "create pr", "sure", "proceed"):
                self._state.user_intent = UserIntent.APPROVE
                self._state.current_state = FlowState.CREATE_PR
                logger.info("User approved PR creation")
            elif user_input_lower in ("no", "n", "cancel", "skip", "exit", "quit"):
                self._state.user_intent = UserIntent.REJECT
                self._state.current_state = FlowState.END
                logger.info("User cancelled PR creation")
            else:
                # Unrecognised input — prompt again
                self._state.awaiting_user_input = True
                logger.info("Unrecognised PR confirmation input, prompting again")
        return self._state.to_dict()

    def _wrap_create_pr(self, state_dict: dict[str, Any]) -> dict[str, Any]:
        """Wrapper for create PR node."""
        self._state = create_pr_node(self._state, self._config)
        return self._state.to_dict()

    def _handle_error(self, state_dict: dict[str, Any]) -> dict[str, Any]:
        """Handle error state."""
        logger.error(f"Flow error: {self._state.error_message}")
        return self._state.to_dict()

    def _route_from_init(self, state_dict: dict[str, Any]) -> str:
        """Route from INIT state."""
        if self._state.current_state == FlowState.ERROR:
            return "error"
        return "analyze"

    def _route_from_analyze(self, state_dict: dict[str, Any]) -> str:
        """Route from ANALYZE state."""
        if self._state.current_state == FlowState.ERROR:
            return "error"
        return "lookup"

    def _route_from_lookup(self, state_dict: dict[str, Any]) -> str:
        """Route from LOOKUP state."""
        if self._state.current_state == FlowState.ERROR:
            return "error"
        if self._state.awaiting_user_input:
            return "wait_for_lookup"
        return "propose"

    def _route_from_process_input(self, state_dict: dict[str, Any]) -> str:
        """Route based on processed user input."""
        if self._state.current_state == FlowState.ERROR:
            return "error"
        if self._state.current_state == FlowState.GENERATE:
            return "generate"
        if self._state.current_state == FlowState.REFINE:
            return "refine"
        if self._state.awaiting_user_input:
            return "wait"
        return "wait"

    def _route_from_generate(self, state_dict: dict[str, Any]) -> str:
        """Route from GENERATE state."""
        if self._state.current_state == FlowState.ERROR:
            return "error"
        return "validate"

    def _route_from_validate(self, state_dict: dict[str, Any]) -> str:
        """Route from VALIDATE state."""
        if self._state.current_state == FlowState.ERROR:
            return "error"
        if self._state.current_state == FlowState.REFINE:
            return "refine"
        return "confirm_name"

    def _route_from_process_name(self, state_dict: dict[str, Any]) -> str:
        """Route from process_name node."""
        if self._state.current_state == FlowState.PREVIEW:
            return "preview"
        return "wait"

    def _route_from_pr_confirmation(self, state_dict: dict[str, Any]) -> str:
        """Route based on PR confirmation response."""
        if self._state.current_state == FlowState.ERROR:
            return "error"
        if self._state.current_state == FlowState.CREATE_PR:
            return "create_pr"
        if self._state.current_state == FlowState.END:
            return "end"
        if self._state.awaiting_user_input:
            return "wait"
        return "wait"

    def _route_from_create_pr(self, state_dict: dict[str, Any]) -> str:
        """Route from CREATE_PR state."""
        if self._state.current_state == FlowState.ERROR:
            return "error"
        return "end"

    @property
    def state(self) -> GraphState:
        """Get current flow state."""
        return self._state

    @property
    def ai_service(self) -> AIService:
        """Get the AI service instance."""
        return self._ai_service

    def start(
        self,
        csv_path: str,
        context_paths: list[str] | None = None,
        base_uri: str | None = None,
        output_folder: str | None = None,
        local_output: bool = False,
    ) -> GraphState:
        """Start the mapping flow.

        Args:
            csv_path: Path to the CSV file.
            context_paths: Optional list of context files (any format).
            base_uri: Optional base URI for generated resources.
            local_output: When True, write results to a local folder instead
                of opening a GitHub PR.

        Returns:
            Current state after initialization and analysis.
        """
        logger.info(f"Starting mapping flow for {csv_path}")

        # Set initial state
        self._state = GraphState(
            csv_path=csv_path,
            context_paths=context_paths or [],
            base_uri=base_uri,
            output_folder=output_folder,
            local_output=local_output,
        )

        # Run until we need user input
        self._graph.invoke(self._state.to_dict())

        return self._state

    def continue_with_input(self, user_input: str) -> GraphState:
        """Continue the flow with user input.

        Args:
            user_input: User's response.

        Returns:
            Updated state after processing input.
        """
        logger.info("Continuing flow with user input")

        self._state.user_input = user_input
        self._state.awaiting_user_input = False

        # Handle vocabulary reuse confirmation if in LOOKUP state
        if self._state.current_state == FlowState.LOOKUP:
            return self._handle_lookup_confirmation(user_input)

        # Handle name confirmation if in CONFIRM_NAME state
        if self._state.current_state == FlowState.CONFIRM_NAME:
            return self._handle_name_confirmation(user_input)

        # Handle source URL and context inclusion states
        if self._state.current_state == FlowState.ASK_CSV_URL:
            return self._handle_csv_url(user_input)

        # Handle PR confirmation if in PREVIEW state
        if self._state.current_state == FlowState.PREVIEW:
            return self._handle_pr_confirmation(user_input)

        # Process input for proposal states
        self._state = handle_user_input(self._state, user_input, self._ai_service)

        # Handle state transitions based on user intent
        if self._state.current_state == FlowState.GENERATE:
            # User approved - generate RML
            logger.info("User approved mapping, generating RML")
            self._state = generate_node(self._state, self._ai_service)

            if self._state.current_state != FlowState.ERROR:
                # Run Tier 1 (syntax) with auto-retry, then Tier 2 (RMLMapper)
                self._run_tier1_with_retries()

                # If Tier 1 passed, run Tier 2 (RMLMapper)
                if self._state.current_state == FlowState.VALIDATE:
                    logger.info("Tier 1 passed, running Tier 2 (RMLMapper)")
                    self._state = validate_node(
                        self._state,
                        rmlmapper_jar=self._config.rml.rmlmapper_jar,
                        use_docker=self._config.rml.rmlmapper_use_docker,
                        yarrrml_parser_docker_image=self._config.rml.yarrrml_parser_docker_image,
                    )

                # If all validation passed, move to CONFIRM_NAME
                if self._state.current_state == FlowState.PREVIEW:
                    self._state = confirm_name_node(self._state)

                # If Tier 2 failed, escalate to user via REFINE → PROPOSE
                elif self._state.current_state == FlowState.REFINE:
                    logger.info("Validation failed, refining mapping")
                    self._state = propose_node(self._state, self._ai_service)

        elif self._state.current_state == FlowState.REFINE:
            # User wants changes - loop back to propose
            self._state.current_state = FlowState.PROPOSE
            self._state = propose_node(self._state, self._ai_service)

        return self._state

    def _run_tier1_with_retries(self) -> None:
        """Run Tier 1 syntax validation with automatic retries.

        On syntax failure, re-runs generate_node with the error in context
        up to MAX_SYNTAX_RETRIES times. If all retries are exhausted,
        escalates to the user via REFINE → PROPOSE.

        Mutates self._state in place.
        """
        self._state.validation_retry_count = 0

        while self._state.validation_retry_count < MAX_SYNTAX_RETRIES:
            self._state = syntax_check_node(self._state)

            if self._state.current_state == FlowState.VALIDATE:
                # Syntax check passed — proceed to Tier 2
                logger.info(
                    f"Tier 1 passed on attempt "
                    f"{self._state.validation_retry_count + 1}"
                )
                return

            # Syntax check failed — increment and retry
            self._state.validation_retry_count += 1
            logger.warning(
                f"Tier 1 syntax check failed, retry "
                f"{self._state.validation_retry_count}/{MAX_SYNTAX_RETRIES}"
            )

            if self._state.validation_retry_count < MAX_SYNTAX_RETRIES:
                # Re-generate with error context
                self._state.current_state = FlowState.GENERATE
                self._state = regenerate_node(self._state, self._ai_service)

                if self._state.current_state == FlowState.ERROR:
                    return

        # Max retries exhausted — escalate to user
        logger.warning(
            f"Tier 1 failed after {MAX_SYNTAX_RETRIES} attempts, "
            "escalating to user"
        )
        self._state.add_message(
            "assistant",
            f"I was unable to fix the syntax error after "
            f"{MAX_SYNTAX_RETRIES} attempts. "
            f"Please help me resolve this issue:\n\n"
            f"{self._state.validation_error}",
        )
        self._state.current_state = FlowState.REFINE
        if self._state.mapping_proposal:
            self._state.mapping_proposal.status = "refining"

    def _handle_lookup_confirmation(self, user_input: str) -> GraphState:
        """Handle user yes/no confirmation of vocabulary reuse from SPARQL.

        'yes' / 'y' — keep the ReuseContext and proceed to PROPOSE.
        'no' / 'n' / 'skip' — clear the ReuseContext (use fresh URIs) and proceed.
        Anything else — prompt again.

        Args:
            user_input: User's response.

        Returns:
            Updated state.
        """
        self._state.add_message("user", user_input)
        answer = user_input.lower().strip()

        if answer in ("yes", "y", "ja", "ok", "sure", "reuse"):
            logger.info("User accepted vocabulary reuse from SPARQL")
            self._state.add_message(
                "assistant",
                "Great, I will reuse the existing vocabulary URIs in the mapping.",
            )
            self._state.awaiting_user_input = False
            self._state.current_state = FlowState.PROPOSE
            self._state = propose_node(self._state, self._ai_service)
        elif answer in ("no", "n", "nein", "skip", "fresh", "new"):
            logger.info("User rejected vocabulary reuse — clearing reuse context")
            from ogd_to_lod.lookup import ReuseContext
            self._state.reuse_context = ReuseContext()
            self._state.add_message(
                "assistant",
                "Understood, I will generate fresh URIs for all properties and code values.",
            )
            self._state.awaiting_user_input = False
            self._state.current_state = FlowState.PROPOSE
            self._state = propose_node(self._state, self._ai_service)
        else:
            self._state.awaiting_user_input = True
            self._state.add_message(
                "assistant",
                "Please answer with 'yes' to reuse the existing URIs or 'no' to generate fresh ones.",
            )

        return self._state

    def _handle_name_confirmation(self, user_input: str) -> GraphState:
        """Handle user confirmation of the mapping name.

        Empty input accepts the suggested name; non-empty input overrides it.
        Then transitions to ASK_CSV_URL to collect source URLs.

        Args:
            user_input: User's response (empty = accept, non-empty = override).

        Returns:
            Updated state after processing name confirmation.
        """
        self._state.add_message("user", user_input)

        name_input = user_input.strip()
        if name_input:
            self._state.mapping_name = name_input
            logger.info("User provided custom mapping name: %s", self._state.mapping_name)
        else:
            logger.info("User accepted suggested name: %s", self._state.mapping_name)

        # Transition to ASK_CSV_URL
        self._state.current_state = FlowState.ASK_CSV_URL
        self._state.awaiting_user_input = True
        self._state.add_message(
            "assistant",
            "Enter the public URL for the CSV source (or press Enter to skip):",
        )

        return self._state

    def _handle_pr_confirmation(self, user_input: str) -> GraphState:
        """Handle user confirmation for PR creation (yes/no only).

        Args:
            user_input: User's response.

        Returns:
            Updated state after processing PR confirmation.
        """
        user_input_lower = user_input.lower().strip()
        self._state.add_message("user", user_input)

        if user_input_lower in ("yes", "y", "ok", "create", "create pr", "sure", "proceed"):
            self._state.user_intent = UserIntent.APPROVE
            logger.info("User approved PR creation")
            self._state = create_pr_node(self._state, self._config)
        elif user_input_lower in ("no", "n", "cancel", "skip", "exit", "quit"):
            self._state.user_intent = UserIntent.REJECT
            self._state.current_state = FlowState.END
            self._state.add_message(
                "assistant",
                "PR creation cancelled. RML mapping has been generated but not committed."
            )
            logger.info("User cancelled PR creation")
        else:
            # Unrecognised input — prompt again
            self._state.awaiting_user_input = True
            logger.info("Unrecognised PR confirmation input, prompting again")

        return self._state

    def _handle_csv_url(self, user_input: str) -> GraphState:
        """Handle CSV source URL input.

        Empty input skips; non-empty stores the URL. Transitions to
        ASK_DCAT_URL if a DCAT path was provided, otherwise to PREVIEW.

        Args:
            user_input: User's response.

        Returns:
            Updated state.
        """
        self._state.add_message("user", user_input)
        url = user_input.strip()
        if url:
            self._state.csv_source_url = url
            logger.info("User provided CSV source URL: %s", url)
        else:
            logger.info("User skipped CSV source URL")

        # Go straight to preview
        self._state = preview_node(self._state, self._ai_service)

        return self._state

    def get_proposal_text(self) -> str | None:
        """Get the current proposal text for display."""
        return self._state.proposal_text

    def get_parsed_summary(self) -> str | None:
        """Get the parsed data summary for display."""
        return self._state.parsed_summary

    def is_awaiting_input(self) -> bool:
        """Check if flow is waiting for user input."""
        return self._state.awaiting_user_input

    def is_complete(self) -> bool:
        """Check if flow has completed."""
        return self._state.current_state in (FlowState.END, FlowState.ERROR)

    def is_approved(self) -> bool:
        """Check if mapping has been approved."""
        return (
            self._state.mapping_proposal is not None
            and self._state.mapping_proposal.status == "approved"
        )

    def get_generated_rml(self) -> str | None:
        """Get the generated RML Turtle content."""
        return self._state.generated_rml

    def has_generated_rml(self) -> bool:
        """Check if RML has been generated."""
        return self._state.generated_rml is not None

    def is_validated(self) -> bool:
        """Check if RML has been successfully validated."""
        return (
            self._state.current_state == FlowState.PREVIEW
            and self._state.validation_error is None
        )

    def get_validation_error(self) -> str | None:
        """Get the validation error message if validation failed."""
        return self._state.validation_error

    def get_rdf_preview(self) -> str | None:
        """Get the RDF preview generated from validation."""
        return self._state.rdf_preview

    def has_rdf_preview(self) -> bool:
        """Check if an RDF preview is available."""
        return self._state.rdf_preview is not None

    def is_awaiting_lookup_confirmation(self) -> bool:
        """Check if flow is waiting for vocabulary reuse confirmation."""
        return (
            self._state.current_state == FlowState.LOOKUP
            and self._state.awaiting_user_input
        )

    def is_awaiting_name_confirmation(self) -> bool:
        """Check if flow is waiting for mapping name confirmation."""
        return (
            self._state.current_state == FlowState.CONFIRM_NAME
            and self._state.awaiting_user_input
        )

    def is_awaiting_pr_confirmation(self) -> bool:
        """Check if flow is waiting for PR creation confirmation."""
        return (
            self._state.current_state == FlowState.PREVIEW
            and self._state.awaiting_user_input
        )

    def is_awaiting_csv_url(self) -> bool:
        """Check if flow is waiting for CSV source URL input."""
        return (
            self._state.current_state == FlowState.ASK_CSV_URL
            and self._state.awaiting_user_input
        )

    def get_pr_description(self) -> str | None:
        """Get the built PR description."""
        return self._state.pr_description

    def has_created_pr(self) -> bool:
        """Check if PR has been created."""
        return self._state.pr_url is not None

    def is_local_output(self) -> bool:
        """Check if the flow is running in local-output mode."""
        return self._state.local_output

    def has_local_output(self) -> bool:
        """Check if local-output files have been written."""
        return self._state.local_output_path is not None

    def get_local_output_path(self) -> str | None:
        """Get the folder where local-output files were written."""
        return self._state.local_output_path

    def get_pr_url(self) -> str | None:
        """Get the URL of the created PR."""
        return self._state.pr_url

    def get_pr_number(self) -> int | None:
        """Get the number of the created PR."""
        return self._state.pr_number

    def reset_request_count(self) -> None:
        """Reset the AI service request counter to zero.

        This allows continuing with more requests after reaching the limit.
        """
        self._ai_service.reset_request_count()
        logger.info("AI service request counter reset")
