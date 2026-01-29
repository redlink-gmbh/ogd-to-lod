"""LangGraph flow definition for the mapping conversation."""

from typing import Any

from langgraph.graph import END, StateGraph

from ogd_to_lod.ai import AIService
from ogd_to_lod.config import Config
from ogd_to_lod.logging import get_logger

from .nodes import (
    analyze_node,
    create_pr_node,
    generate_node,
    handle_user_input,
    init_node,
    preview_node,
    propose_node,
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
        graph.add_node("propose", self._wrap_propose)
        graph.add_node("wait_for_input", self._wait_for_input)
        graph.add_node("process_input", self._wrap_process_input)
        graph.add_node("generate", self._wrap_generate)
        graph.add_node("validate", self._wrap_validate)
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
                "propose": "propose",
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
                "preview": "preview",  # Validation passed, proceed to preview
                "refine": "propose",  # Validation failed, loop back for refinement
                "error": "error",
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
        self._state = analyze_node(self._state, self._config)
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
        self._state = validate_node(
            self._state,
            rmlmapper_jar=rmlmapper_jar,
            use_docker=use_docker,
        )
        return self._state.to_dict()

    def _wrap_preview(self, state_dict: dict[str, Any]) -> dict[str, Any]:
        """Wrapper for preview node."""
        self._state = preview_node(self._state)
        return self._state.to_dict()

    def _wait_for_pr_confirmation(self, state_dict: dict[str, Any]) -> dict[str, Any]:
        """Node that waits for PR creation confirmation."""
        self._state.awaiting_user_input = True
        return self._state.to_dict()

    def _wrap_process_pr_confirmation(self, state_dict: dict[str, Any]) -> dict[str, Any]:
        """Wrapper for processing PR confirmation input."""
        if self._state.user_input:
            user_input = self._state.user_input.lower().strip()
            if user_input in ("yes", "y", "ok", "create", "create pr", "sure", "proceed"):
                self._state.user_intent = UserIntent.APPROVE
                self._state.current_state = FlowState.CREATE_PR
                logger.info("User approved PR creation")
            elif user_input in ("no", "n", "cancel", "skip", "exit", "quit"):
                self._state.user_intent = UserIntent.REJECT
                self._state.current_state = FlowState.END
                logger.info("User cancelled PR creation")
            else:
                # Unknown response, ask again
                self._state.awaiting_user_input = True
                logger.info("Unknown response, awaiting clarification")
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
        return "preview"

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
        dcat_path: str | None = None,
        base_uri: str | None = None,
    ) -> GraphState:
        """Start the mapping flow.

        Args:
            csv_path: Path to the CSV file.
            dcat_path: Optional path to DCAT metadata file.
            base_uri: Optional base URI for generated resources.

        Returns:
            Current state after initialization and analysis.
        """
        logger.info(f"Starting mapping flow for {csv_path}")

        # Set initial state
        self._state = GraphState(
            csv_path=csv_path,
            dcat_path=dcat_path,
            base_uri=base_uri,
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

            # Validate the generated RML
            if self._state.current_state != FlowState.ERROR:
                logger.info("Validating generated RML")
                self._state = validate_node(
                    self._state,
                    rmlmapper_jar=self._config.rml.rmlmapper_jar,
                    use_docker=self._config.rml.rmlmapper_use_docker,
                )

                # If validation passed, move to PREVIEW
                if self._state.current_state == FlowState.PREVIEW:
                    self._state = preview_node(self._state)

                # If validation failed, loop back to propose with error context
                elif self._state.current_state == FlowState.REFINE:
                    logger.info("Validation failed, refining mapping")
                    self._state = propose_node(self._state, self._ai_service)

        elif self._state.current_state == FlowState.REFINE:
            # User wants changes - loop back to propose
            self._state.current_state = FlowState.PROPOSE
            self._state = propose_node(self._state, self._ai_service)

        return self._state

    def _handle_pr_confirmation(self, user_input: str) -> GraphState:
        """Handle user confirmation for PR creation.

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
            # Unknown response, ask for clarification
            self._state.awaiting_user_input = True
            self._state.add_message(
                "assistant",
                "Please respond with 'yes' to create a PR or 'no' to skip PR creation."
            )
            logger.info("Unknown response, awaiting clarification")

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
        return self._state.current_state in (FlowState.END, FlowState.ERROR, FlowState.PREVIEW)

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

    def is_awaiting_pr_confirmation(self) -> bool:
        """Check if flow is waiting for PR creation confirmation."""
        return (
            self._state.current_state == FlowState.PREVIEW
            and self._state.awaiting_user_input
        )

    def has_created_pr(self) -> bool:
        """Check if PR has been created."""
        return self._state.pr_url is not None

    def get_pr_url(self) -> str | None:
        """Get the URL of the created PR."""
        return self._state.pr_url

    def get_pr_number(self) -> int | None:
        """Get the number of the created PR."""
        return self._state.pr_number
