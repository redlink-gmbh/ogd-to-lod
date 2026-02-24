"""YARRRML validation using a two-step Docker pipeline.

This module provides two-tier validation for YARRRML mappings:
- Tier 1 (syntax): yaml.safe_load() + structural checks (pure Python, fast).
- Tier 2 (Docker): yarrrml-parser converts YARRRML → Turtle RML, then
  RMLMapper executes it against sample CSV data. Escalates to the user on
  failure since data-fit issues need human judgement.
"""

import csv
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ogd_to_lod.logging import get_logger

# Import the CSV source placeholder constant
CSV_SOURCE_PLACEHOLDER = "{{CSV_SOURCE}}"

logger = get_logger(__name__)


class RMLValidationError(Exception):
    """Raised when RML validation fails."""

    pass


class RMLMapperNotFoundError(RMLValidationError):
    """Raised when RMLMapper is not found."""

    pass


@dataclass
class ValidationResult:
    """Result of RML validation.

    Attributes:
        valid: Whether the RML is valid.
        rdf_output: Generated RDF output if valid (Turtle format).
        error_message: Error message if invalid.
        warnings: List of warnings from validation.
        error_category: Categorised error type (e.g. "missing_column", "invalid_iri").
        user_friendly_error: Human-readable explanation of the error.
    """

    valid: bool
    rdf_output: str | None = None
    error_message: str | None = None
    warnings: list[str] | None = None
    error_category: str | None = None
    user_friendly_error: str | None = None


class RMLValidator:
    """Validates YARRRML mappings using a two-step Docker pipeline.

    This validator supports two tiers:
    1. Syntax validation via yaml.safe_load() + structural checks (fast, no Docker)
    2. Full validation via yarrrml-parser → RMLMapper against sample CSV data

    Tier 2 requires Docker with the yarrrml-parser and rmlmapper-java images.
    """

    # Default timeout for Docker execution (in seconds)
    DEFAULT_TIMEOUT = 60

    def __init__(
        self,
        rmlmapper_jar: str | None = None,
        use_docker: bool = False,
        docker_image: str = "rmlio/rmlmapper-java:latest",
        yarrrml_parser_docker_image: str = "rmlio/yarrrml-parser:latest",
    ):
        """Initialize the validator.

        Args:
            rmlmapper_jar: Path to RMLMapper JAR file (kept for backward
                compatibility; unused when use_docker=True).
            use_docker: If True, use Docker for Tier 2 validation.
            docker_image: Docker image for RMLMapper.
            yarrrml_parser_docker_image: Docker image for yarrrml-parser.
        """
        self._use_docker = use_docker
        self._docker_image = docker_image
        self._yarrrml_parser_image = yarrrml_parser_docker_image

        if use_docker:
            self._rmlmapper_jar = None
        else:
            jar = rmlmapper_jar or os.environ.get("RMLMAPPER_JAR")
            self._rmlmapper_jar = str(Path(jar).resolve()) if jar else None

    # ── Tier 1: Syntax validation ────────────────────────────────────────

    def validate_syntax(self, rml_content: str) -> ValidationResult:
        """Validate YARRRML syntax using yaml.safe_load() + structural checks (Tier 1).

        This is a fast, cheap check that catches YAML syntax errors and missing
        required keys without needing Docker or CSV data. Suitable for auto-retry
        when the AI produces invalid YARRRML.

        Args:
            rml_content: YARRRML mapping in YAML format.

        Returns:
            ValidationResult with syntax validation outcome.
        """
        return self._validate_yaml_syntax(rml_content)

    # ── Tier 2: Docker two-step validation ───────────────────────────────

    def validate_with_rmlmapper(
        self,
        rml_content: str,
        csv_path: str,
        sample_rows: int = 3,
        output_format: str = "turtle",
        timeout: int | None = None,
    ) -> ValidationResult:
        """Validate YARRRML by converting and executing it against sample CSV (Tier 2).

        Two-step Docker pipeline:
        1. yarrrml-parser converts YARRRML → Turtle RML
        2. RMLMapper executes the Turtle RML against sample CSV data

        All files share a single temp directory mounted at /data in both containers.

        Args:
            rml_content: YARRRML mapping (may contain {{CSV_SOURCE}} placeholder).
            csv_path: Path to the source CSV file.
            sample_rows: Number of data rows to include in sample (default: 3).
            output_format: Output format for RDF (turtle, nquads, jsonld).
            timeout: Timeout in seconds per Docker step (default: 60).

        Returns:
            ValidationResult with validation outcome, including error
            categorisation on failure.
        """
        timeout = timeout or self.DEFAULT_TIMEOUT

        # When Docker is not enabled, skip Tier 2 gracefully
        if not self._use_docker:
            logger.warning("Docker not enabled, skipping Tier 2 validation")
            return ValidationResult(
                valid=True,
                warnings=[
                    "Docker not enabled — Tier 2 validation skipped. "
                    "Set rmlmapper_use_docker: true in config to enable full validation."
                ],
            )

        # Verify CSV file exists
        if not Path(csv_path).exists():
            return ValidationResult(
                valid=False,
                error_message=f"CSV file not found: {csv_path}",
                error_category="file_not_found",
                user_friendly_error=f"The CSV file '{csv_path}' does not exist.",
            )

        # Extract sample CSV into a shared temp directory
        sample_filename = "sample.csv"
        tmpdir = self._extract_sample_csv(csv_path, sample_filename, sample_rows)

        try:
            yarrrml_file = Path(tmpdir.name) / "mapping.yarrrml.yaml"
            output_file = Path(tmpdir.name) / "output.ttl"

            # Replace the CSV source placeholder with the sample filename
            yarrrml_with_source = self._replace_csv_placeholder(rml_content, sample_filename)
            yarrrml_file.write_text(yarrrml_with_source)

            try:
                # Step 1: yarrrml-parser converts YARRRML → Turtle RML
                parser_result = self._run_yarrrml_parser_docker(tmpdir.name, timeout)
                if not parser_result.valid:
                    return parser_result

                # Step 2: RMLMapper executes Turtle RML against sample CSV
                result = self._run_rmlmapper_docker(
                    tmpdir.name, output_file, output_format, timeout
                )

                # Categorise errors if validation failed
                if not result.valid and result.error_message:
                    category, friendly = self._categorize_error(result.error_message)
                    result.error_category = category
                    result.user_friendly_error = friendly

                return result

            except subprocess.TimeoutExpired:
                return ValidationResult(
                    valid=False,
                    error_message=f"Validation timed out after {timeout} seconds",
                    error_category="timeout",
                    user_friendly_error=(
                        f"Validation took longer than {timeout} seconds. "
                        "The mapping may be too complex or contain infinite loops."
                    ),
                )
            except Exception as e:
                logger.error(f"Unexpected error during validation: {e}")
                return ValidationResult(
                    valid=False,
                    error_message=f"Validation error: {e}",
                    error_category="unknown",
                    user_friendly_error=f"An unexpected error occurred: {e}",
                )
        finally:
            tmpdir.cleanup()

    # ── Backward-compatible wrapper ──────────────────────────────────────

    def validate(
        self,
        rml_content: str,
        csv_path: str,
        output_format: str = "turtle",
        timeout: int | None = None,
    ) -> ValidationResult:
        """Validate a YARRRML mapping (backward-compatible).

        Runs both tiers: YAML syntax first, then Docker two-step if available.

        Args:
            rml_content: YARRRML mapping in YAML format.
            csv_path: Path to the source CSV file.
            output_format: Output format for RDF (turtle, nquads, jsonld).
            timeout: Timeout in seconds (default: 60).

        Returns:
            ValidationResult with validation outcome.
        """
        # Tier 1: syntax check
        syntax_result = self.validate_syntax(rml_content)
        if not syntax_result.valid:
            return syntax_result

        # Tier 2: Docker two-step
        return self.validate_with_rmlmapper(
            rml_content, csv_path, output_format=output_format, timeout=timeout
        )

    # ── Sample CSV extraction ────────────────────────────────────────────

    def _extract_sample_csv(
        self,
        csv_path: str,
        sample_filename: str,
        sample_rows: int = 3,
    ) -> tempfile.TemporaryDirectory:
        """Extract header + first N rows from a CSV for validation.

        The sample file is written into a temp directory using the specified
        sample filename.

        Args:
            csv_path: Path to the full CSV file.
            sample_filename: Filename to use for the sample CSV.
            sample_rows: Number of data rows to include.

        Returns:
            TemporaryDirectory containing the sample CSV. Caller must manage
            its lifecycle (cleanup).
        """
        tmpdir = tempfile.TemporaryDirectory()

        try:
            source_path = Path(csv_path)
            dest_path = Path(tmpdir.name) / sample_filename

            # Detect delimiter by reading the first line
            with open(source_path, "r", newline="", encoding="utf-8") as f:
                sample = f.read(8192)

            try:
                dialect = csv.Sniffer().sniff(sample)
                delimiter = dialect.delimiter
            except csv.Error:
                delimiter = ","

            # Read header + first N rows
            with open(source_path, "r", newline="", encoding="utf-8") as src:
                reader = csv.reader(src, delimiter=delimiter)
                rows = []
                for i, row in enumerate(reader):
                    rows.append(row)
                    if i >= sample_rows:  # header + sample_rows data rows
                        break

            # Write sample CSV (create intermediate dirs for paths like "data/file.csv")
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with open(dest_path, "w", newline="", encoding="utf-8") as dst:
                writer = csv.writer(dst, delimiter=delimiter)
                writer.writerows(rows)

            logger.debug(
                f"Extracted sample CSV: {len(rows)} rows "
                f"(incl. header) → {dest_path}"
            )

        except Exception:
            tmpdir.cleanup()
            raise

        return tmpdir

    @staticmethod
    def _replace_csv_placeholder(rml_content: str, csv_filename: str) -> str:
        """Replace CSV source placeholder with actual filename.

        Replaces all occurrences of {{CSV_SOURCE}} with the provided filename.
        If the placeholder is not found, the YARRRML is returned unchanged.

        Args:
            rml_content: YARRRML content possibly containing {{CSV_SOURCE}} placeholder.
            csv_filename: The actual CSV filename to substitute.

        Returns:
            YARRRML content with placeholder replaced.
        """
        if CSV_SOURCE_PLACEHOLDER in rml_content:
            logger.debug(f"Replacing {CSV_SOURCE_PLACEHOLDER} with {csv_filename}")
            return rml_content.replace(CSV_SOURCE_PLACEHOLDER, csv_filename)
        return rml_content

    # ── Error categorisation ─────────────────────────────────────────────

    @staticmethod
    def _categorize_error(error_message: str) -> tuple[str, str]:
        """Categorise an RMLMapper error into a user-friendly bucket.

        Args:
            error_message: Raw error message from RMLMapper or yarrrml-parser.

        Returns:
            Tuple of (category, user_friendly_description).
        """
        msg_lower = error_message.lower()

        if "yarrrml" in msg_lower or "yaml" in msg_lower:
            return (
                "yarrrml_parse_error",
                "The YARRRML mapping could not be parsed. "
                "Check the YAML syntax and YARRRML structure.",
            )

        if "column" in msg_lower and ("not found" in msg_lower or "missing" in msg_lower):
            return (
                "missing_column",
                "The mapping references a CSV column that does not exist. "
                "Check that column names match the CSV header exactly "
                "(including case and whitespace).",
            )

        if "iri" in msg_lower or "uri" in msg_lower or "invalid url" in msg_lower:
            return (
                "invalid_iri",
                "The mapping generates an invalid IRI/URI. "
                "This usually means a template contains characters that "
                "are not allowed in URIs (spaces, special characters).",
            )

        if "type" in msg_lower and ("mismatch" in msg_lower or "cast" in msg_lower):
            return (
                "type_mismatch",
                "A data value cannot be converted to the expected type "
                "(e.g. text where a number is expected). Check the datatype "
                "annotations in the mapping.",
            )

        if "file" in msg_lower and "not found" in msg_lower:
            return (
                "file_not_found",
                "The mapping references a file that could not be found. "
                "Ensure the access field filename matches the actual CSV file.",
            )

        if "parse" in msg_lower or "syntax" in msg_lower:
            return (
                "syntax_error",
                "The mapping has a syntax error that could not be parsed.",
            )

        return (
            "unknown",
            f"Validation failed: {error_message[:200]}",
        )

    # ── Output analysis ─────────────────────────────────────────────────

    @staticmethod
    def _is_empty_rdf_output(rdf_output: str) -> bool:
        """Check whether RDF output contains only prefix/base declarations.

        RMLMapper exits successfully even when no triples are generated
        (e.g. when CSV column references don't match due to a wrong
        delimiter).  The output file will contain @prefix declarations
        but no actual data triples.

        Args:
            rdf_output: RDF output string (Turtle format).

        Returns:
            True if the output has no data triples.
        """
        for line in rdf_output.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", "@prefix", "@base")):
                continue
            # PREFIX / BASE (SPARQL-style declarations)
            upper = stripped.upper()
            if upper.startswith("PREFIX ") or upper.startswith("BASE "):
                continue
            return False  # Found actual content
        return True

    # ── Internal helpers ─────────────────────────────────────────────────

    def _validate_yaml_syntax(self, rml_content: str) -> ValidationResult:
        """Validate YARRRML syntax using yaml.safe_load() + structural checks.

        Args:
            rml_content: YARRRML mapping in YAML format.

        Returns:
            ValidationResult with syntax validation outcome.
        """
        try:
            doc = yaml.safe_load(rml_content)
        except yaml.YAMLError as e:
            return ValidationResult(
                valid=False,
                error_message=f"YARRRML syntax error: {e}",
            )

        if not isinstance(doc, dict):
            return ValidationResult(
                valid=False,
                error_message="YARRRML must be a YAML mapping (dict at top level)",
            )

        if "mappings" not in doc:
            return ValidationResult(
                valid=False,
                error_message="YARRRML must have a 'mappings' key",
            )

        warnings = []

        if "prefixes" not in doc:
            warnings.append("No 'prefixes' block found in YARRRML")

        # Check each mapping entry for required keys
        mappings = doc.get("mappings", {})
        if isinstance(mappings, dict):
            for name, mapping in mappings.items():
                if not isinstance(mapping, dict):
                    continue
                if "sources" not in mapping:
                    warnings.append(f"Mapping '{name}' has no 'sources'")
                if "s" not in mapping:
                    warnings.append(f"Mapping '{name}' has no subject ('s')")
                if "po" not in mapping:
                    warnings.append(f"Mapping '{name}' has no predicate-objects ('po')")

        return ValidationResult(
            valid=True,
            warnings=warnings if warnings else None,
        )

    def _run_yarrrml_parser_docker(
        self, tmpdir_name: str, timeout: int
    ) -> ValidationResult:
        """Run yarrrml-parser Docker container to convert YARRRML → Turtle RML.

        Reads /data/mapping.yarrrml.yaml and writes /data/mapping.ttl.

        Args:
            tmpdir_name: Path to the shared temp directory mounted at /data.
            timeout: Timeout in seconds.

        Returns:
            ValidationResult(valid=True) on success, ValidationResult(valid=False)
            with error details on failure.
        """
        cmd = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{tmpdir_name}:/data",
            self._yarrrml_parser_image,
            "-i",
            "/data/mapping.yarrrml.yaml",
            "-o",
            "/data/mapping.ttl",
        ]

        logger.debug(f"Running yarrrml-parser: {' '.join(cmd)}")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "Unknown yarrrml-parser error"
            logger.debug(f"yarrrml-parser failed: {error_msg}")
            return ValidationResult(
                valid=False,
                error_message=error_msg,
                error_category="syntax_error",
                user_friendly_error=(
                    "The YARRRML mapping could not be parsed. "
                    "Check the YAML syntax and YARRRML structure."
                ),
            )

        return ValidationResult(valid=True)

    def _run_rmlmapper_docker(
        self,
        tmpdir_name: str,
        output_file: Path,
        output_format: str,
        timeout: int,
    ) -> ValidationResult:
        """Run RMLMapper Docker container to execute Turtle RML → RDF output.

        Reads /data/mapping.ttl and /data/sample.csv, writes /data/output.ttl.

        Args:
            tmpdir_name: Path to the shared temp directory mounted at /data.
            output_file: Expected output file path (for reading results).
            output_format: Output format (turtle, nquads, jsonld).
            timeout: Timeout in seconds.

        Returns:
            ValidationResult with execution outcome.
        """
        cmd = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{tmpdir_name}:/data",
            self._docker_image,
            "-m",
            "/data/mapping.ttl",
            "-o",
            "/data/output.ttl",
            "-s",
            output_format,
        ]

        logger.debug(f"Running RMLMapper: {' '.join(cmd)}")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        return self._process_result(result, output_file)

    def _run_jar_in_dir(
        self,
        rml_file: Path,
        working_dir: str,
        output_file: Path,
        output_format: str,
        timeout: int,
    ) -> ValidationResult:
        """Execute RMLMapper JAR file with a specific working directory.

        Kept for backward compatibility; not called in the default validation path.

        Args:
            rml_file: Path to RML mapping file.
            working_dir: Directory containing the sample CSV.
            output_file: Path for output file.
            output_format: Output format.
            timeout: Timeout in seconds.

        Returns:
            ValidationResult with execution outcome.
        """
        cmd = [
            "java",
            "-jar",
            self._rmlmapper_jar,
            "-m",
            str(rml_file),
            "-o",
            str(output_file),
            "-s",
            output_format,
        ]

        logger.debug(f"Running RMLMapper JAR: {' '.join(cmd)}")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=working_dir,
        )

        return self._process_result(result, output_file)

    def _process_result(
        self, result: subprocess.CompletedProcess, output_file: Path
    ) -> ValidationResult:
        """Process RMLMapper execution result.

        Args:
            result: Subprocess result.
            output_file: Path to output file.

        Returns:
            ValidationResult based on execution outcome.
        """
        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "Unknown error"
            error_msg = self._clean_error_message(error_msg)

            logger.debug(f"RMLMapper failed: {error_msg}")

            return ValidationResult(
                valid=False,
                error_message=error_msg,
            )

        try:
            if output_file.exists():
                rdf_output = output_file.read_text()

                # Check for empty output (only prefixes, no data triples)
                if self._is_empty_rdf_output(rdf_output):
                    return ValidationResult(
                        valid=False,
                        error_message=(
                            "RMLMapper produced no output triples "
                            "— only prefix declarations."
                        ),
                        error_category="empty_output",
                        user_friendly_error=(
                            "The mapping executed successfully but produced "
                            "no RDF triples. This usually means the column "
                            "references in the mapping don't match the CSV "
                            "headers (check delimiter, column names, and "
                            "letter case)."
                        ),
                    )

                warnings = None
                if result.stderr:
                    warning_lines = [
                        line
                        for line in result.stderr.splitlines()
                        if "WARN" in line or "warning" in line.lower()
                    ]
                    if warning_lines:
                        warnings = warning_lines

                return ValidationResult(
                    valid=True,
                    rdf_output=rdf_output,
                    warnings=warnings,
                )
            else:
                return ValidationResult(
                    valid=False,
                    error_message="RMLMapper did not produce output",
                )

        except Exception as e:
            return ValidationResult(
                valid=False,
                error_message=f"Failed to read RMLMapper output: {e}",
            )

    def _clean_error_message(self, error_msg: str) -> str:
        """Clean up error messages for better readability.

        Args:
            error_msg: Raw error message.

        Returns:
            Cleaned error message.
        """
        lines = error_msg.splitlines()
        cleaned_lines = []

        for line in lines:
            if line.strip().startswith("at "):
                continue
            if line.strip().startswith("Caused by:"):
                continue
            if "java." in line and "Exception" in line:
                if ":" in line:
                    cleaned_lines.append(line.split(":")[-1].strip())
                continue
            cleaned_lines.append(line)

        return "\n".join(cleaned_lines).strip()

    def is_available(self) -> bool:
        """Check if RMLMapper Docker image is available.

        Returns:
            True if the RMLMapper Docker image is present locally.
        """
        if self._use_docker:
            try:
                result = subprocess.run(
                    ["docker", "images", "-q", self._docker_image],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                return bool(result.stdout.strip())
            except Exception:
                return False
        else:
            if not self._rmlmapper_jar:
                return False
            return Path(self._rmlmapper_jar).exists()


def validate_rml(
    rml_content: str,
    csv_path: str,
    rmlmapper_jar: str | None = None,
    use_docker: bool = False,
) -> ValidationResult:
    """Convenience function to validate a YARRRML mapping.

    Args:
        rml_content: YARRRML mapping in YAML format.
        csv_path: Path to the source CSV file.
        rmlmapper_jar: Path to RMLMapper JAR (optional, unused when Docker enabled).
        use_docker: Use Docker for Tier 2 validation.

    Returns:
        ValidationResult with validation outcome.
    """
    validator = RMLValidator(rmlmapper_jar=rmlmapper_jar, use_docker=use_docker)
    return validator.validate(rml_content, csv_path)
