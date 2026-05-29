"""LangGraph state schema for the mapping conversation flow."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ogd_to_lod.lookup import ReuseContext


class FlowState(Enum):
    """Possible states in the conversation flow."""

    INIT = "init"
    ANALYZE = "analyze"
    LOOKUP = "lookup"
    PROPOSE = "propose"
    REFINE = "refine"
    GENERATE = "generate"
    VALIDATE = "validate"
    CONFIRM_NAME = "confirm_name"
    CONFIRM_PROPOSED_CSV_URL = "confirm_proposed_csv_url"
    ASK_CSV_URL = "ask_csv_url"
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
    datatype: str | None = None  # e.g. xsd:dateTime, xsd:date
    source: str | None = None  # "csv" (default) or "context"
    static_value: str | None = None  # context field name when source is "context"
    context_label: str | None = None  # context field providing label/definition


@dataclass
class MeasureProposal:
    """Proposed measure mapping."""

    column: str
    unit: str | None = None
    aggregation: str | None = None
    context_label: str | None = None  # context field providing label/definition


@dataclass
class MappingProposal:
    """Complete mapping proposal from AI."""

    dimensions: list[DimensionProposal] = field(default_factory=list)
    measures: list[MeasureProposal] = field(default_factory=list)
    skipped_columns: list[str] = field(default_factory=list)
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
                    "datatype": d.datatype,
                    "source": d.source,
                    "static_value": d.static_value,
                    "context_label": d.context_label,
                }
                for d in self.dimensions
            ],
            "measures": [
                {
                    "column": m.column,
                    "unit": m.unit,
                    "aggregation": m.aggregation,
                    "context_label": m.context_label,
                }
                for m in self.measures
            ],
            "skipped_columns": self.skipped_columns,
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
    context_paths: list[str] = field(default_factory=list)
    base_uri: str | None = None
    output_folder: str | None = None

    # When True, results are written to a local folder instead of a GitHub PR
    local_output: bool = False
    # Filesystem path where local results were written (set in CREATE_PR)
    local_output_path: str | None = None

    # Parsed data (populated in ANALYZE state)
    csv_schema: dict[str, Any] | None = None
    dataset_context: dict[str, Any] | None = None
    parsed_summary: str | None = None

    # Vocabulary reuse context (populated in LOOKUP state)
    reuse_context: ReuseContext | None = None

    # Mapping proposal (populated in PROPOSE state)
    mapping_proposal: MappingProposal | None = None
    proposal_text: str | None = None  # AI's explanation

    # User interaction
    user_input: str | None = None
    user_intent: UserIntent = UserIntent.UNKNOWN
    awaiting_user_input: bool = False

    # Generated artifacts (populated in GENERATE state)
    generated_rml: str | None = None
    generated_metadata: str | None = None
    rdf_preview: str | None = None
    validation_error: str | None = None
    validation_retry_count: int = 0

    # AI-generated summary of mapping decisions (populated in PREVIEW state)
    mapping_decisions: str | None = None

    # Mapping name (populated in CONFIRM_NAME state, user-editable)
    mapping_name: str | None = None

    # Source URL (populated in ASK_CSV_URL state)
    csv_source_url: str | None = None
    proposed_csv_source_url: str | None = None

    context_raw_files: list[dict] = field(default_factory=list)  # all context files

    # PR description (populated in PREVIEW state)
    pr_description: str | None = None

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
            "context_paths": self.context_paths,
            "base_uri": self.base_uri,
            "output_folder": self.output_folder,
            "local_output": self.local_output,
            "local_output_path": self.local_output_path,
            "csv_schema": self.csv_schema,
            "dataset_context": self.dataset_context,
            "parsed_summary": self.parsed_summary,
            "reuse_context": self.reuse_context.has_matches() if self.reuse_context else False,
            "mapping_proposal": self.mapping_proposal.to_dict() if self.mapping_proposal else None,
            "proposal_text": self.proposal_text,
            "user_input": self.user_input,
            "user_intent": self.user_intent.value,
            "awaiting_user_input": self.awaiting_user_input,
            "generated_rml": self.generated_rml,
            "generated_metadata": self.generated_metadata,
            "rdf_preview": self.rdf_preview,
            "mapping_decisions": self.mapping_decisions,
            "mapping_name": self.mapping_name,
            "csv_source_url": self.csv_source_url,
            "proposed_csv_source_url": self.proposed_csv_source_url,
            "context_raw_files": self.context_raw_files,
            "pr_description": self.pr_description,
            "validation_error": self.validation_error,
            "validation_retry_count": self.validation_retry_count,
            "pr_url": self.pr_url,
            "pr_number": self.pr_number,
            "messages": self.messages,
            "error_message": self.error_message,
        }
