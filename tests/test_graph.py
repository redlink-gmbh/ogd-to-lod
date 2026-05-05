"""Tests for the LangGraph conversation flow."""

import pytest
from unittest.mock import MagicMock, patch

from ogd_to_lod.config import Config, GitHubConfig, AzureOpenAIConfig, RMLConfig
from ogd_to_lod.graph.state import (
    DimensionProposal,
    FlowState,
    GraphState,
    MappingProposal,
    MeasureProposal,
    UserIntent,
)
from ogd_to_lod.graph.nodes import (
    init_node,
    analyze_node,
    propose_node,
    confirm_name_node,
    preview_node,
    create_pr_node,
    handle_user_input,
    suggest_mapping_name,
    _build_pr_description,
    _build_summary,
    _build_ai_context,
    _parse_proposal,
    _robust_parse_yaml_proposal,
    _fix_common_yaml_issues,
    _parse_yaml_line_by_line,
    _extract_proposal_from_text,
)
from ogd_to_lod.graph.flow import MappingFlow


@pytest.fixture
def mock_config():
    """Create a mock configuration."""
    return Config(
        github=GitHubConfig(repo="test/repo", token="test-token"),
        azure=AzureOpenAIConfig(
            endpoint="https://test.openai.azure.com/",
            api_key="test-key",
            deployment="gpt-4",
        ),
        rml=RMLConfig(base_uri="https://example.org/"),
    )


@pytest.fixture
def mock_ai_service():
    """Create a mock AI service."""
    service = MagicMock()
    service.send_message.return_value = """Based on the data, I suggest:

**Dimensions:**
- `year` - temporal dimension
- `region` - spatial dimension

**Measures:**
- `value` - count measure

```yaml
dimensions:
  - column: year
    type: temporal
    granularity: year
  - column: region
    type: spatial
measures:
  - column: value
    unit: count
```
"""
    service.parse_response = MagicMock(side_effect=lambda x: MagicMock(
        text="Based on the data...",
        code_blocks=[MagicMock(language="yaml", content="""dimensions:
  - column: year
    type: temporal
    granularity: year
  - column: region
    type: spatial
measures:
  - column: value
    unit: count""")],
        get_yaml_blocks=MagicMock(return_value=["""dimensions:
  - column: year
    type: temporal
    granularity: year
  - column: region
    type: spatial
measures:
  - column: value
    unit: count"""]),
    ))
    return service


class TestGraphState:
    """Tests for GraphState dataclass."""

    def test_initial_state(self):
        """Test default state initialization."""
        state = GraphState()
        assert state.current_state == FlowState.INIT
        assert state.csv_path is None
        assert state.messages == []
        assert state.awaiting_user_input is False

    def test_add_message(self):
        """Test adding messages to history."""
        state = GraphState()
        state.add_message("user", "Hello")
        state.add_message("assistant", "Hi there!")

        assert len(state.messages) == 2
        assert state.messages[0] == {"role": "user", "content": "Hello"}
        assert state.messages[1] == {"role": "assistant", "content": "Hi there!"}

    def test_get_last_ai_message(self):
        """Test getting last AI message."""
        state = GraphState()
        state.add_message("user", "Question")
        state.add_message("assistant", "First answer")
        state.add_message("user", "Follow up")
        state.add_message("assistant", "Second answer")

        assert state.get_last_ai_message() == "Second answer"

    def test_get_last_ai_message_empty(self):
        """Test getting last AI message when none exist."""
        state = GraphState()
        state.add_message("user", "Question")
        assert state.get_last_ai_message() is None

    def test_to_dict(self):
        """Test state serialization."""
        state = GraphState(
            csv_path="/path/to/file.csv",
            base_uri="https://example.org/",
        )
        result = state.to_dict()

        assert result["csv_path"] == "/path/to/file.csv"
        assert result["base_uri"] == "https://example.org/"
        assert result["current_state"] == "init"


class TestMappingProposal:
    """Tests for MappingProposal dataclass."""

    def test_empty_proposal(self):
        """Test empty proposal creation."""
        proposal = MappingProposal()
        assert proposal.dimensions == []
        assert proposal.measures == []
        assert proposal.status == "pending"

    def test_proposal_with_data(self):
        """Test proposal with dimensions and measures."""
        proposal = MappingProposal(
            dimensions=[
                DimensionProposal(column="year", dimension_type="temporal"),
                DimensionProposal(column="region", dimension_type="spatial"),
            ],
            measures=[
                MeasureProposal(column="value", unit="count"),
            ],
        )

        assert len(proposal.dimensions) == 2
        assert len(proposal.measures) == 1
        assert proposal.dimensions[0].column == "year"

    def test_proposal_to_dict(self):
        """Test proposal serialization."""
        proposal = MappingProposal(
            dimensions=[
                DimensionProposal(column="year", dimension_type="temporal", granularity="year"),
            ],
            measures=[
                MeasureProposal(column="value", unit="count"),
            ],
            status="approved",
        )

        result = proposal.to_dict()

        assert len(result["dimensions"]) == 1
        assert result["dimensions"][0]["column"] == "year"
        assert result["dimensions"][0]["type"] == "temporal"
        assert result["status"] == "approved"


class TestInitNode:
    """Tests for init_node function."""

    def test_init_with_valid_csv(self, mock_config):
        """Test init with valid CSV path."""
        state = GraphState(csv_path="/path/to/file.csv")
        result = init_node(state, mock_config)

        assert result.current_state == FlowState.ANALYZE
        assert result.base_uri == "https://example.org/"

    def test_init_without_csv(self, mock_config):
        """Test init without CSV path."""
        state = GraphState()
        result = init_node(state, mock_config)

        assert result.current_state == FlowState.ERROR
        assert "CSV path is required" in result.error_message

    def test_init_with_custom_base_uri(self, mock_config):
        """Test init preserves custom base URI."""
        state = GraphState(
            csv_path="/path/to/file.csv",
            base_uri="https://custom.org/",
        )
        result = init_node(state, mock_config)

        assert result.base_uri == "https://custom.org/"


class TestAnalyzeNode:
    """Tests for analyze_node function."""

    @patch("ogd_to_lod.graph.nodes.parse_csv")
    def test_analyze_csv_success(self, mock_parse_csv, mock_config):
        """Test successful CSV analysis."""
        from ogd_to_lod.parsers.models import CSVData, ColumnInfo, ColumnType

        mock_parse_csv.return_value = CSVData(
            source="/path/to/file.csv",
            columns=[
                ColumnInfo(name="year", detected_type=ColumnType.INTEGER, sample_values=[2020, 2021]),
                ColumnInfo(name="value", detected_type=ColumnType.FLOAT, sample_values=[1.5, 2.5]),
            ],
            sample_rows=[{"year": 2020, "value": 1.5}],
            total_rows=100,
        )

        state = GraphState(csv_path="/path/to/file.csv")
        state.current_state = FlowState.ANALYZE

        result = analyze_node(state, mock_config)

        assert result.current_state == FlowState.LOOKUP
        assert result.csv_schema is not None
        assert len(result.csv_schema["columns"]) == 2
        assert result.parsed_summary is not None

    @patch("ogd_to_lod.graph.nodes.parse_csv")
    def test_analyze_csv_includes_delimiter(self, mock_parse_csv, mock_config):
        """Test that analyze_node propagates delimiter to csv_schema."""
        from ogd_to_lod.parsers.models import CSVData, ColumnInfo, ColumnType

        mock_parse_csv.return_value = CSVData(
            source="/path/to/file.csv",
            columns=[
                ColumnInfo(name="year", detected_type=ColumnType.INTEGER, sample_values=[2020]),
            ],
            sample_rows=[{"year": 2020}],
            total_rows=10,
            delimiter=";",
        )

        state = GraphState(csv_path="/path/to/file.csv")
        state.current_state = FlowState.ANALYZE

        result = analyze_node(state, mock_config)

        assert result.csv_schema["delimiter"] == ";"

    @patch("ogd_to_lod.graph.nodes.parse_csv")
    def test_analyze_csv_comma_delimiter(self, mock_parse_csv, mock_config):
        """Test that analyze_node propagates comma delimiter (default)."""
        from ogd_to_lod.parsers.models import CSVData, ColumnInfo, ColumnType

        mock_parse_csv.return_value = CSVData(
            source="/path/to/file.csv",
            columns=[
                ColumnInfo(name="year", detected_type=ColumnType.INTEGER, sample_values=[2020]),
            ],
            sample_rows=[{"year": 2020}],
            total_rows=10,
        )

        state = GraphState(csv_path="/path/to/file.csv")
        state.current_state = FlowState.ANALYZE

        result = analyze_node(state, mock_config)

        assert result.csv_schema["delimiter"] == ","

    @patch("ogd_to_lod.graph.nodes.parse_csv")
    def test_analyze_csv_failure(self, mock_parse_csv, mock_config):
        """Test CSV analysis failure."""
        from ogd_to_lod.parsers import CSVParseError

        mock_parse_csv.side_effect = CSVParseError("Invalid CSV")

        state = GraphState(csv_path="/invalid/file.csv")
        state.current_state = FlowState.ANALYZE

        result = analyze_node(state, mock_config)

        assert result.current_state == FlowState.ERROR
        assert "Failed to parse CSV" in result.error_message


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_build_summary_with_csv(self):
        """Test summary building with CSV data."""
        csv_schema = {
            "source": "/path/to/file.csv",
            "columns": [
                {"name": "year", "type": "int", "samples": [2020, 2021]},
                {"name": "value", "type": "float", "samples": [1.5, 2.5]},
            ],
            "total_rows": 100,
        }

        summary = _build_summary(csv_schema, None)

        assert "CSV Schema" in summary
        assert "year" in summary
        assert "value" in summary
        assert "100" in summary

    def test_build_summary_with_dcat(self):
        """Test summary building with dataset context."""
        dataset_context = {
            "title": "Test Dataset",
            "description": "A test dataset for testing",
            "publisher": "Test Org",
            "keywords": ["test", "data"],
            "column_contexts": {},
        }

        summary = _build_summary(None, dataset_context)

        assert "Dataset Context" in summary
        assert "Test Dataset" in summary
        assert "Test Org" in summary

    def test_build_ai_context_includes_delimiter(self):
        """Test that _build_ai_context includes delimiter info."""
        state = GraphState(
            base_uri="https://example.org/",
            csv_schema={
                "columns": [{"name": "year", "type": "int", "samples": [2020]}],
                "total_rows": 10,
                "sample_rows": [],
                "delimiter": ";",
            },
        )

        context = _build_ai_context(state)

        assert "Delimiter: ';'" in context

    def test_build_ai_context_default_delimiter(self):
        """Test that _build_ai_context defaults to comma when delimiter absent."""
        state = GraphState(
            base_uri="https://example.org/",
            csv_schema={
                "columns": [{"name": "year", "type": "int", "samples": [2020]}],
                "total_rows": 10,
                "sample_rows": [],
            },
        )

        context = _build_ai_context(state)

        assert "Delimiter: ','" in context

    def test_parse_proposal(self):
        """Test parsing YAML proposal data."""
        data = {
            "dimensions": [
                {"column": "year", "type": "temporal", "granularity": "year"},
                {"column": "region", "type": "spatial"},
            ],
            "measures": [
                {"column": "value", "unit": "count"},
            ],
        }

        proposal = _parse_proposal(data)

        assert len(proposal.dimensions) == 2
        assert len(proposal.measures) == 1
        assert proposal.dimensions[0].column == "year"
        assert proposal.dimensions[0].dimension_type == "temporal"
        assert proposal.measures[0].unit == "count"


class TestUserIntentHandling:
    """Tests for user intent handling."""

    def test_approve_intent(self, mock_ai_service):
        """Test handling approval intent."""
        mock_ai_service.send_message.return_value = "APPROVE"

        state = GraphState(
            current_state=FlowState.PROPOSE,
            mapping_proposal=MappingProposal(),
        )

        result = handle_user_input(state, "looks good", mock_ai_service)

        assert result.user_intent == UserIntent.APPROVE
        assert result.current_state == FlowState.GENERATE
        assert result.mapping_proposal.status == "approved"

    def test_override_intent(self, mock_ai_service):
        """Test handling override intent."""
        mock_ai_service.send_message.return_value = "OVERRIDE"

        state = GraphState(
            current_state=FlowState.PROPOSE,
            mapping_proposal=MappingProposal(),
        )

        result = handle_user_input(state, "change year to categorical", mock_ai_service)

        assert result.user_intent == UserIntent.OVERRIDE
        assert result.current_state == FlowState.REFINE
        assert result.mapping_proposal.status == "refining"

    def test_question_intent(self, mock_ai_service):
        """Test handling question intent."""
        mock_ai_service.send_message.return_value = "QUESTION"

        state = GraphState(
            current_state=FlowState.PROPOSE,
            mapping_proposal=MappingProposal(),
        )

        result = handle_user_input(state, "why did you choose temporal?", mock_ai_service)

        assert result.user_intent == UserIntent.QUESTION
        assert result.current_state == FlowState.REFINE


class TestMappingFlow:
    """Tests for MappingFlow class."""

    @patch("ogd_to_lod.graph.flow.analyze_node")
    @patch("ogd_to_lod.graph.flow.init_node")
    def test_flow_initialization(self, mock_init, mock_analyze, mock_config, mock_ai_service):
        """Test flow initialization."""
        mock_init.return_value = GraphState(current_state=FlowState.ANALYZE)
        mock_analyze.return_value = GraphState(current_state=FlowState.PROPOSE)

        flow = MappingFlow(mock_config, mock_ai_service)

        assert flow.state.current_state == FlowState.INIT
        assert flow.ai_service == mock_ai_service

    def test_is_awaiting_input(self, mock_config, mock_ai_service):
        """Test is_awaiting_input method."""
        flow = MappingFlow(mock_config, mock_ai_service)
        flow._state.awaiting_user_input = True

        assert flow.is_awaiting_input() is True

    def test_is_complete(self, mock_config, mock_ai_service):
        """Test is_complete method."""
        flow = MappingFlow(mock_config, mock_ai_service)

        assert flow.is_complete() is False

        flow._state.current_state = FlowState.END
        assert flow.is_complete() is True

        flow._state.current_state = FlowState.ERROR
        assert flow.is_complete() is True

        # PREVIEW is NOT complete — still awaiting PR confirmation
        flow._state.current_state = FlowState.PREVIEW
        assert flow.is_complete() is False

        # CONFIRM_NAME is NOT complete — still awaiting name confirmation
        flow._state.current_state = FlowState.CONFIRM_NAME
        assert flow.is_complete() is False

    def test_is_approved(self, mock_config, mock_ai_service):
        """Test is_approved method."""
        flow = MappingFlow(mock_config, mock_ai_service)

        assert flow.is_approved() is False

        flow._state.mapping_proposal = MappingProposal(status="approved")
        assert flow.is_approved() is True


class TestRobustYAMLParsing:
    """Tests for robust YAML parsing functions."""

    def test_parse_proposal_alternative_keys(self):
        """Test _parse_proposal with alternative key names."""
        # Using 'name' instead of 'column'
        data = {
            "dimensions": [
                {"name": "year", "type": "temporal"},
            ],
            "measures": [
                {"name": "value", "unit": "count"},
            ],
        }
        proposal = _parse_proposal(data)
        assert len(proposal.dimensions) == 1
        assert proposal.dimensions[0].column == "year"
        assert len(proposal.measures) == 1
        assert proposal.measures[0].column == "value"

    def test_parse_proposal_dims_and_metrics_keys(self):
        """Test _parse_proposal with 'dims' and 'metrics' keys."""
        data = {
            "dims": [
                {"col": "year", "kind": "temporal"},
            ],
            "metrics": [
                {"field": "value"},
            ],
        }
        proposal = _parse_proposal(data)
        assert len(proposal.dimensions) == 1
        assert proposal.dimensions[0].column == "year"
        assert proposal.dimensions[0].dimension_type == "temporal"
        assert len(proposal.measures) == 1
        assert proposal.measures[0].column == "value"

    def test_parse_proposal_uppercase_keys(self):
        """Test _parse_proposal with uppercase keys."""
        data = {
            "Dimensions": [
                {"column": "year", "type": "temporal"},
            ],
            "Measures": [
                {"column": "value"},
            ],
        }
        proposal = _parse_proposal(data)
        assert len(proposal.dimensions) == 1
        assert len(proposal.measures) == 1

    def test_parse_proposal_skips_invalid_items(self):
        """Test _parse_proposal skips non-dict items."""
        data = {
            "dimensions": [
                {"column": "year", "type": "temporal"},
                "invalid string item",
                None,
                {"column": "region", "type": "spatial"},
            ],
        }
        proposal = _parse_proposal(data)
        assert len(proposal.dimensions) == 2

    def test_parse_proposal_skips_empty_columns(self):
        """Test _parse_proposal skips items without column names."""
        data = {
            "dimensions": [
                {"column": "year", "type": "temporal"},
                {"type": "spatial"},  # No column name
                {"column": "", "type": "categorical"},  # Empty column name
            ],
        }
        proposal = _parse_proposal(data)
        assert len(proposal.dimensions) == 1

    def test_robust_parse_yaml_standard(self):
        """Test _robust_parse_yaml_proposal with standard YAML."""
        yaml_content = """dimensions:
  - column: year
    type: temporal
measures:
  - column: value
    unit: count"""
        proposal = _robust_parse_yaml_proposal(yaml_content)
        assert proposal is not None
        assert len(proposal.dimensions) == 1
        assert len(proposal.measures) == 1

    def test_robust_parse_yaml_with_trailing_commas(self):
        """Test _robust_parse_yaml_proposal fixes trailing commas."""
        yaml_content = """dimensions:
  - column: year,
    type: temporal,
measures:
  - column: value,"""
        proposal = _robust_parse_yaml_proposal(yaml_content)
        assert proposal is not None
        assert len(proposal.dimensions) == 1
        assert len(proposal.measures) == 1

    def test_robust_parse_yaml_with_smart_quotes(self):
        """Test _robust_parse_yaml_proposal fixes smart quotes."""
        yaml_content = """dimensions:
  - column: "year"
    type: 'temporal'"""
        proposal = _robust_parse_yaml_proposal(yaml_content)
        assert proposal is not None
        assert len(proposal.dimensions) == 1

    def test_fix_common_yaml_issues_trailing_commas(self):
        """Test _fix_common_yaml_issues removes trailing commas."""
        content = "key: value,\nother: data,"
        fixed = _fix_common_yaml_issues(content)
        assert ",\n" not in fixed
        assert "value\n" in fixed

    def test_fix_common_yaml_issues_smart_quotes(self):
        """Test _fix_common_yaml_issues replaces smart quotes."""
        content = '\u201csmart\u201d and \u2018single\u2019'
        fixed = _fix_common_yaml_issues(content)
        # After fixing, smart quotes should become regular quotes
        assert '\u201c' not in fixed  # left double quote removed
        assert '\u201d' not in fixed  # right double quote removed
        assert '\u2018' not in fixed  # left single quote removed
        assert '\u2019' not in fixed  # right single quote removed

    def test_fix_common_yaml_issues_tabs(self):
        """Test _fix_common_yaml_issues converts tabs to spaces."""
        content = "key:\n\tvalue"
        fixed = _fix_common_yaml_issues(content)
        assert "\t" not in fixed
        assert "  value" in fixed

    def test_fix_common_yaml_issues_missing_space_after_colon(self):
        """Test _fix_common_yaml_issues adds space after colon."""
        content = "key:value"
        fixed = _fix_common_yaml_issues(content)
        assert "key: value" in fixed

    def test_parse_yaml_line_by_line_basic(self):
        """Test _parse_yaml_line_by_line with basic structure."""
        yaml_content = """dimensions:
  - column: year
    type: temporal
  - column: region
    type: spatial
measures:
  - column: value
    unit: count"""
        proposal = _parse_yaml_line_by_line(yaml_content)
        assert proposal is not None
        assert len(proposal.dimensions) == 2
        assert len(proposal.measures) == 1
        assert proposal.dimensions[0].column == "year"
        assert proposal.dimensions[1].column == "region"

    def test_parse_yaml_line_by_line_inline_format(self):
        """Test _parse_yaml_line_by_line with inline list items."""
        yaml_content = """dimensions:
- column: year
  type: temporal
measures:
- column: value"""
        proposal = _parse_yaml_line_by_line(yaml_content)
        assert proposal is not None
        assert len(proposal.dimensions) == 1
        assert len(proposal.measures) == 1

    def test_extract_proposal_from_text_dimension_mentions(self):
        """Test _extract_proposal_from_text finds dimension mentions."""
        response = """Based on the data:
- Column 'year' should be a temporal dimension
- Column 'region' is a spatial dimension
- 'category' - categorical dimension"""
        proposal = _extract_proposal_from_text(response)
        assert proposal is not None
        assert len(proposal.dimensions) >= 2

    def test_extract_proposal_from_text_measure_mentions(self):
        """Test _extract_proposal_from_text finds measure mentions."""
        response = """The measures are:
- 'value' as a measure
- 'count' - measure
- Column `total` should be a measure"""
        proposal = _extract_proposal_from_text(response)
        assert proposal is not None
        assert len(proposal.measures) >= 2

    def test_extract_proposal_from_text_no_duplicates(self):
        """Test _extract_proposal_from_text avoids duplicates."""
        response = """Column 'year' is a temporal dimension.
The 'year' column is a temporal dimension.
year - temporal dimension"""
        proposal = _extract_proposal_from_text(response)
        assert proposal is not None
        # Should have only one 'year' dimension
        year_dims = [d for d in proposal.dimensions if d.column == "year"]
        assert len(year_dims) == 1

    def test_extract_proposal_from_text_returns_none_for_empty(self):
        """Test _extract_proposal_from_text returns None when nothing found."""
        response = "This is just a general response with no mapping info."
        proposal = _extract_proposal_from_text(response)
        assert proposal is None

    def test_robust_parse_yaml_returns_none_for_invalid(self):
        """Test _robust_parse_yaml_proposal returns None for completely invalid content."""
        yaml_content = "this is not yaml at all [ { ] }"
        proposal = _robust_parse_yaml_proposal(yaml_content)
        # Should return None or empty proposal
        assert proposal is None or (not proposal.dimensions and not proposal.measures)


class TestSuggestMappingName:
    """Tests for suggest_mapping_name helper."""

    def test_from_dcat_title(self):
        state = GraphState(
            csv_path="/data/file.csv",
            dataset_context={"title": "Population Statistics 2024"},
        )
        assert suggest_mapping_name(state) == "population-statistics-2024"

    def test_from_csv_filename(self):
        state = GraphState(csv_path="/data/my_dataset.csv")
        assert suggest_mapping_name(state) == "my-dataset"

    def test_fallback_when_no_inputs(self):
        state = GraphState()
        assert suggest_mapping_name(state) == "mapping"

    def test_dcat_title_preferred_over_csv(self):
        state = GraphState(
            csv_path="/data/raw.csv",
            dataset_context={"title": "Air Quality Measurements"},
        )
        name = suggest_mapping_name(state)
        assert "air-quality" in name
        assert "raw" not in name

    def test_special_chars_normalised(self):
        state = GraphState(
            dataset_context={"title": "Data (2024) — v2.0"},
        )
        name = suggest_mapping_name(state)
        # Should only contain lowercase, digits, hyphens
        assert all(c.isalnum() or c == "-" for c in name)
        assert name  # non-empty


class TestConfirmNameNode:
    """Tests for confirm_name_node setting mapping_name."""

    def test_suggests_name_from_csv(self):
        state = GraphState(
            csv_path="/data/population.csv",
            generated_rml="@prefix rr: <http://example.org/> .",
        )
        result = confirm_name_node(state)
        assert result.mapping_name == "population"
        assert result.current_state == FlowState.CONFIRM_NAME
        assert result.awaiting_user_input is True

    def test_suggests_name_from_dcat(self):
        state = GraphState(
            csv_path="/data/file.csv",
            generated_rml="@prefix rr: <http://example.org/> .",
            dataset_context={"title": "Population Statistics 2024"},
        )
        result = confirm_name_node(state)
        assert result.mapping_name == "population-statistics-2024"

    def test_preserves_existing_mapping_name(self):
        state = GraphState(
            csv_path="/data/population.csv",
            generated_rml="@prefix rr: <http://example.org/> .",
            mapping_name="custom-name",
        )
        result = confirm_name_node(state)
        assert result.mapping_name == "custom-name"

    def test_error_when_no_rml(self):
        state = GraphState(csv_path="/data/file.csv")
        result = confirm_name_node(state)
        assert result.current_state == FlowState.ERROR


class TestCreatePrNodeEnhancements:
    """Tests for create_pr_node using mapping_name and placeholder."""

    @patch("ogd_to_lod.graph.nodes.GitHubService")
    def test_uses_state_mapping_name(self, mock_gh_cls, mock_config, tmp_path):
        mock_service = MagicMock()
        mock_service.create_mapping_pr.return_value = MagicMock(
            pr_url="https://github.com/test/repo/pull/1",
            pr_number=1,
        )
        mock_gh_cls.return_value = mock_service

        csv_file = tmp_path / "file.csv"
        csv_file.write_text("col1\nval\n")

        state = GraphState(
            csv_path=str(csv_file),
            generated_rml='@prefix rml: <http://semweb.mmlab.be/ns/rml#> .\nex:M rml:logicalSource [ rml:source "file.csv" ].',
            mapping_name="my-mapping",
        )
        create_pr_node(state, mock_config)
        call_args = mock_service.create_mapping_pr.call_args
        assert call_args.kwargs["mapping_name"] == "my-mapping"

    @patch("ogd_to_lod.graph.nodes.GitHubService")
    def test_falls_back_to_csv_filename(self, mock_gh_cls, mock_config, tmp_path):
        mock_service = MagicMock()
        mock_service.create_mapping_pr.return_value = MagicMock(
            pr_url="https://github.com/test/repo/pull/1",
            pr_number=1,
        )
        mock_gh_cls.return_value = mock_service

        csv_file = tmp_path / "fallback.csv"
        csv_file.write_text("col1\nval\n")

        state = GraphState(
            csv_path=str(csv_file),
            generated_rml='@prefix rml: <http://semweb.mmlab.be/ns/rml#> .\nex:M rml:logicalSource [ rml:source "fallback.csv" ].',
        )
        create_pr_node(state, mock_config)
        call_args = mock_service.create_mapping_pr.call_args
        assert call_args.kwargs["mapping_name"] == "fallback"

    @patch("ogd_to_lod.graph.nodes.GitHubService")
    def test_passes_rml_with_csv_source_placeholder(self, mock_gh_cls, mock_config, tmp_path):
        mock_service = MagicMock()
        mock_service.create_mapping_pr.return_value = MagicMock(
            pr_url="https://github.com/test/repo/pull/1",
            pr_number=1,
        )
        mock_gh_cls.return_value = mock_service

        csv_file = tmp_path / "test.csv"
        csv_file.write_text("col1\nval\n")

        state = GraphState(
            csv_path=str(csv_file),
            generated_rml='@prefix rml: <http://semweb.mmlab.be/ns/rml#> .\nex:M rml:logicalSource [ rml:source "{CSV_SOURCE}" ].',
            mapping_name="test",
        )
        create_pr_node(state, mock_config)
        call_args = mock_service.create_mapping_pr.call_args
        rml_committed = call_args.kwargs["rml_content"]
        assert "{CSV_SOURCE}" in rml_committed


class TestBuildPrDescriptionIncludesRdfPreview:
    """Tests for _build_pr_description including rdf_preview."""

    def test_includes_rdf_preview(self):
        state = GraphState(
            csv_path="/data/file.csv",
            rdf_preview="ex:a ex:b ex:c .",
        )
        result = _build_pr_description(state, "test-mapping")
        assert "ex:a ex:b ex:c" in result

    def test_no_rdf_content_when_empty(self):
        state = GraphState(csv_path="/data/file.csv")
        result = _build_pr_description(state, "test-mapping")
        assert "```turtle" not in result


class TestPreviewNode:
    """Tests for preview_node building PR description."""

    def test_builds_pr_description(self):
        state = GraphState(
            csv_path="/data/population.csv",
            generated_rml="@prefix rr: <http://example.org/> .",
            mapping_name="population",
        )
        result = preview_node(state)
        assert result.pr_description is not None
        assert result.current_state == FlowState.PREVIEW
        assert result.awaiting_user_input is True

    def test_error_when_no_rml(self):
        state = GraphState(csv_path="/data/file.csv", mapping_name="test")
        result = preview_node(state)
        assert result.current_state == FlowState.ERROR

    def test_uses_mapping_name_in_description(self):
        state = GraphState(
            csv_path="/data/pop.csv",
            generated_rml="@prefix rr: <http://example.org/> .",
            mapping_name="my-mapping",
        )
        result = preview_node(state)
        assert "my-mapping" in result.pr_description


class TestNameConfirmationFlow:
    """Tests for the name confirmation step in the flow."""

    def test_empty_input_keeps_suggested_name(self, mock_config, mock_ai_service):
        flow = MappingFlow(mock_config, mock_ai_service)
        flow._state.current_state = FlowState.CONFIRM_NAME
        flow._state.generated_rml = "some rml"
        flow._state.csv_path = "/data/file.csv"
        flow._state.mapping_name = "auto-suggested"
        flow._state.awaiting_user_input = True

        result = flow._handle_name_confirmation("")

        assert result.mapping_name == "auto-suggested"
        # Now transitions to ASK_CSV_URL instead of PREVIEW
        assert result.current_state == FlowState.ASK_CSV_URL
        assert result.awaiting_user_input is True

    def test_custom_input_overrides_name(self, mock_config, mock_ai_service):
        flow = MappingFlow(mock_config, mock_ai_service)
        flow._state.current_state = FlowState.CONFIRM_NAME
        flow._state.generated_rml = "some rml"
        flow._state.csv_path = "/data/file.csv"
        flow._state.mapping_name = "auto-suggested"
        flow._state.awaiting_user_input = True

        result = flow._handle_name_confirmation("my-custom-name")

        assert result.mapping_name == "my-custom-name"
        assert result.current_state == FlowState.ASK_CSV_URL


class TestPrConfirmationSimplified:
    """Tests for simplified PR confirmation (yes/no only, no custom-name branch)."""

    @patch("ogd_to_lod.graph.nodes.GitHubService")
    def test_yes_creates_pr(self, mock_gh_cls, mock_config, mock_ai_service, tmp_path):
        mock_service = MagicMock()
        mock_service.create_mapping_pr.return_value = MagicMock(
            pr_url="https://github.com/test/repo/pull/1",
            pr_number=1,
        )
        mock_gh_cls.return_value = mock_service

        csv_file = tmp_path / "file.csv"
        csv_file.write_text("col1\nval\n")

        flow = MappingFlow(mock_config, mock_ai_service)
        flow._state.current_state = FlowState.PREVIEW
        flow._state.generated_rml = "some rml"
        flow._state.csv_path = str(csv_file)
        flow._state.mapping_name = "test"
        flow._state.awaiting_user_input = True

        result = flow._handle_pr_confirmation("yes")
        assert result.user_intent == UserIntent.APPROVE

    def test_no_cancels(self, mock_config, mock_ai_service):
        flow = MappingFlow(mock_config, mock_ai_service)
        flow._state.current_state = FlowState.PREVIEW
        flow._state.awaiting_user_input = True

        result = flow._handle_pr_confirmation("no")
        assert result.current_state == FlowState.END
        assert result.user_intent == UserIntent.REJECT

    def test_unrecognised_input_prompts_again(self, mock_config, mock_ai_service):
        flow = MappingFlow(mock_config, mock_ai_service)
        flow._state.current_state = FlowState.PREVIEW
        flow._state.awaiting_user_input = True

        result = flow._handle_pr_confirmation("some-random-text")
        assert result.awaiting_user_input is True
        # Should NOT treat as custom name — stays in PREVIEW
        assert result.current_state == FlowState.PREVIEW


class TestNameConfirmationTransitionsToAskCsvUrl:
    """Test that name confirmation now leads to ASK_CSV_URL."""

    def test_name_confirmation_transitions_to_ask_csv_url(self, mock_config, mock_ai_service):
        flow = MappingFlow(mock_config, mock_ai_service)
        flow._state.current_state = FlowState.CONFIRM_NAME
        flow._state.generated_rml = "some rml"
        flow._state.csv_path = "/data/file.csv"
        flow._state.mapping_name = "auto-suggested"
        flow._state.awaiting_user_input = True

        result = flow._handle_name_confirmation("")

        assert result.current_state == FlowState.ASK_CSV_URL
        assert result.awaiting_user_input is True
        assert result.mapping_name == "auto-suggested"

    def test_custom_name_also_goes_to_ask_csv_url(self, mock_config, mock_ai_service):
        flow = MappingFlow(mock_config, mock_ai_service)
        flow._state.current_state = FlowState.CONFIRM_NAME
        flow._state.generated_rml = "some rml"
        flow._state.csv_path = "/data/file.csv"
        flow._state.mapping_name = "auto-suggested"
        flow._state.awaiting_user_input = True

        result = flow._handle_name_confirmation("my-custom")

        assert result.current_state == FlowState.ASK_CSV_URL
        assert result.mapping_name == "my-custom"


class TestCsvUrlFlow:
    """Tests for the ASK_CSV_URL state handler."""

    def test_empty_input_skips_url(self, mock_config, mock_ai_service):
        flow = MappingFlow(mock_config, mock_ai_service)
        flow._state.current_state = FlowState.ASK_CSV_URL
        flow._state.csv_path = "/data/file.csv"
        flow._state.generated_rml = "some rml"
        flow._state.awaiting_user_input = True
        # No context paths → should go to PREVIEW
        flow._state.context_paths = []

        result = flow._handle_csv_url("")

        assert result.csv_source_url is None
        assert result.current_state == FlowState.PREVIEW

    def test_url_stored_and_no_dcat_goes_to_preview(self, mock_config, mock_ai_service):
        flow = MappingFlow(mock_config, mock_ai_service)
        flow._state.current_state = FlowState.ASK_CSV_URL
        flow._state.csv_path = "/data/file.csv"
        flow._state.generated_rml = "some rml"
        flow._state.awaiting_user_input = True
        flow._state.context_paths = []

        result = flow._handle_csv_url("https://example.com/data.csv")

        assert result.csv_source_url == "https://example.com/data.csv"
        assert result.current_state == FlowState.PREVIEW

    def test_with_context_paths_still_goes_to_preview(self, mock_config, mock_ai_service):
        flow = MappingFlow(mock_config, mock_ai_service)
        flow._state.current_state = FlowState.ASK_CSV_URL
        flow._state.csv_path = "/data/file.csv"
        flow._state.generated_rml = "some rml"
        flow._state.context_paths = ["/data/dcat.ttl"]
        flow._state.awaiting_user_input = True

        result = flow._handle_csv_url("https://example.com/data.csv")

        assert result.csv_source_url == "https://example.com/data.csv"
        assert result.current_state == FlowState.PREVIEW


class TestContinueWithInputRouting:
    """Tests that continue_with_input routes new states correctly."""

    def test_routes_ask_csv_url(self, mock_config, mock_ai_service):
        flow = MappingFlow(mock_config, mock_ai_service)
        flow._state.current_state = FlowState.ASK_CSV_URL
        flow._state.csv_path = "/data/file.csv"
        flow._state.generated_rml = "some rml"
        flow._state.awaiting_user_input = True
        flow._state.context_paths = []

        result = flow.continue_with_input("https://example.com/data.csv")

        assert result.csv_source_url == "https://example.com/data.csv"

class TestPrDescriptionUsesUrls:
    """Test that _build_pr_description uses source URLs when available."""

    def test_csv_url_in_description(self):
        state = GraphState(
            csv_path="/data/file.csv",
            csv_source_url="https://example.com/data.csv",
        )
        result = _build_pr_description(state, "test-mapping")
        assert "https://example.com/data.csv" in result
        assert "`/data/file.csv`" not in result

    def test_dcat_url_in_description(self):
        state = GraphState(
            csv_path="/data/file.csv",
            context_paths=["/data/dcat.ttl"],
        )
        result = _build_pr_description(state, "test-mapping")
        assert "`dcat.ttl`" in result
        assert "`/data/dcat.ttl`" not in result

    def test_not_provided_when_no_url(self):
        state = GraphState(
            csv_path="/data/file.csv",
            context_paths=["/data/dcat.ttl"],
        )
        result = _build_pr_description(state, "test-mapping")
        # Local paths should NOT appear — only public URLs are shown
        assert "`/data/file.csv`" not in result
        assert "`/data/dcat.ttl`" not in result


class TestIsAwaitingHelpers:
    """Tests for is_awaiting_* helper methods."""

    def test_is_awaiting_csv_url(self, mock_config, mock_ai_service):
        flow = MappingFlow(mock_config, mock_ai_service)
        assert flow.is_awaiting_csv_url() is False

        flow._state.current_state = FlowState.ASK_CSV_URL
        flow._state.awaiting_user_input = True
        assert flow.is_awaiting_csv_url() is True

