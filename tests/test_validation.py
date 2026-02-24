"""Tests for RML validation module."""

import csv
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from ogd_to_lod.validation import (
    RMLMapperNotFoundError,
    RMLValidationError,
    RMLValidator,
    ValidationResult,
    validate_rml,
)


# ── Constants ────────────────────────────────────────────────────────────

INVALID_YARRRML_SYNTAX = """\
prefixes:
  ex: "https://example.org/"
mappings:
  observations:
    sources: [  # unclosed bracket — invalid YAML
"""

MINIMAL_YARRRML = """\
mappings:
  observations:
    sources:
      - access: "data.csv"
        referenceFormulation: csv
        delimiter: ","
    s: ex:obs_$(col)
    po:
      - [a, ex:Thing]
"""

YARRRML_MISSING_PREFIXES = """\
mappings:
  observations:
    sources:
      - access: "data.csv"
        referenceFormulation: csv
        delimiter: ","
    s: ex:obs_$(col)
    po:
      - [a, ex:Thing]
"""


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

    def test_init_with_yarrrml_parser_image(self):
        """Test initialization with custom yarrrml-parser image."""
        validator = RMLValidator(
            use_docker=True,
            yarrrml_parser_docker_image="custom/yarrrml-parser:v1",
        )
        assert validator._yarrrml_parser_image == "custom/yarrrml-parser:v1"

    def test_init_with_env_var(self, monkeypatch):
        """Test initialization from environment variable."""
        monkeypatch.setenv("RMLMAPPER_JAR", "/env/path/rmlmapper.jar")
        validator = RMLValidator()
        assert validator._rmlmapper_jar == "/env/path/rmlmapper.jar"

    def test_validate_syntax_only_valid(self, sample_rml):
        """Test syntax-only validation with valid YARRRML."""
        validator = RMLValidator()  # No Docker configured
        result = validator.validate(sample_rml, "/nonexistent/path.csv")

        assert result.valid is True
        assert result.rdf_output is None  # No actual RDF generation

    def test_validate_syntax_only_invalid(self):
        """Test syntax-only validation with invalid YARRRML."""
        validator = RMLValidator()
        result = validator.validate(INVALID_YARRRML_SYNTAX, "/nonexistent/path.csv")

        assert result.valid is False
        assert "syntax error" in result.error_message.lower()

    def test_validate_syntax_only_with_warnings(self):
        """Test syntax-only validation produces warnings for missing components."""
        validator = RMLValidator()
        result = validator.validate_syntax(YARRRML_MISSING_PREFIXES)

        # Should be valid syntax but warn about missing prefixes
        assert result.valid is True
        assert result.warnings is not None
        assert any("prefixes" in w.lower() for w in result.warnings)

    def test_validate_csv_not_found(self, sample_rml):
        """Test validation fails when CSV doesn't exist (Docker mode)."""
        validator = RMLValidator(use_docker=True)
        result = validator.validate_with_rmlmapper(
            sample_rml, "/nonexistent/data.csv"
        )

        assert result.valid is False
        assert "not found" in result.error_message.lower()

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
        assert result.error_category is None
        assert result.user_friendly_error is None

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

    def test_result_with_error_category(self):
        """Test result with error categorisation fields."""
        result = ValidationResult(
            valid=False,
            error_message="Column 'foo' not found",
            error_category="missing_column",
            user_friendly_error="The mapping references a CSV column that does not exist.",
        )
        assert result.error_category == "missing_column"
        assert "does not exist" in result.user_friendly_error


class TestValidateRMLFunction:
    """Tests for the validate_rml convenience function."""

    def test_validate_rml_syntax_only(self, sample_rml):
        """Test convenience function with syntax-only validation."""
        result = validate_rml(sample_rml, "/nonexistent/path.csv")

        assert result.valid is True


class TestValidateSyntax:
    """Tests for the public validate_syntax() method (Tier 1)."""

    def test_valid_yarrrml(self, sample_rml):
        """Test syntax validation passes for valid YARRRML."""
        validator = RMLValidator()
        result = validator.validate_syntax(sample_rml)

        assert result.valid is True
        assert result.error_message is None

    def test_invalid_yarrrml(self):
        """Test syntax validation fails for invalid YARRRML."""
        validator = RMLValidator()
        result = validator.validate_syntax(INVALID_YARRRML_SYNTAX)

        assert result.valid is False
        assert result.error_message is not None
        assert "syntax error" in result.error_message.lower()

    def test_minimal_valid_yarrrml(self):
        """Test syntax validation passes for minimal valid YARRRML with mappings key."""
        validator = RMLValidator()
        result = validator.validate_syntax(MINIMAL_YARRRML)

        assert result.valid is True

    def test_missing_mappings_key(self):
        """Test syntax validation fails when mappings key is absent."""
        no_mappings = "prefixes:\n  ex: 'https://example.org/'\n"
        validator = RMLValidator()
        result = validator.validate_syntax(no_mappings)

        assert result.valid is False
        assert "mappings" in result.error_message.lower()

    def test_empty_string(self):
        """Test syntax validation with empty string."""
        validator = RMLValidator()
        result = validator.validate_syntax("")

        assert result.valid is False

    def test_warns_on_missing_prefixes(self):
        """Test that missing prefixes block generates a warning."""
        validator = RMLValidator()
        result = validator.validate_syntax(YARRRML_MISSING_PREFIXES)

        assert result.valid is True
        assert result.warnings is not None
        assert any("prefixes" in w.lower() for w in result.warnings)

    def test_warns_on_mapping_missing_subject(self):
        """Test that a mapping without a subject generates a warning."""
        no_subject = """\
prefixes:
  ex: "https://example.org/"
mappings:
  obs:
    sources:
      - access: "data.csv"
        referenceFormulation: csv
        delimiter: ","
    po:
      - [a, ex:Thing]
"""
        validator = RMLValidator()
        result = validator.validate_syntax(no_subject)

        assert result.valid is True
        assert result.warnings is not None
        assert any("subject" in w.lower() or "'s'" in w.lower() for w in result.warnings)


class TestValidateWithRMLMapper:
    """Tests for validate_with_rmlmapper() method (Tier 2)."""

    def test_skips_when_docker_not_enabled(self, sample_rml):
        """Test Tier 2 gracefully skips when Docker is not configured."""
        validator = RMLValidator()  # use_docker=False by default
        result = validator.validate_with_rmlmapper(sample_rml, "/some/path.csv")

        assert result.valid is True
        assert result.warnings is not None
        assert any("docker not enabled" in w.lower() for w in result.warnings)

    def test_csv_not_found_with_docker(self, sample_rml):
        """Test Tier 2 fails when CSV file doesn't exist (Docker mode)."""
        validator = RMLValidator(use_docker=True)
        result = validator.validate_with_rmlmapper(
            sample_rml, "/nonexistent/data.csv"
        )

        assert result.valid is False
        assert result.error_category == "file_not_found"

    @patch("subprocess.run")
    def test_success_with_mocked_docker(self, mock_run, data_csv, sample_rml):
        """Test successful Tier 2 validation with mocked Docker."""
        # Both subprocess.run calls (yarrrml-parser + rmlmapper) return success
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )

        validator = RMLValidator(use_docker=True)
        with patch.object(Path, "read_text", return_value="ex:obs1 a ex:Observation ."):
            with patch.object(Path, "exists", return_value=True):
                result = validator.validate_with_rmlmapper(sample_rml, data_csv)

        assert result.valid is True
        assert mock_run.called
        # Both yarrrml-parser and rmlmapper should be called
        assert mock_run.call_count == 2

    @patch("subprocess.run")
    def test_yarrrml_parser_failure(self, mock_run, data_csv, sample_rml):
        """Test that yarrrml-parser failure returns invalid result."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="Error: unexpected token at line 3",
        )

        validator = RMLValidator(use_docker=True)
        result = validator.validate_with_rmlmapper(sample_rml, data_csv)

        assert result.valid is False
        assert result.error_category == "syntax_error"
        # Only yarrrml-parser is called (rmlmapper is not reached)
        assert mock_run.call_count == 1

    @patch("subprocess.run")
    def test_rmlmapper_failure(self, mock_run, data_csv, sample_rml):
        """Test that RMLMapper failure (step 2) returns invalid result."""
        mock_run.side_effect = [
            # Step 1: yarrrml-parser succeeds
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            # Step 2: rmlmapper fails
            subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="",
                stderr="Error: Column 'foo' not found in CSV",
            ),
        ]

        validator = RMLValidator(use_docker=True)
        result = validator.validate_with_rmlmapper(sample_rml, data_csv)

        assert result.valid is False
        assert result.error_category == "missing_column"
        assert result.user_friendly_error is not None
        assert mock_run.call_count == 2


class TestYARRRMLParserDocker:
    """Tests for _run_yarrrml_parser_docker method."""

    @patch("subprocess.run")
    def test_yarrrml_parser_success(self, mock_run):
        """Test yarrrml-parser step returns valid on success."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr="",
        )

        validator = RMLValidator(use_docker=True)
        result = validator._run_yarrrml_parser_docker("/tmp/testdir", timeout=30)

        assert result.valid is True
        assert mock_run.called
        cmd = mock_run.call_args[0][0]
        assert "yarrrml-parser" in " ".join(cmd) or "yarrrml" in " ".join(cmd).lower()

    @patch("subprocess.run")
    def test_yarrrml_parser_failure(self, mock_run):
        """Test yarrrml-parser step returns invalid on failure."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="SyntaxError: Unexpected token } at line 5",
        )

        validator = RMLValidator(use_docker=True)
        result = validator._run_yarrrml_parser_docker("/tmp/testdir", timeout=30)

        assert result.valid is False
        assert result.error_category == "syntax_error"
        assert "Unexpected token" in result.error_message

    @patch("subprocess.run")
    def test_yarrrml_parser_uses_correct_docker_image(self, mock_run):
        """Test that yarrrml-parser uses the configured Docker image."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr="",
        )

        custom_image = "my-registry/yarrrml-parser:custom"
        validator = RMLValidator(
            use_docker=True,
            yarrrml_parser_docker_image=custom_image,
        )
        validator._run_yarrrml_parser_docker("/tmp/testdir", timeout=30)

        cmd = mock_run.call_args[0][0]
        assert custom_image in cmd


class TestIsEmptyRDFOutput:
    """Tests for _is_empty_rdf_output static helper."""

    def test_prefix_only_output(self):
        """Output with only @prefix declarations is empty."""
        output = (
            '@prefix rml: <http://w3id.org/rml/> .\n'
            '@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n'
            '@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n'
        )
        assert RMLValidator._is_empty_rdf_output(output) is True

    def test_prefix_and_base_only(self):
        """Output with @prefix and @base is still empty."""
        output = (
            '@base <http://example.org/> .\n'
            '@prefix ex: <http://example.org/> .\n'
        )
        assert RMLValidator._is_empty_rdf_output(output) is True

    def test_sparql_style_declarations(self):
        """SPARQL-style PREFIX/BASE declarations are also empty."""
        output = (
            'PREFIX ex: <http://example.org/>\n'
            'BASE <http://example.org/>\n'
        )
        assert RMLValidator._is_empty_rdf_output(output) is True

    def test_blank_string(self):
        """Completely empty string is empty."""
        assert RMLValidator._is_empty_rdf_output("") is True

    def test_whitespace_only(self):
        """Whitespace-only output is empty."""
        assert RMLValidator._is_empty_rdf_output("  \n  \n") is True

    def test_comments_only(self):
        """Output with only comments (and prefixes) is empty."""
        output = (
            '# This is a comment\n'
            '@prefix ex: <http://example.org/> .\n'
            '# Another comment\n'
        )
        assert RMLValidator._is_empty_rdf_output(output) is True

    def test_output_with_triples(self):
        """Output with actual triples is NOT empty."""
        output = (
            '@prefix ex: <http://example.org/> .\n'
            '\n'
            'ex:subject ex:predicate "value" .\n'
        )
        assert RMLValidator._is_empty_rdf_output(output) is False

    def test_output_with_blank_node(self):
        """Output with blank node triples is NOT empty."""
        output = (
            '@prefix ex: <http://example.org/> .\n'
            '\n'
            '_:b0 ex:predicate "value" .\n'
        )
        assert RMLValidator._is_empty_rdf_output(output) is False

    def test_realistic_rmlmapper_empty(self):
        """Realistic RMLMapper output that is empty (from user's report)."""
        output = (
            '@prefix cube: <https://cube.link/> .\n'
            '@prefix ex: <https://ld.stadt-zuerich.ch/statistics/> .\n'
            '@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n'
            '@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n'
            '@prefix rml: <http://w3id.org/rml/> .\n'
            '@prefix schema: <http://schema.org/> .\n'
            '@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n'
        )
        assert RMLValidator._is_empty_rdf_output(output) is True


class TestProcessResultEmptyOutput:
    """Tests for _process_result detecting empty RMLMapper output."""

    def test_prefix_only_output_is_invalid(self):
        """_process_result returns valid=False when output has only prefixes."""
        prefix_only = (
            '@prefix ex: <http://example.org/> .\n'
            '@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n'
        )

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".ttl", delete=False
        ) as f:
            f.write(prefix_only)
            output_path = Path(f.name)

        try:
            validator = RMLValidator()
            completed = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr="",
            )
            result = validator._process_result(completed, output_path)

            assert result.valid is False
            assert result.error_category == "empty_output"
            assert "no output triples" in result.error_message.lower()
            assert result.user_friendly_error is not None
        finally:
            output_path.unlink()

    def test_output_with_triples_is_valid(self):
        """_process_result returns valid=True when output contains triples."""
        rdf_with_data = (
            '@prefix ex: <http://example.org/> .\n'
            '\n'
            'ex:obs1 a ex:Observation ;\n'
            '  ex:value "42" .\n'
        )

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".ttl", delete=False
        ) as f:
            f.write(rdf_with_data)
            output_path = Path(f.name)

        try:
            validator = RMLValidator()
            completed = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr="",
            )
            result = validator._process_result(completed, output_path)

            assert result.valid is True
            assert result.rdf_output == rdf_with_data
        finally:
            output_path.unlink()

    def test_empty_file_is_invalid(self):
        """_process_result returns valid=False for a completely empty file."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".ttl", delete=False
        ) as f:
            f.write("")
            output_path = Path(f.name)

        try:
            validator = RMLValidator()
            completed = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr="",
            )
            result = validator._process_result(completed, output_path)

            assert result.valid is False
            assert result.error_category == "empty_output"
        finally:
            output_path.unlink()


class TestSampleCSVExtraction:
    """Tests for _extract_sample_csv."""

    def test_sample_csv_row_count(self, data_csv, sample_rml):
        """Test that sample CSV has the correct number of rows."""
        validator = RMLValidator()
        tmpdir = validator._extract_sample_csv(data_csv, "data.csv", sample_rows=3)

        try:
            sample_path = Path(tmpdir.name) / "data.csv"
            assert sample_path.exists()

            with open(sample_path, "r") as f:
                reader = csv.reader(f)
                rows = list(reader)

            # Header + 3 data rows = 4
            assert len(rows) == 4
            assert rows[0] == ["year", "region", "value"]
        finally:
            tmpdir.cleanup()

    def test_sample_csv_default_rows(self, data_csv, sample_rml):
        """Test default sample size (3 rows)."""
        validator = RMLValidator()
        tmpdir = validator._extract_sample_csv(data_csv, "data.csv")

        try:
            sample_path = Path(tmpdir.name) / "data.csv"
            with open(sample_path, "r") as f:
                reader = csv.reader(f)
                rows = list(reader)

            # Header + 3 data rows = 4 (default changed from 5 to 3)
            assert len(rows) == 4
        finally:
            tmpdir.cleanup()

    def test_semicolon_delimiter(self, semicolon_csv, sample_rml):
        """Test that semicolon delimiters are preserved."""
        validator = RMLValidator()
        tmpdir = validator._extract_sample_csv(
            semicolon_csv, "test.csv", sample_rows=2
        )

        try:
            sample_path = Path(tmpdir.name) / "test.csv"
            assert sample_path.exists()

            with open(sample_path, "r") as f:
                content = f.read()

            # Should contain semicolons (preserved delimiter)
            assert ";" in content

            # Parse and check row count
            with open(sample_path, "r") as f:
                reader = csv.reader(f, delimiter=";")
                rows = list(reader)

            assert len(rows) == 3  # header + 2 data rows
        finally:
            tmpdir.cleanup()

    def test_small_csv_fewer_rows_than_requested(self, small_csv, sample_rml):
        """Test extraction from CSV with fewer rows than requested."""
        validator = RMLValidator()
        tmpdir = validator._extract_sample_csv(
            small_csv, "data.csv", sample_rows=10
        )

        try:
            sample_path = Path(tmpdir.name) / "data.csv"
            with open(sample_path, "r") as f:
                reader = csv.reader(f)
                rows = list(reader)

            # Only header + 1 row available
            assert len(rows) == 2
        finally:
            tmpdir.cleanup()


class TestErrorCategorization:
    """Tests for _categorize_error."""

    def test_yarrrml_parse_error(self):
        """Test categorisation of YARRRML parse errors."""
        category, desc = RMLValidator._categorize_error(
            "yarrrml-parser: parse error at line 3"
        )
        assert category == "yarrrml_parse_error"
        assert "yarrrml" in desc.lower()

    def test_missing_column(self):
        """Test categorisation of missing column errors."""
        category, desc = RMLValidator._categorize_error(
            "Error: Column 'population' not found in CSV"
        )
        assert category == "missing_column"
        assert "column" in desc.lower()

    def test_invalid_iri(self):
        """Test categorisation of invalid IRI errors."""
        category, desc = RMLValidator._categorize_error(
            "Error: Invalid IRI generated from template"
        )
        assert category == "invalid_iri"
        assert "iri" in desc.lower() or "uri" in desc.lower()

    def test_type_mismatch(self):
        """Test categorisation of type mismatch errors."""
        category, desc = RMLValidator._categorize_error(
            "Error: Type mismatch — cannot cast 'abc' to xsd:integer"
        )
        assert category == "type_mismatch"
        assert "type" in desc.lower()

    def test_file_not_found(self):
        """Test categorisation of file not found errors."""
        category, desc = RMLValidator._categorize_error(
            "Error: File not found: data.csv"
        )
        assert category == "file_not_found"
        assert "file" in desc.lower()

    def test_syntax_error(self):
        """Test categorisation of syntax errors."""
        category, desc = RMLValidator._categorize_error(
            "Parse error at line 5: unexpected token"
        )
        assert category == "syntax_error"

    def test_unknown_error(self):
        """Test categorisation of unrecognised errors."""
        category, desc = RMLValidator._categorize_error(
            "Something completely unexpected happened"
        )
        assert category == "unknown"
        assert "Something completely unexpected" in desc


@pytest.mark.integration
class TestIntegrationYARRRML:
    """Integration tests that exercise the real Docker pipeline.

    These tests require:
    - Docker installed and running
    - rmlio/yarrrml-parser:latest image (pulled automatically)
    - rmlio/rmlmapper-java:latest image (pulled automatically)

    Skip with: pytest -m "not integration"
    """

    def test_valid_yarrrml_produces_rdf(self, docker_available, data_csv, sample_rml):
        """Tier 2 with real Docker — success path, asserts RDF output."""
        validator = RMLValidator(use_docker=True)
        result = validator.validate_with_rmlmapper(sample_rml, data_csv)

        assert result.valid is True
        assert result.rdf_output is not None
        assert len(result.rdf_output.strip()) > 0

    def test_full_validate_chain(self, docker_available, data_csv, sample_rml):
        """validate() runs both Tier 1 + Tier 2 end-to-end."""
        validator = RMLValidator(use_docker=True)
        result = validator.validate(sample_rml, data_csv)

        assert result.valid is True
        assert result.rdf_output is not None

    def test_small_csv_works(self, docker_available, small_csv, sample_rml):
        """1-row CSV still produces valid RDF."""
        validator = RMLValidator(use_docker=True)
        result = validator.validate_with_rmlmapper(sample_rml, small_csv)

        assert result.valid is True
        assert result.rdf_output is not None
