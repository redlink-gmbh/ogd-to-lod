"""Tests for RML generation module."""

import pytest
from unittest.mock import MagicMock, patch

from ogd_to_lod.config import Config, GitHubConfig, AzureOpenAIConfig, RMLConfig
from ogd_to_lod.rml import RMLGenerator, RMLGenerationError, generate_rml
from ogd_to_lod.rml.prompts import RML_CORRECTION_PROMPT, RML_GENERATION_PROMPT
from ogd_to_lod.graph.state import (
    DimensionProposal,
    FlowState,
    GraphState,
    MappingProposal,
    MeasureProposal,
)
from ogd_to_lod.graph.nodes import generate_node, regenerate_node


# Sample YARRRML output for testing
SAMPLE_YARRRML_OUTPUT = """\
prefixes:
  rml: "http://semweb.mmlab.be/ns/rml#"
  rr: "http://www.w3.org/ns/r2rml#"
  ql: "http://semweb.mmlab.be/ns/ql#"
  schema: "http://schema.org/"
  cube: "https://cube.link/"
  xsd: "http://www.w3.org/2001/XMLSchema#"
  ex: "https://example.org/"
  ex-obs: "https://example.org/observation/"
  ex-property: "https://example.org/property/"
  ex-code: "https://example.org/code/"

mappings:
  observations:
    sources:
      - access: "{{CSV_SOURCE}}"
        referenceFormulation: csv
        delimiter: ","
    s: ex-obs:$(year)_$(region)
    po:
      - [a, cube:Observation]
      - [ex-property:ZEIT, $(year), xsd:gYear]
      - [ex-property:RAUM, ex-code:$(region)~iri]
      - [ex-property:value, $(value), xsd:decimal]
  regionCodes:
    sources:
      - access: "{{CSV_SOURCE}}"
        referenceFormulation: csv
        delimiter: ","
    s: ex-code:$(region)
    po:
      - [a, schema:DefinedTerm]
      - [schema:name, $(region)]
"""


@pytest.fixture
def mock_ai_service():
    """Create a mock AI service that returns sample YARRRML."""
    service = MagicMock()
    service.send_message.return_value = f"""Here is the generated YARRRML mapping:

```yaml
{SAMPLE_YARRRML_OUTPUT}
```

This mapping creates observations from the CSV data with year and region as dimensions and value as a measure.
"""
    return service


@pytest.fixture
def sample_mapping_proposal():
    """Create a sample approved mapping proposal."""
    return {
        "dimensions": [
            {"column": "year", "type": "temporal", "granularity": "year"},
            {"column": "region", "type": "spatial"},
        ],
        "measures": [
            {"column": "value", "unit": "count"},
        ],
        "status": "approved",
    }


@pytest.fixture
def sample_csv_schema():
    """Create a sample CSV schema."""
    return {
        "source": "/path/to/data.csv",
        "columns": [
            {"name": "year", "type": "int", "samples": [2020, 2021, 2022]},
            {"name": "region", "type": "string", "samples": ["North", "South", "East"]},
            {"name": "value", "type": "float", "samples": [100.5, 200.3, 150.0]},
        ],
        "total_rows": 1000,
    }


class TestRMLGenerator:
    """Tests for RMLGenerator class."""

    def test_generate_success(self, mock_ai_service, sample_mapping_proposal, sample_csv_schema):
        """Test successful YARRRML generation."""
        generator = RMLGenerator(mock_ai_service)

        result = generator.generate(
            mapping_proposal=sample_mapping_proposal,
            csv_schema=sample_csv_schema,
            csv_path="/path/to/data.csv",
            base_uri="https://example.org/",
        )

        assert result is not None
        assert "mappings:" in result
        assert "cube:Observation" in result
        mock_ai_service.send_message.assert_called_once()

    def test_generate_passes_delimiter_to_prompt(self, mock_ai_service, sample_mapping_proposal):
        """Test that generate() passes csv_delimiter to the prompt."""
        generator = RMLGenerator(mock_ai_service)

        schema_with_semicolon = {
            "source": "data.csv",
            "columns": [{"name": "year", "type": "int", "samples": [2020]}],
            "total_rows": 10,
            "delimiter": ";",
        }

        generator.generate(
            mapping_proposal=sample_mapping_proposal,
            csv_schema=schema_with_semicolon,
            csv_path="/path/to/data.csv",
            base_uri="https://example.org/",
        )

        # The prompt sent to AI should contain the semicolon delimiter
        call_args = mock_ai_service.send_message.call_args[0][0]
        assert ";" in call_args
        assert "detected CSV delimiter" in call_args.lower() or "delimiter" in call_args.lower()

    def test_generate_no_yaml_block(self, mock_ai_service, sample_mapping_proposal, sample_csv_schema):
        """Test generation fails when no YAML block is returned."""
        mock_ai_service.send_message.return_value = "Here is some text without any code block."

        generator = RMLGenerator(mock_ai_service)

        with pytest.raises(RMLGenerationError) as exc_info:
            generator.generate(
                mapping_proposal=sample_mapping_proposal,
                csv_schema=sample_csv_schema,
                csv_path="/path/to/data.csv",
                base_uri="https://example.org/",
            )

        assert "AI did not generate valid YARRRML output" in str(exc_info.value)

    def test_generate_ai_error(self, mock_ai_service, sample_mapping_proposal, sample_csv_schema):
        """Test generation handles AI service errors."""
        mock_ai_service.send_message.side_effect = Exception("API error")

        generator = RMLGenerator(mock_ai_service)

        with pytest.raises(RMLGenerationError) as exc_info:
            generator.generate(
                mapping_proposal=sample_mapping_proposal,
                csv_schema=sample_csv_schema,
                csv_path="/path/to/data.csv",
                base_uri="https://example.org/",
            )

        assert "Failed to generate YARRRML" in str(exc_info.value)

    def test_format_proposal(self, mock_ai_service, sample_mapping_proposal):
        """Test proposal formatting for AI prompt."""
        generator = RMLGenerator(mock_ai_service)

        result = generator._format_proposal(sample_mapping_proposal)

        assert "### Dimensions:" in result
        assert "year: temporal" in result
        assert "region: spatial" in result
        assert "### Measures:" in result
        assert "value" in result

    def test_format_schema(self, mock_ai_service, sample_csv_schema):
        """Test schema formatting for AI prompt."""
        generator = RMLGenerator(mock_ai_service)

        result = generator._format_schema(sample_csv_schema)

        assert "Source: /path/to/data.csv" in result
        assert "Total rows: 1000" in result
        assert "year (int)" in result
        assert "region (string)" in result
        assert "value (float)" in result

    def test_format_schema_includes_delimiter(self, mock_ai_service):
        """Test that _format_schema includes the delimiter info."""
        generator = RMLGenerator(mock_ai_service)

        schema_semicolon = {
            "source": "data.csv",
            "columns": [{"name": "col", "type": "string", "samples": ["a"]}],
            "total_rows": 10,
            "delimiter": ";",
        }
        result = generator._format_schema(schema_semicolon)
        assert "Delimiter: ';'" in result

    def test_format_schema_default_delimiter(self, mock_ai_service):
        """Test that _format_schema defaults to comma when delimiter absent."""
        generator = RMLGenerator(mock_ai_service)

        schema_no_delim = {
            "source": "data.csv",
            "columns": [{"name": "col", "type": "string", "samples": ["a"]}],
            "total_rows": 10,
        }
        result = generator._format_schema(schema_no_delim)
        assert "Delimiter: ','" in result


class TestGenerateRMLFunction:
    """Tests for the generate_rml convenience function."""

    def test_generate_rml_function(self, mock_ai_service, sample_mapping_proposal, sample_csv_schema):
        """Test the convenience function."""
        result = generate_rml(
            ai_service=mock_ai_service,
            mapping_proposal=sample_mapping_proposal,
            csv_schema=sample_csv_schema,
            csv_path="/path/to/data.csv",
            base_uri="https://example.org/",
        )

        assert result is not None
        assert "mappings:" in result


class TestGenerateNode:
    """Tests for the generate_node function."""

    def test_generate_node_success(self, mock_ai_service):
        """Test successful YARRRML generation via node."""
        state = GraphState(
            current_state=FlowState.GENERATE,
            csv_path="/path/to/data.csv",
            base_uri="https://example.org/",
            csv_schema={
                "source": "/path/to/data.csv",
                "columns": [
                    {"name": "year", "type": "int", "samples": [2020]},
                    {"name": "value", "type": "float", "samples": [100.0]},
                ],
                "total_rows": 100,
            },
            mapping_proposal=MappingProposal(
                dimensions=[DimensionProposal(column="year", dimension_type="temporal")],
                measures=[MeasureProposal(column="value")],
                status="approved",
            ),
        )

        result = generate_node(state, mock_ai_service)

        assert result.generated_rml is not None
        assert result.current_state == FlowState.PREVIEW
        assert "mappings:" in result.generated_rml

    def test_generate_node_no_proposal(self, mock_ai_service):
        """Test generate node fails without mapping proposal."""
        state = GraphState(
            current_state=FlowState.GENERATE,
            csv_path="/path/to/data.csv",
            base_uri="https://example.org/",
            csv_schema={"columns": []},
        )

        result = generate_node(state, mock_ai_service)

        assert result.current_state == FlowState.ERROR
        assert "No mapping proposal" in result.error_message

    def test_generate_node_not_approved(self, mock_ai_service):
        """Test generate node fails with unapproved proposal."""
        state = GraphState(
            current_state=FlowState.GENERATE,
            csv_path="/path/to/data.csv",
            base_uri="https://example.org/",
            csv_schema={"columns": []},
            mapping_proposal=MappingProposal(status="pending"),
        )

        result = generate_node(state, mock_ai_service)

        assert result.current_state == FlowState.ERROR
        assert "must be approved" in result.error_message

    def test_generate_node_no_csv_schema(self, mock_ai_service):
        """Test generate node fails without CSV schema."""
        state = GraphState(
            current_state=FlowState.GENERATE,
            csv_path="/path/to/data.csv",
            base_uri="https://example.org/",
            mapping_proposal=MappingProposal(status="approved"),
        )

        result = generate_node(state, mock_ai_service)

        assert result.current_state == FlowState.ERROR
        assert "CSV schema is required" in result.error_message

    def test_generate_node_no_csv_path(self, mock_ai_service):
        """Test generate node fails without CSV path."""
        state = GraphState(
            current_state=FlowState.GENERATE,
            base_uri="https://example.org/",
            csv_schema={"columns": []},
            mapping_proposal=MappingProposal(status="approved"),
        )

        result = generate_node(state, mock_ai_service)

        assert result.current_state == FlowState.ERROR
        assert "CSV path is required" in result.error_message

    def test_generate_node_no_base_uri(self, mock_ai_service):
        """Test generate node fails without base URI."""
        state = GraphState(
            current_state=FlowState.GENERATE,
            csv_path="/path/to/data.csv",
            csv_schema={"columns": []},
            mapping_proposal=MappingProposal(status="approved"),
        )

        result = generate_node(state, mock_ai_service)

        assert result.current_state == FlowState.ERROR
        assert "Base URI is required" in result.error_message


class TestRMLPrompts:
    """Tests for RML generation prompts."""

    def test_prompt_has_required_prefixes(self):
        """Test that the prompt includes required vocabulary prefixes."""
        assert "rml:" in RML_GENERATION_PROMPT
        assert "rr:" in RML_GENERATION_PROMPT
        assert "cube:" in RML_GENERATION_PROMPT
        assert "schema:" in RML_GENERATION_PROMPT
        assert "xsd:" in RML_GENERATION_PROMPT

    def test_prompt_has_placeholders(self):
        """Test that the prompt has required placeholders."""
        assert "{base_uri}" in RML_GENERATION_PROMPT
        assert "{csv_path}" in RML_GENERATION_PROMPT
        assert "{mapping_proposal}" in RML_GENERATION_PROMPT
        assert "{csv_schema}" in RML_GENERATION_PROMPT
        assert "{csv_delimiter}" in RML_GENERATION_PROMPT

    def test_prompt_has_yarrrml_source_section(self):
        """Test that the prompt includes a YARRRML source definition."""
        assert "sources:" in RML_GENERATION_PROMPT
        assert "referenceFormulation: csv" in RML_GENERATION_PROMPT
        assert "delimiter:" in RML_GENERATION_PROMPT

    def test_prompt_mentions_cube_link(self):
        """Test that the prompt mentions cube.link vocabulary."""
        assert "cube:" in RML_GENERATION_PROMPT
        assert "cube:Observation" in RML_GENERATION_PROMPT

    def test_prompt_mentions_schema_org(self):
        """Test that the prompt mentions schema.org vocabulary."""
        assert "schema:" in RML_GENERATION_PROMPT
        assert "DefinedTerm" in RML_GENERATION_PROMPT

    def test_prompt_requests_yaml_output(self):
        """Test that the prompt asks for YAML output."""
        assert "yaml" in RML_GENERATION_PROMPT.lower()

    def test_prompt_mentions_yarrrml(self):
        """Test that the prompt mentions YARRRML."""
        assert "YARRRML" in RML_GENERATION_PROMPT

    def test_prompt_has_mappings_structure(self):
        """Test that the prompt shows mappings structure."""
        assert "mappings:" in RML_GENERATION_PROMPT
        assert "prefixes:" in RML_GENERATION_PROMPT


class TestRMLCorrectionPrompt:
    """Tests for the RML_CORRECTION_PROMPT template."""

    def test_has_error_message_placeholder(self):
        """Test that the correction prompt contains the error_message placeholder."""
        assert "{error_message}" in RML_CORRECTION_PROMPT

    def test_mentions_yaml_code_block(self):
        """Test that the correction prompt asks for a yaml code block."""
        assert "yaml" in RML_CORRECTION_PROMPT

    def test_format_with_error(self):
        """Test that the correction prompt can be formatted with an error message."""
        formatted = RML_CORRECTION_PROMPT.format(
            error_message='YAML syntax error at line 5'
        )
        assert 'YAML syntax error at line 5' in formatted
        assert "Fix ONLY" in formatted


class TestRMLGeneratorRegenerate:
    """Tests for RMLGenerator.regenerate_with_error()."""

    def test_regenerate_sends_error_context(self, mock_ai_service):
        """Test that regenerate_with_error sends the error message to the AI."""
        generator = RMLGenerator(mock_ai_service)

        result = generator.regenerate_with_error('YAML syntax error: unexpected token')

        assert result is not None
        # Verify the correction prompt was sent with the error
        call_args = mock_ai_service.send_message.call_args[0][0]
        assert 'YAML syntax error: unexpected token' in call_args

    def test_regenerate_no_yaml_block(self, mock_ai_service):
        """Test that regenerate_with_error raises when no yaml block returned."""
        mock_ai_service.send_message.return_value = "I cannot fix this error."

        generator = RMLGenerator(mock_ai_service)

        with pytest.raises(RMLGenerationError) as exc_info:
            generator.regenerate_with_error("some error")

        assert "did not return corrected YARRRML" in str(exc_info.value)

    def test_regenerate_ai_error(self, mock_ai_service):
        """Test that regenerate_with_error wraps AI exceptions."""
        mock_ai_service.send_message.side_effect = Exception("API error")

        generator = RMLGenerator(mock_ai_service)

        with pytest.raises(RMLGenerationError) as exc_info:
            generator.regenerate_with_error("some error")

        assert "Failed to regenerate YARRRML" in str(exc_info.value)


class TestRegenerateNode:
    """Tests for the regenerate_node graph node function."""

    def test_regenerate_node_success(self, mock_ai_service):
        """Test successful regeneration via node."""
        state = GraphState(
            current_state=FlowState.GENERATE,
            csv_path="/path/to/data.csv",
            base_uri="https://example.org/",
            validation_error='YAML syntax error at line 5',
            generated_rml="invalid content",
        )

        result = regenerate_node(state, mock_ai_service)

        assert result.generated_rml is not None
        assert "mappings:" in result.generated_rml
        assert result.current_state == FlowState.PREVIEW

    def test_regenerate_node_uses_validation_error(self, mock_ai_service):
        """Test that regenerate_node passes validation_error to AI."""
        state = GraphState(
            current_state=FlowState.GENERATE,
            csv_path="/path/to/data.csv",
            base_uri="https://example.org/",
            validation_error='YAML syntax error at line 5',
            generated_rml="invalid content",
        )

        regenerate_node(state, mock_ai_service)

        call_args = mock_ai_service.send_message.call_args[0][0]
        assert 'YAML syntax error at line 5' in call_args

    def test_regenerate_node_ai_failure(self, mock_ai_service):
        """Test regenerate_node handles AI failure."""
        mock_ai_service.send_message.return_value = "No code block here."

        state = GraphState(
            current_state=FlowState.GENERATE,
            csv_path="/path/to/data.csv",
            base_uri="https://example.org/",
            validation_error="some error",
            generated_rml="invalid content",
        )

        result = regenerate_node(state, mock_ai_service)

        assert result.current_state == FlowState.ERROR
        assert "Failed to regenerate RML" in result.error_message
