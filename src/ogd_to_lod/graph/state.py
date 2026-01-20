"""LangGraph state schema for the mapping conversation flow."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class FlowState(Enum):
    """Possible states in the conversation flow."""

    INIT = "init"
    ANALYZE = "analyze"
    PROPOSE = "propose"
    REFINE = "refine"
    GENERATE = "generate"
    VALIDATE = "validate"
    PREVIEW = "preview"
    CREATE_PR = "create_pr"
    END = "end"
    ERROR = "error"


class UserIntent(Enum):
    """Detected user intent from their response."""

    CONFIRM = "confirm"
    REJECT = "reject"
    QUESTION = "question"
    OVERRIDE = "override"
    APPROVE = "approve"
    UNKNOWN = "unknown"


@dataclass
class DimensionProposal:
    """Proposed dimension mapping."""

    column: str
    dimension_type: str  # temporal, spatial, categorical
    granularity: str | None = None
    hierarchy: str | None = None


@dataclass
class MeasureProposal:
    """Proposed measure mapping."""

    column: str
    unit: str | None = None
    aggregation: str | None = None


@dataclass
class MappingProposal:
    """Complete mapping proposal from AI."""

    dimensions: list[DimensionProposal] = field(default_factory=list)
    measures: list[MeasureProposal] = field(default_factory=list)
    status: str = "pending"  # pending, approved, refining

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "dimensions": [
                {
                    "column": d.column,
                    "type": d.dimension_type,
                    "granularity": d.granularity,
                    "hierarchy": d.hierarchy,
                }
                for d in self.dimensions
            ],
            "measures": [
                {
                    "column": m.column,
                    "unit": m.unit,
                    "aggregation": m.aggregation,
                }
                for m in self.measures
            ],
            "status": self.status,
        }


@dataclass
class GraphState:
    """State for the LangGraph conversation flow.

    This state is passed between nodes and updated as the conversation progresses.
    """

    # Current state in the flow
    current_state: FlowState = FlowState.INIT

    # Input paths
    csv_path: str | None = None
    dcat_path: str | None = None
    base_uri: str | None = None

    # Parsed data (populated in ANALYZE state)
    csv_schema: dict[str, Any] | None = None
    dcat_metadata: dict[str, Any] | None = None
    parsed_summary: str | None = None

    # Mapping proposal (populated in PROPOSE state)
    mapping_proposal: MappingProposal | None = None
    proposal_text: str | None = None  # AI's explanation

    # User interaction
    user_input: str | None = None
    user_intent: UserIntent = UserIntent.UNKNOWN
    awaiting_user_input: bool = False

    # Generated artifacts (populated in GENERATE state)
    generated_rml: str | None = None
    rdf_preview: str | None = None
    validation_error: str | None = None

    # PR info (populated in CREATE_PR state)
    pr_url: str | None = None
    pr_number: int | None = None

    # Conversation history for context
    messages: list[dict[str, str]] = field(default_factory=list)

    # Error handling
    error_message: str | None = None

    def add_message(self, role: str, content: str) -> None:
        """Add a message to the conversation history."""
        self.messages.append({"role": role, "content": content})

    def get_last_ai_message(self) -> str | None:
        """Get the last AI message from history."""
        for msg in reversed(self.messages):
            if msg["role"] == "assistant":
                return msg["content"]
        return None

    def to_dict(self) -> dict[str, Any]:
        """Convert state to dictionary for serialization."""
        return {
            "current_state": self.current_state.value,
            "csv_path": self.csv_path,
            "dcat_path": self.dcat_path,
            "base_uri": self.base_uri,
            "csv_schema": self.csv_schema,
            "dcat_metadata": self.dcat_metadata,
            "parsed_summary": self.parsed_summary,
            "mapping_proposal": self.mapping_proposal.to_dict() if self.mapping_proposal else None,
            "proposal_text": self.proposal_text,
            "user_input": self.user_input,
            "user_intent": self.user_intent.value,
            "awaiting_user_input": self.awaiting_user_input,
            "generated_rml": self.generated_rml,
            "rdf_preview": self.rdf_preview,
            "validation_error": self.validation_error,
            "pr_url": self.pr_url,
            "pr_number": self.pr_number,
            "messages": self.messages,
            "error_message": self.error_message,
        }
