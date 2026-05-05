"""Tests for configuration management."""

import os
import tempfile
from pathlib import Path

import pytest

from ogd_to_lod.config import (
    Config,
    _substitute_env_vars,
    load_config,
)


class TestEnvVarSubstitution:
    """Tests for environment variable substitution."""

    def test_substitute_dollar_brace_syntax(self, monkeypatch):
        """Test ${VAR} syntax substitution."""
        monkeypatch.setenv("TEST_VAR", "hello")
        result = _substitute_env_vars("prefix-${TEST_VAR}-suffix")
        assert result == "prefix-hello-suffix"

    def test_substitute_dollar_syntax(self, monkeypatch):
        """Test $VAR syntax substitution."""
        monkeypatch.setenv("TEST_VAR", "world")
        result = _substitute_env_vars("prefix-$TEST_VAR-suffix")
        assert result == "prefix-world-suffix"

    def test_substitute_multiple_vars(self, monkeypatch):
        """Test multiple variable substitution."""
        monkeypatch.setenv("VAR1", "one")
        monkeypatch.setenv("VAR2", "two")
        result = _substitute_env_vars("${VAR1} and ${VAR2}")
        assert result == "one and two"

    def test_missing_env_var_raises(self):
        """Test that missing environment variable raises ValueError."""
        # Ensure the variable is not set
        os.environ.pop("NONEXISTENT_VAR", None)
        with pytest.raises(ValueError, match="Environment variable 'NONEXISTENT_VAR' is not set"):
            _substitute_env_vars("${NONEXISTENT_VAR}")

    def test_no_substitution_needed(self):
        """Test string without variables."""
        result = _substitute_env_vars("plain string")
        assert result == "plain string"


class TestLoadConfig:
    """Tests for loading configuration."""

    def test_load_valid_config(self, monkeypatch, tmp_path):
        """Test loading a valid configuration file."""
        # Prevent .env's GITHUB_REPO from leaking in via load_dotenv()
        monkeypatch.setenv("GITHUB_REPO", "")
        monkeypatch.setenv("TEST_APP_GITHUB_TOKEN", "gh_token")
        monkeypatch.setenv("TEST_AZURE_KEY", "azure_key")

        config_content = """
github:
  repo: "org/repo"
  token: "${TEST_APP_GITHUB_TOKEN}"

azure:
  endpoint: "https://test.openai.azure.com/"
  api_key: "${TEST_AZURE_KEY}"
  deployment: "gpt-4"

rml:
  base_uri: "https://example.org/"
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)

        config = load_config(config_file)

        assert isinstance(config, Config)
        assert config.github.repo == "org/repo"
        assert config.github.token == "gh_token"
        assert config.azure.endpoint == "https://test.openai.azure.com/"
        assert config.azure.api_key == "azure_key"
        assert config.azure.deployment == "gpt-4"
        assert config.rml.base_uri == "https://example.org/"

    def test_missing_config_file(self):
        """Test that missing config file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path/config.yaml")

    def test_missing_required_field(self, tmp_path):
        """Test that missing required field raises ValueError."""
        config_content = """
github:
  repo: "org/repo"
  # token is missing

azure:
  endpoint: "https://test.openai.azure.com/"
  api_key: "key"
  deployment: "gpt-4"
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)

        with pytest.raises(ValueError, match="github.token is required"):
            load_config(config_file)

    def test_empty_config_file(self, tmp_path):
        """Test that empty config file raises ValueError."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("")

        with pytest.raises(ValueError, match="Configuration file is empty"):
            load_config(config_file)

    def test_optional_sparql_endpoint(self, monkeypatch, tmp_path):
        """Test that SPARQL endpoint is optional."""
        monkeypatch.setenv("TEST_TOKEN", "token")
        monkeypatch.setenv("TEST_KEY", "key")

        config_content = """
github:
  repo: "org/repo"
  token: "${TEST_TOKEN}"

azure:
  endpoint: "https://test.openai.azure.com/"
  api_key: "${TEST_KEY}"
  deployment: "gpt-4"

# sparql section omitted - should be optional
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)

        config = load_config(config_file)

        assert config.sparql.endpoint is None
