"""Shared pytest fixtures for the ogd-to-lod test suite."""

import shutil
import subprocess
from pathlib import Path

import pytest


# ── Paths to persistent fixture files ───────────────────────────────────

@pytest.fixture(scope="session")
def data_dir():
    """Root directory containing persistent test fixtures."""
    return Path(__file__).parent / "data"


@pytest.fixture(scope="session")
def data_csv(data_dir):
    """Path (str) to the 7-row comma-delimited CSV fixture."""
    return str(data_dir / "data.csv")


@pytest.fixture(scope="session")
def semicolon_csv(data_dir):
    """Path (str) to the 3-row semicolon-delimited CSV fixture."""
    return str(data_dir / "semicolon.csv")


@pytest.fixture(scope="session")
def small_csv(data_dir):
    """Path (str) to the 1-row comma-delimited CSV fixture."""
    return str(data_dir / "small.csv")


@pytest.fixture(scope="session")
def sample_rml(data_dir):
    """The sample YARRRML mapping content used across validation tests."""
    return (data_dir / "sample.yarrrml.yaml").read_text()


# ── RMLMapper availability fixtures (for integration tests) ────────────

@pytest.fixture(scope="session")
def rmlmapper_jar():
    """Path to the RMLMapper JAR, or skip if not present."""
    jar_path = Path(__file__).parent.parent / "tools" / "rmlmapper.jar"
    if not jar_path.exists():
        pytest.skip("RMLMapper JAR not found at tools/rmlmapper.jar")
    return str(jar_path)


@pytest.fixture(scope="session")
def java_available():
    """Assert that java is on PATH, or skip."""
    if shutil.which("java") is None:
        pytest.skip("Java runtime not found on PATH")


@pytest.fixture(scope="session")
def rmlmapper_available(rmlmapper_jar, java_available):
    """Ensure both JAR and Java are present; return the JAR path."""
    return rmlmapper_jar


@pytest.fixture(scope="session")
def docker_available():
    """Assert that Docker is available and running, or skip."""
    if shutil.which("docker") is None:
        pytest.skip("Docker not found on PATH")
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            pytest.skip("Docker daemon is not running")
    except Exception:
        pytest.skip("Docker is not available")
