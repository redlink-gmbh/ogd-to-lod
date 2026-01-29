"""Tests for RML validation module."""

import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ogd_to_lod.validation import (
    RMLValidator,
    RMLValidationError,
    RMLMapperNotFoundError,
    ValidationResult,
    validate_rml,
)
from ogd_to_lod.graph.state import FlowState, GraphState, MappingProposal
from ogd_to_lod.graph.nodes import validate_node


# Sample RML for testing
SAMPLE_RML = """@prefix rr: <http://www.w3.org/ns/r2rml#> .
@prefix rml: <http://semweb.mmlab.be/ns/rml#> .
@prefix ql: <http://semweb.mmlab.be/ns/ql#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix schema: <http://schema.org/> .
@prefix cube: <https://cube.link/> .
@prefix ex: <https://example.org/> .

ex:TriplesMap a rr:TriplesMap ;
    rml:logicalSource [
        rml:source "data.csv" ;
        rml:referenceFormulation ql:CSV
    ] ;
    rr:subjectMap [
        rr:template "https://example.org/observation/{year}/{region}" ;
        rr:class cube:Observation
    ] ;
    rr:predicateObjectMap [
        rr:predicate cube:dimension ;
        rr:objectMap [
            rml:reference "year" ;
            rr:datatype xsd:gYear
        ]
    ] ;
    rr:predicateObjectMap [
        rr:predicate cube:dimension ;
        rr:objectMap [
            rml:reference "region" ;
            rr:datatype xsd:string
        ]
    ] ;
    rr:predicateObjectMap [
        rr:predicate cube:measure ;
        rr:objectMap [
            rml:reference "value" ;
            rr:datatype xsd:decimal
        ]
    ] .
"""

# Invalid RML (syntax error)
INVALID_RML_SYNTAX = """@prefix rr: <http://www.w3.org/ns/r2rml#> .
This is not valid Turtle syntax!!!
"""

# RML missing components (valid syntax but missing logical source)
MINIMAL_RML = """@prefix rr: <http://www.w3.org/ns/r2rml#> .
@prefix rml: <http://semweb.mmlab.be/ns/rml#> .
@prefix ex: <http://example.org/> .

ex:Something rr:predicateObjectMap [ ] .
"""


@pytest.fixture
def temp_csv():
    """Create a temporary CSV file for testing."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False
    ) as f:
        f.write("year,region,value\n")
        f.write("2020,North,100.5\n")
        f.write("2021,South,200.3\n")
        yield f.name
    os.unlink(f.name)


class TestRMLValidator:
    """Tests for RMLValidator class."""

    def test_init_default(self):
        """Test default initialization."""
        validator = RMLValidator()
        assert validator._rmlmapper_jar is None
        assert validator._use_docker is False

    def test_init_with_jar(self):
        """Test initialization with JAR path."""
        validator = RMLValidator(rmlmapper_jar="/path/to/rmlmapper.jar")
        assert validator._rmlmapper_jar == "/path/to/rmlmapper.jar"

    def test_init_with_docker(self):
        """Test initialization with Docker."""
        validator = RMLValidator(use_docker=True)
        assert validator._use_docker is True
        assert validator._docker_image == "rmlio/rmlmapper-java:latest"

    def test_init_with_env_var(self, monkeypatch):
        """Test initialization from environment variable."""
        monkeypatch.setenv("RMLMAPPER_JAR", "/env/path/rmlmapper.jar")
        validator = RMLValidator()
        assert validator._rmlmapper_jar == "/env/path/rmlmapper.jar"

    def test_validate_syntax_only_valid(self):
        """Test syntax-only validation with valid RML."""
        validator = RMLValidator()  # No JAR configured
        result = validator.validate(SAMPLE_RML, "/nonexistent/path.csv")

        assert result.valid is True
        assert result.rdf_output is None  # No actual RDF generation

    def test_validate_syntax_only_invalid(self):
        """Test syntax-only validation with invalid RML."""
        validator = RMLValidator()
        result = validator.validate(INVALID_RML_SYNTAX, "/nonexistent/path.csv")

        assert result.valid is False
        assert "syntax error" in result.error_message.lower()

    def test_validate_syntax_only_with_warnings(self):
        """Test syntax-only validation produces warnings for missing components."""
        validator = RMLValidator()
        result = validator.validate(MINIMAL_RML, "/nonexistent/path.csv")

        # Should be valid syntax but may have warnings
        assert result.valid is True
        # Warnings about missing logical source or subject map
        if result.warnings:
            assert any(
                "logical source" in w.lower() or "subject map" in w.lower()
                for w in result.warnings
            )

    def test_validate_csv_not_found(self, temp_csv):
        """Test validation fails when CSV doesn't exist."""
        validator = RMLValidator(rmlmapper_jar="/fake/path.jar")
        result = validator.validate(SAMPLE_RML, "/nonexistent/data.csv")

        assert result.valid is False
        assert "not found" in result.error_message.lower()

    @patch("subprocess.run")
    def test_validate_with_jar_success(self, mock_run, temp_csv):
        """Test successful validation with JAR."""
        # Mock successful RMLMapper execution
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )

        with tempfile.NamedTemporaryFile(suffix=".jar", delete=False) as jar_file:
            jar_path = jar_file.name

        try:
            validator = RMLValidator(rmlmapper_jar=jar_path)

            # Need to mock the output file creation
            with patch.object(Path, "read_text", return_value="<rdf output>"):
                with patch.object(Path, "exists", return_value=True):
                    result = validator.validate(SAMPLE_RML, temp_csv)

            assert result.valid is True
            assert mock_run.called
        finally:
            os.unlink(jar_path)

    @patch("subprocess.run")
    def test_validate_with_jar_failure(self, mock_run, temp_csv):
        """Test validation failure with JAR."""
        # Mock failed RMLMapper execution
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="Error: Invalid column reference 'unknown_column'",
        )

        with tempfile.NamedTemporaryFile(suffix=".jar", delete=False) as jar_file:
            jar_path = jar_file.name

        try:
            validator = RMLValidator(rmlmapper_jar=jar_path)
            result = validator.validate(SAMPLE_RML, temp_csv)

            assert result.valid is False
            assert "unknown_column" in result.error_message
        finally:
            os.unlink(jar_path)

    @patch("subprocess.run")
    def test_validate_timeout(self, mock_run, temp_csv):
        """Test validation timeout handling."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="java", timeout=60)

        with tempfile.NamedTemporaryFile(suffix=".jar", delete=False) as jar_file:
            jar_path = jar_file.name

        try:
            validator = RMLValidator(rmlmapper_jar=jar_path)
            result = validator.validate(SAMPLE_RML, temp_csv, timeout=60)

            assert result.valid is False
            assert "timed out" in result.error_message.lower()
        finally:
            os.unlink(jar_path)

    def test_clean_error_message(self):
        """Test error message cleaning."""
        validator = RMLValidator()

        # Test with Java stack trace
        raw_error = """java.lang.RuntimeException: Error processing mapping
at be.ugent.rml.Executor.execute(Executor.java:123)
at be.ugent.rml.Main.main(Main.java:45)
Caused by: java.io.FileNotFoundException: file.csv
Invalid file path"""

        cleaned = validator._clean_error_message(raw_error)

        # Should not contain stack trace lines
        assert "at be.ugent" not in cleaned
        assert "Caused by:" not in cleaned
        # Should contain the actual error
        assert "Invalid file path" in cleaned

    def test_is_available_no_jar(self):
        """Test availability check with no JAR."""
        validator = RMLValidator()
        assert validator.is_available() is False

    def test_is_available_with_nonexistent_jar(self):
        """Test availability check with nonexistent JAR."""
        validator = RMLValidator(rmlmapper_jar="/nonexistent/path.jar")
        assert validator.is_available() is False

    def test_is_available_with_existing_jar(self):
        """Test availability check with existing JAR."""
        with tempfile.NamedTemporaryFile(suffix=".jar", delete=False) as jar_file:
            jar_path = jar_file.name

        try:
            validator = RMLValidator(rmlmapper_jar=jar_path)
            assert validator.is_available() is True
        finally:
            os.unlink(jar_path)


class TestValidationResult:
    """Tests for ValidationResult dataclass."""

    def test_valid_result(self):
        """Test valid result creation."""
        result = ValidationResult(
            valid=True,
            rdf_output="<rdf content>",
        )
        assert result.valid is True
        assert result.rdf_output == "<rdf content>"
        assert result.error_message is None
        assert result.warnings is None

    def test_invalid_result(self):
        """Test invalid result creation."""
        result = ValidationResult(
            valid=False,
            error_message="Something went wrong",
        )
        assert result.valid is False
        assert result.rdf_output is None
        assert result.error_message == "Something went wrong"

    def test_result_with_warnings(self):
        """Test result with warnings."""
        result = ValidationResult(
            valid=True,
            warnings=["Warning 1", "Warning 2"],
        )
        assert result.valid is True
        assert len(result.warnings) == 2


class TestValidateRMLFunction:
    """Tests for the validate_rml convenience function."""

    def test_validate_rml_syntax_only(self):
        """Test convenience function with syntax-only validation."""
        result = validate_rml(SAMPLE_RML, "/nonexistent/path.csv")

        assert result.valid is True


class TestValidateNode:
    """Tests for the validate_node graph function."""

    def test_validate_node_no_rml(self):
        """Test validate_node fails without generated RML."""
        state = GraphState(
            current_state=FlowState.GENERATE,
            csv_path="/path/to/data.csv",
        )

        result = validate_node(state)

        assert result.current_state == FlowState.ERROR
        assert "No RML to validate" in result.error_message

    def test_validate_node_no_csv_path(self):
        """Test validate_node fails without CSV path."""
        state = GraphState(
            current_state=FlowState.GENERATE,
            generated_rml=SAMPLE_RML,
        )

        result = validate_node(state)

        assert result.current_state == FlowState.ERROR
        assert "CSV path required" in result.error_message

    def test_validate_node_syntax_valid(self, temp_csv):
        """Test validate_node with valid RML syntax."""
        state = GraphState(
            current_state=FlowState.GENERATE,
            csv_path=temp_csv,
            generated_rml=SAMPLE_RML,
            mapping_proposal=MappingProposal(status="approved"),
        )

        result = validate_node(state)

        assert result.current_state == FlowState.PREVIEW
        assert result.validation_error is None

    def test_validate_node_syntax_invalid(self, temp_csv):
        """Test validate_node with invalid RML syntax."""
        state = GraphState(
            current_state=FlowState.GENERATE,
            csv_path=temp_csv,
            generated_rml=INVALID_RML_SYNTAX,
            mapping_proposal=MappingProposal(status="approved"),
        )

        result = validate_node(state)

        assert result.current_state == FlowState.REFINE
        assert result.validation_error is not None
        assert result.mapping_proposal.status == "refining"

    def test_validate_node_adds_success_message(self, temp_csv):
        """Test validate_node adds success message to conversation."""
        state = GraphState(
            current_state=FlowState.GENERATE,
            csv_path=temp_csv,
            generated_rml=SAMPLE_RML,
            mapping_proposal=MappingProposal(status="approved"),
        )

        result = validate_node(state)

        # Check that a success message was added
        assert len(result.messages) > 0
        last_message = result.messages[-1]
        assert last_message["role"] == "assistant"
        assert "validation successful" in last_message["content"].lower()

    def test_validate_node_adds_error_message(self, temp_csv):
        """Test validate_node adds error message on failure."""
        state = GraphState(
            current_state=FlowState.GENERATE,
            csv_path=temp_csv,
            generated_rml=INVALID_RML_SYNTAX,
            mapping_proposal=MappingProposal(status="approved"),
        )

        result = validate_node(state)

        # Check that an error message was added
        assert len(result.messages) > 0
        last_message = result.messages[-1]
        assert last_message["role"] == "assistant"
        assert "validation failed" in last_message["content"].lower()
