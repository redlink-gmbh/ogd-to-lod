"""RML validation using RMLMapper.

This module provides functionality to validate RML mappings by executing them
against sample CSV data using the RMLMapper tool.
"""

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ogd_to_lod.logging import get_logger


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
    """

    valid: bool
    rdf_output: str | None = None
    error_message: str | None = None
    warnings: list[str] | None = None


class RMLValidator:
    """Validates RML mappings using RMLMapper.

    This validator executes RML mappings against CSV data to verify:
    1. The RML syntax is valid
    2. The mapping can be executed against the source data
    3. Valid RDF is produced

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
            self._rmlmapper_jar = rmlmapper_jar or os.environ.get("RMLMAPPER_JAR")

    def validate(
        self,
        rml_content: str,
        csv_path: str,
        output_format: str = "turtle",
        timeout: int | None = None,
    ) -> ValidationResult:
        """Validate an RML mapping by executing it.

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
        timeout = timeout or self.DEFAULT_TIMEOUT

        # Check if RMLMapper is available
        if not self._use_docker and not self._rmlmapper_jar:
            logger.warning("RMLMapper JAR not configured, using syntax-only validation")
            return self._validate_syntax_only(rml_content)

        # Verify CSV file exists
        if not Path(csv_path).exists():
            return ValidationResult(
                valid=False,
                error_message=f"CSV file not found: {csv_path}",
            )

        # Create temporary files for RML and output
        with tempfile.TemporaryDirectory() as tmpdir:
            rml_file = Path(tmpdir) / "mapping.ttl"
            output_file = Path(tmpdir) / "output.ttl"

            # Write RML to temp file
            rml_file.write_text(rml_content)

            try:
                if self._use_docker:
                    result = self._run_docker(
                        rml_file, csv_path, output_file, output_format, timeout
                    )
                else:
                    result = self._run_jar(
                        rml_file, csv_path, output_file, output_format, timeout
                    )

                return result

            except subprocess.TimeoutExpired:
                return ValidationResult(
                    valid=False,
                    error_message=f"RMLMapper timed out after {timeout} seconds",
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
                )

    def _validate_syntax_only(self, rml_content: str) -> ValidationResult:
        """Validate RML syntax without executing the mapping.

        This is a fallback when RMLMapper is not available.
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
        # Build command
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

        # Execute RMLMapper
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=Path(csv_path).parent,  # Run in CSV directory
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
            csv_path: Path to CSV source file.
            output_file: Path for output file.
            output_format: Output format.
            timeout: Timeout in seconds.

        Returns:
            ValidationResult with execution outcome.
        """
        csv_dir = Path(csv_path).parent
        csv_filename = Path(csv_path).name

        # Build Docker command
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

        # Execute Docker
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
        # Check for errors
        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "Unknown error"
            # Clean up common RMLMapper error messages
            error_msg = self._clean_error_message(error_msg)

            logger.debug(f"RMLMapper failed: {error_msg}")

            return ValidationResult(
                valid=False,
                error_message=error_msg,
            )

        # Read output
        try:
            if output_file.exists():
                rdf_output = output_file.read_text()

                # Parse warnings from stderr
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
        # Remove Java stack traces for cleaner messages
        lines = error_msg.splitlines()
        cleaned_lines = []

        for line in lines:
            # Skip stack trace lines
            if line.strip().startswith("at "):
                continue
            if line.strip().startswith("Caused by:"):
                continue
            if "java." in line and "Exception" in line:
                # Keep the exception message but simplify
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
