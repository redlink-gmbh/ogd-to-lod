"""RML validation using RMLMapper.

This module provides two-tier validation for RML mappings:
- Tier 1 (syntax): Cheap rdflib parse check, suitable for auto-retry on AI errors.
- Tier 2 (RMLMapper): Thorough data-aware check using sample CSV data, escalates to
  user on failure since data-fit issues need human judgement.
"""

import csv
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

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
    """Validates RML mappings using RMLMapper.

    This validator supports two tiers:
    1. Syntax validation via rdflib (fast, no external dependencies)
    2. Full validation via RMLMapper against sample CSV data

    The validator uses RMLMapper, a Java-based tool that must be available
    either as a JAR file or via Docker.
    """

    # Default timeout for RMLMapper execution (in seconds)
    DEFAULT_TIMEOUT = 60

    def __init__(
        self,
        rmlmapper_jar: str | None = None,
        use_docker: bool = False,
        docker_image: str = "rmlio/rmlmapper-java:latest",
    ):
        """Initialize the validator.

        Args:
            rmlmapper_jar: Path to RMLMapper JAR file. If not provided,
                will look for RMLMAPPER_JAR environment variable.
            use_docker: If True, use Docker instead of JAR file.
            docker_image: Docker image to use (only if use_docker=True).
        """
        self._use_docker = use_docker
        self._docker_image = docker_image

        if use_docker:
            self._rmlmapper_jar = None
        else:
            jar = rmlmapper_jar or os.environ.get("RMLMAPPER_JAR")
            # Resolve to absolute so the path works even when cwd changes
            # (e.g. when running RMLMapper in a temp directory).
            self._rmlmapper_jar = str(Path(jar).resolve()) if jar else None

    # ── Tier 1: Syntax validation ────────────────────────────────────────

    def validate_syntax(self, rml_content: str) -> ValidationResult:
        """Validate RML Turtle syntax using rdflib (Tier 1).

        This is a fast, cheap check that catches syntax errors without needing
        RMLMapper or CSV data. Suitable for auto-retry when the AI produces
        invalid Turtle.

        Args:
            rml_content: RML mapping in Turtle format.

        Returns:
            ValidationResult with syntax validation outcome.
        """
        return self._validate_syntax_only(rml_content)

    # ── Tier 2: RMLMapper validation ─────────────────────────────────────

    def validate_with_rmlmapper(
        self,
        rml_content: str,
        csv_path: str,
        sample_rows: int = 3,
        output_format: str = "turtle",
        timeout: int | None = None,
    ) -> ValidationResult:
        """Validate RML by executing it against sample CSV data (Tier 2).

        Extracts the first N rows from the CSV, writes them to a temp directory
        with a sample filename, then runs RMLMapper. If the RML contains the
        {{CSV_SOURCE}} placeholder, it will be replaced with the sample filename.

        Args:
            rml_content: RML mapping in Turtle format (may contain {{CSV_SOURCE}} placeholder).
            csv_path: Path to the source CSV file.
            sample_rows: Number of data rows to include in sample (default: 3).
            output_format: Output format for RDF (turtle, nquads, jsonld).
            timeout: Timeout in seconds (default: 60).

        Returns:
            ValidationResult with validation outcome, including error
            categorisation on failure.
        """
        timeout = timeout or self.DEFAULT_TIMEOUT

        # Check if RMLMapper is available
        if not self._use_docker and not self._rmlmapper_jar:
            logger.warning("RMLMapper not configured, skipping Tier 2 validation")
            return ValidationResult(
                valid=True,
                warnings=["RMLMapper not configured — Tier 2 validation skipped"],
            )

        if not self._use_docker and not Path(self._rmlmapper_jar).exists():
            logger.warning(
                f"RMLMapper JAR not found at {self._rmlmapper_jar}, "
                "skipping Tier 2 validation"
            )
            return ValidationResult(
                valid=True,
                warnings=[
                    f"RMLMapper JAR not found at {self._rmlmapper_jar} "
                    "— Tier 2 validation skipped. "
                    "Run scripts/setup-rmlmapper.sh to download it."
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

        # Extract sample CSV and run RMLMapper in temp directory
        # Use a simple sample filename for the CSV
        sample_filename = "sample.csv"
        tmpdir = self._extract_sample_csv(csv_path, sample_filename, sample_rows)
        try:
            rml_file = Path(tmpdir.name) / "mapping.ttl"
            output_file = Path(tmpdir.name) / "output.ttl"

            # Replace the CSV source placeholder with the sample filename
            rml_with_source = self._replace_csv_placeholder(rml_content, sample_filename)
            rml_file.write_text(self._ensure_base_directive(rml_with_source))

            try:
                if self._use_docker:
                    result = self._run_docker(
                        rml_file, tmpdir.name, output_file, output_format, timeout
                    )
                else:
                    result = self._run_jar_in_dir(
                        rml_file, tmpdir.name, output_file, output_format, timeout
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
                    error_message=f"RMLMapper timed out after {timeout} seconds",
                    error_category="timeout",
                    user_friendly_error=(
                        f"RMLMapper took longer than {timeout} seconds. "
                        "The mapping may be too complex or contain infinite loops."
                    ),
                )
            except FileNotFoundError as e:
                raise RMLMapperNotFoundError(
                    f"RMLMapper not found: {e}. "
                    "Ensure Java is installed and RMLMAPPER_JAR is set."
                ) from e
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
        """Validate an RML mapping by executing it (backward-compatible).

        Runs both tiers: syntax first, then RMLMapper if available.

        Args:
            rml_content: RML mapping in Turtle format.
            csv_path: Path to the source CSV file.
            output_format: Output format for RDF (turtle, nquads, jsonld).
            timeout: Timeout in seconds (default: 60).

        Returns:
            ValidationResult with validation outcome.

        Raises:
            RMLMapperNotFoundError: If RMLMapper is not available.
            RMLValidationError: If validation fails due to configuration issues.
        """
        # Tier 1: syntax check
        syntax_result = self.validate_syntax(rml_content)
        if not syntax_result.valid:
            return syntax_result

        # Tier 2: RMLMapper check
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
        If the placeholder is not found, the RML is returned unchanged (for
        backward compatibility with mappings that have hardcoded paths).

        Args:
            rml_content: RML content possibly containing {{CSV_SOURCE}} placeholder.
            csv_filename: The actual CSV filename to substitute.

        Returns:
            RML content with placeholder replaced.
        """
        if CSV_SOURCE_PLACEHOLDER in rml_content:
            logger.debug(f"Replacing {CSV_SOURCE_PLACEHOLDER} with {csv_filename}")
            return rml_content.replace(CSV_SOURCE_PLACEHOLDER, csv_filename)
        return rml_content

    @staticmethod
    def _ensure_base_directive(rml_content: str) -> str:
        """Prepend a @base directive if the RML uses relative IRIs without one.

        RMLMapper (RDF4J) rejects relative IRIs like <#LogicalSource> when no
        @base is declared.  Adding a dummy base lets them resolve correctly.

        Args:
            rml_content: RML content in Turtle format.

        Returns:
            RML content with @base prepended if needed.
        """
        has_base = bool(re.search(r'@base\s+<', rml_content, re.IGNORECASE))
        has_relative_iris = bool(re.search(r'<#\w', rml_content))
        if has_relative_iris and not has_base:
            logger.debug("Injecting @base directive for relative IRIs")
            return '@base <http://example.org/mapping/> .\n' + rml_content
        return rml_content

    @staticmethod
    def _get_rml_source_filename(rml_content: str, csv_path: str) -> str:
        """Parse the source filename from RML content.

        Looks for ``rml:source "filename"`` (simple form) or
        ``csvw:url "filename"`` (CSVW dialect form). Falls back to the
        basename of the provided *csv_path*.

        Args:
            rml_content: RML content in Turtle format.
            csv_path: Fallback CSV path.

        Returns:
            The filename the RML expects.
        """
        # Simple form: rml:source "filename"
        match = re.search(r'rml:source\s+"([^"]+)"', rml_content)
        if match:
            return match.group(1)
        # CSVW form: csvw:url "filename"
        match = re.search(r'csvw:url\s+"([^"]+)"', rml_content)
        if match:
            return match.group(1)
        return Path(csv_path).name

    # ── Error categorisation ─────────────────────────────────────────────

    @staticmethod
    def _categorize_error(error_message: str) -> tuple[str, str]:
        """Categorise an RMLMapper error into a user-friendly bucket.

        Args:
            error_message: Raw error message from RMLMapper.

        Returns:
            Tuple of (category, user_friendly_description).
        """
        msg_lower = error_message.lower()

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
                "Ensure the rml:source filename matches the actual CSV file.",
            )

        if "parse" in msg_lower or "syntax" in msg_lower:
            return (
                "syntax_error",
                "The RML mapping has a syntax error that RMLMapper could "
                "not parse. This may be a Turtle formatting issue.",
            )

        return (
            "unknown",
            f"RMLMapper validation failed: {error_message[:200]}",
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

    def _validate_syntax_only(self, rml_content: str) -> ValidationResult:
        """Validate RML syntax without executing the mapping.

        Uses rdflib to parse the Turtle syntax.

        Args:
            rml_content: RML mapping in Turtle format.

        Returns:
            ValidationResult with syntax validation outcome.
        """
        try:
            from rdflib import Graph

            g = Graph()
            g.parse(data=rml_content, format="turtle")

            # Check for required RML components
            warnings = []

            # Check for logical source
            logical_sources = list(
                g.query(
                    """
                SELECT ?source WHERE {
                    ?map rml:logicalSource ?source .
                }
            """
                )
            )
            if not logical_sources:
                warnings.append("No logical source (rml:logicalSource) found")

            # Check for subject map
            subject_maps = list(
                g.query(
                    """
                SELECT ?map WHERE {
                    ?map rr:subjectMap ?sm .
                }
            """
                )
            )
            if not subject_maps:
                warnings.append("No subject map (rr:subjectMap) found")

            return ValidationResult(
                valid=True,
                rdf_output=None,
                warnings=warnings if warnings else None,
            )

        except Exception as e:
            return ValidationResult(
                valid=False,
                error_message=f"RML syntax error: {e}",
            )

    def _run_jar_in_dir(
        self,
        rml_file: Path,
        working_dir: str,
        output_file: Path,
        output_format: str,
        timeout: int,
    ) -> ValidationResult:
        """Execute RMLMapper JAR file with a specific working directory.

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

        logger.debug(f"Running RMLMapper: {' '.join(cmd)}")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=working_dir,
        )

        return self._process_result(result, output_file)

    def _run_jar(
        self,
        rml_file: Path,
        csv_path: str,
        output_file: Path,
        output_format: str,
        timeout: int,
    ) -> ValidationResult:
        """Execute RMLMapper JAR file.

        Args:
            rml_file: Path to RML mapping file.
            csv_path: Path to CSV source file.
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

        logger.debug(f"Running RMLMapper: {' '.join(cmd)}")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=Path(csv_path).parent,
        )

        return self._process_result(result, output_file)

    def _run_docker(
        self,
        rml_file: Path,
        csv_path: str,
        output_file: Path,
        output_format: str,
        timeout: int,
    ) -> ValidationResult:
        """Execute RMLMapper via Docker.

        Args:
            rml_file: Path to RML mapping file.
            csv_path: Path to CSV source file or directory containing CSV.
            output_file: Path for output file.
            output_format: Output format.
            timeout: Timeout in seconds.

        Returns:
            ValidationResult with execution outcome.
        """
        csv_dir = Path(csv_path) if Path(csv_path).is_dir() else Path(csv_path).parent

        cmd = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{rml_file.parent}:/data",
            "-v",
            f"{csv_dir}:/csv:ro",
            self._docker_image,
            "-m",
            "/data/mapping.ttl",
            "-o",
            "/data/output.ttl",
            "-s",
            output_format,
        ]

        logger.debug(f"Running RMLMapper via Docker: {' '.join(cmd)}")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
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
        """Clean up RMLMapper error messages for better readability.

        Args:
            error_msg: Raw error message from RMLMapper.

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
        """Check if RMLMapper is available.

        Returns:
            True if RMLMapper can be executed.
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
    """Convenience function to validate RML.

    Args:
        rml_content: RML mapping in Turtle format.
        csv_path: Path to the source CSV file.
        rmlmapper_jar: Path to RMLMapper JAR (optional).
        use_docker: Use Docker instead of JAR.

    Returns:
        ValidationResult with validation outcome.
    """
    validator = RMLValidator(rmlmapper_jar=rmlmapper_jar, use_docker=use_docker)
    return validator.validate(rml_content, csv_path)
